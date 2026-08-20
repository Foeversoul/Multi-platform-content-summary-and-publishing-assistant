"""Worker 可靠性测试（T1/T2）：批量消费保活、PEL 崩溃恢复、事件补偿投递。"""

import pytest

from app.storage.models import EventLog, EventStatus
from app.storage.queue import (
    EventDeliveryError,
    emit_event,
    recover_pending_events,
    redeliver_stale_events,
    run_once,
)


async def _emit(redis, session_factory, event_type="evt", payload=None, stream="s:events") -> str:
    with session_factory() as session:
        return await emit_event(redis, session, event_type, payload or {"x": 1}, stream)


async def test_run_once_survives_bad_event(redis, session_factory):
    """单条坏事件标记 DEAD 后不 re-raise，进程继续消费（保活）。"""
    event_id = await _emit(redis, session_factory)

    async def bad_dispatch(event_type, payload, session):
        raise RuntimeError("boom")

    with session_factory() as session:
        processed = await run_once(redis, session, "g1", "c1", bad_dispatch, "s:events")
    assert processed == 1
    with session_factory() as session:
        log = session.get(EventLog, event_id)
        assert log.status == EventStatus.DEAD
        assert "boom" in log.error


async def test_run_once_consumes_batch(redis, session_factory):
    """一次批量消费多条消息并全部落库 PROCESSED。"""
    ids = []
    for i in range(3):
        ids.append(await _emit(redis, session_factory, payload={"i": i}))
    seen = []

    async def dispatch(event_type, payload, session):
        seen.append(payload["i"])
        return "ok"

    with session_factory() as session:
        processed = await run_once(redis, session, "g1", "c1", dispatch, "s:events", batch_size=10)
    assert processed == 3
    assert sorted(seen) == [0, 1, 2]
    with session_factory() as session:
        assert all(session.get(EventLog, eid).status == EventStatus.PROCESSED for eid in ids)


async def test_recover_pending_claims_orphaned(redis, session_factory):
    """失联消费者遗留的 PEL 消息可被新消费者认领。"""
    await _emit(redis, session_factory)
    await redis.xgroup_create("s:events", "g1", id="0", mkstream=True)
    await redis.xreadgroup("g1", "c1", {"s:events": ">"}, count=1)  # c1 读取后模拟崩溃（未 ack）
    recovered = await recover_pending_events(redis, "g1", "c2", "s:events", min_idle_ms=0)
    assert recovered == 1


async def test_pel_recovery_full_flow(redis, session_factory):
    """崩溃恢复端到端：认领后由新消费者经 PEL 通道处理并落库。"""
    event_id = await _emit(redis, session_factory)
    await redis.xgroup_create("s:events", "g1", id="0", mkstream=True)
    await redis.xreadgroup("g1", "c1", {"s:events": ">"}, count=1)  # c1 未 ack 即崩溃
    await recover_pending_events(redis, "g1", "c2", "s:events", min_idle_ms=0)
    seen = []

    async def dispatch(event_type, payload, session):
        seen.append(event_type)
        return "ok"

    with session_factory() as session:
        processed = await run_once(redis, session, "g1", "c2", dispatch, "s:events")
    assert processed == 1
    assert seen == ["evt"]
    with session_factory() as session:
        assert session.get(EventLog, event_id).status == EventStatus.PROCESSED


async def test_redeliver_stale_events_requeues(redis, session_factory):
    """QUEUED 超时未处理的事件被补偿重投到事件流（幂等重复投递安全）。"""
    await _emit(redis, session_factory)
    count = await redeliver_stale_events(redis, session_factory, "s:events", timeout_seconds=0)
    assert count == 1
    assert len(await redis.xrange("s:events")) == 2  # 原始 + 补偿副本


async def test_redeliver_skips_processed(redis, session_factory):
    """已处理事件不被补偿重复投递。"""
    await _emit(redis, session_factory)

    async def dispatch(event_type, payload, session):
        return "ok"

    with session_factory() as session:
        await run_once(redis, session, "g1", "c1", dispatch, "s:events")
    count = await redeliver_stale_events(redis, session_factory, "s:events", timeout_seconds=0)
    assert count == 0


async def test_emit_event_retries_then_succeeds(session_factory):
    """投递瞬时失败时指数退避重试并成功。"""
    calls = {"n": 0}

    class FlakyRedis:
        async def xadd(self, *args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ConnectionError("redis down")
            return "1-0"

    with session_factory() as session:
        event_id = await emit_event(FlakyRedis(), session, "evt", {"x": 1}, "s:events", retries=3, retry_base=0)
    assert calls["n"] == 2
    assert event_id


async def test_emit_event_raises_delivery_error(session_factory):
    """重试后仍失败抛 EventDeliveryError，且 EventLog 保持 QUEUED 供补偿。"""
    calls = {"n": 0}

    class BrokenRedis:
        async def xadd(self, *args, **kwargs):
            calls["n"] += 1
            raise ConnectionError("redis down")

    with session_factory() as session:
        with pytest.raises(EventDeliveryError):
            await emit_event(BrokenRedis(), session, "evt", {"x": 1}, "s:events", retries=3, retry_base=0)
        assert calls["n"] == 3
        # 事件保持 QUEUED（两阶段一致性的补偿依据）
        logs = [log for log in session.query(EventLog).all() if log.status == EventStatus.QUEUED]
        assert len(logs) == 1


async def test_run_once_delivery_error_keeps_queued(redis, session_factory):
    """dispatch 内投递失败抛 EventDeliveryError 时，事件不被标记 DEAD。"""
    event_id = await _emit(redis, session_factory)

    async def dispatch(event_type, payload, session):
        raise EventDeliveryError("redis down")

    with session_factory() as session, pytest.raises(EventDeliveryError):
        await run_once(redis, session, "g1", "c1", dispatch, "s:events")
    with session_factory() as session:
        assert session.get(EventLog, event_id).status == EventStatus.QUEUED
