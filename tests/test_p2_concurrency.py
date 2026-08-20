"""P2 并发与压力测试（T8）：高并发投递、多消费者无重复分配、坏事件风暴保活。"""

import asyncio

from app.storage.models import EventLog, EventStatus
from app.storage.queue import emit_event, run_once


async def _emit(redis, session_factory, event_type="evt", payload=None, stream="s:events") -> str:
    with session_factory() as session:
        return await emit_event(redis, session, event_type, payload or {"x": 1}, stream)


async def _count_status(session_factory, status: EventStatus) -> int:
    with session_factory() as session:
        return sum(1 for log in session.query(EventLog).all() if log.status == status)


async def test_concurrent_emit_all_queued(redis, session_factory):
    """高并发投递：30 条事件并发写入，EventLog 全部 QUEUED，事件流条目数一致。"""
    n = 30
    ids = await asyncio.gather(*(_emit(redis, session_factory, payload={"i": i}) for i in range(n)))
    assert len(set(ids)) == n
    assert await _count_status(session_factory, EventStatus.QUEUED) == n
    assert len(await redis.xrange("s:events")) == n


async def test_multi_consumer_split_without_overlap(redis):
    """多 worker 并行从同一消费组取消息，消息 ID 无重叠（group 分配语义）。"""
    await redis.xgroup_create("s:events", "g1", id="0", mkstream=True)
    for i in range(6):
        await redis.xadd("s:events", {"event_id": f"e{i}", "event_type": "evt", "payload": "{}"})

    async def read_once(name: str):
        return await redis.xreadgroup("g1", name, {"s:events": ">"}, count=10)

    results = await asyncio.gather(read_once("c1"), read_once("c2"))
    got = [entry[0] for r in results if r for entry in r[0][1]]
    assert len(got) == 6
    assert len(set(got)) == 6  # 无重复分配


async def test_multi_worker_consumes_all_once(redis, session_factory):
    """两个 worker（不同 consumer）顺序消费同一组：每条事件恰好处理一次，全部落库 PROCESSED。

    并发分配语义由 test_multi_consumer_split_without_overlap 以纯 Redis 覆盖；
    这里验证端到端落库，session 顺序使用（StaticPool 单连接不可并发）。
    """
    for i in range(4):
        await _emit(redis, session_factory, payload={"i": i})
    seen: list[int] = []

    async def dispatch(event_type, payload, session):
        seen.append(payload["i"])
        return "ok"

    async def consume(name: str) -> int:
        with session_factory() as session:
            return await run_once(redis, session, "g1", name, dispatch, "s:events")

    total = (await consume("c1")) + (await consume("c2"))
    assert total == 4
    assert sorted(seen) == [0, 1, 2, 3]
    assert await _count_status(session_factory, EventStatus.PROCESSED) == 4


async def test_worker_survives_bad_event_storm(redis, session_factory):
    """坏事件风暴：半数 dispatch 抛异常，worker 逐条标记 DEAD 后继续，不 re-raise。"""
    for i in range(20):
        await _emit(redis, session_factory, payload={"i": i})
    processed: list[int] = []

    async def dispatch(event_type, payload, session):
        if payload["i"] % 2 == 0:
            raise RuntimeError("boom-even")
        processed.append(payload["i"])
        return "ok"

    with session_factory() as session:
        rounds = [
            await run_once(redis, session, "g1", "c1", dispatch, "s:events"),
            await run_once(redis, session, "g1", "c1", dispatch, "s:events"),
        ]
    assert sum(rounds) == 20
    assert sorted(processed) == [i for i in range(20) if i % 2 == 1]
    assert await _count_status(session_factory, EventStatus.DEAD) == 10
    assert await _count_status(session_factory, EventStatus.PROCESSED) == 10


async def test_worker_processing_crash_is_recovered(redis, session_factory):
    """处理中崩溃（未 ack）→ 恢复认领 → 新消费者重放，不丢事件（T8 故障注入）。"""
    event_id = await _emit(redis, session_factory, payload={"i": 0})
    await redis.xgroup_create("s:events", "g1", id="0", mkstream=True)
    # c1 读取后未 ack 即崩溃（模拟进程被 SIGKILL）
    await redis.xreadgroup("g1", "c1", {"s:events": ">"}, count=1)
    pending = await redis.xpending("s:events", "g1")
    assert pending["pending"] == 1  # 事件滞留 PEL

    from app.storage.queue import recover_pending_events

    assert await recover_pending_events(redis, "g1", "c2", "s:events", min_idle_ms=0) == 1
    seen: list[dict] = []

    async def dispatch(event_type, payload, session):
        seen.append(payload)
        return "ok"

    with session_factory() as session:
        processed = await run_once(redis, session, "g1", "c2", dispatch, "s:events")
    assert processed == 1
    assert seen == [{"i": 0}]
    with session_factory() as session:
        assert session.get(EventLog, event_id).status == EventStatus.PROCESSED
