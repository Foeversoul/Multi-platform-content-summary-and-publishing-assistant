import json

from app.storage.models import EventLog, EventStatus
from app.storage.queue import emit_event, receive_one


async def test_emit_writes_log_and_stream(redis, session_factory):
    session = session_factory()
    event_id = await emit_event(redis, session, "crawl.requested", {"source_id": "demo"}, "s:events")
    log = session.get(EventLog, event_id)
    assert log is not None
    assert log.status == EventStatus.QUEUED
    entries = await redis.xrange("s:events")
    assert len(entries) == 1
    session.close()


async def test_receive_dispatch_and_idempotency(redis, session_factory):
    session = session_factory()
    seen = []

    async def dispatch(event_type, payload, session):
        seen.append((event_type, payload["source_id"]))
        return "ok"

    event_id = await emit_event(redis, session, "crawl.requested", {"source_id": "demo"}, "s:events")
    ok1 = await receive_one(redis, session, "g1", "c1", dispatch, "s:events")
    assert ok1 is True
    assert seen == [("crawl.requested", "demo")]
    log = session.get(EventLog, event_id)
    assert log.status == EventStatus.PROCESSED
    # 同一事件再次投递到流中也不会重复处理
    await redis.xadd("s:events", {"event_id": event_id, "event_type": "crawl.requested", "payload": json.dumps({"source_id": "demo"})})
    ok2 = await receive_one(redis, session, "g1", "c1", dispatch, "s:events")
    assert ok2 is True
    assert len(seen) == 1
    session.close()


async def test_receive_no_message(redis, session_factory):
    session = session_factory()

    async def dispatch(event_type, payload, session):
        raise AssertionError("should not dispatch")

    assert await receive_one(redis, session, "g1", "c1", dispatch, "s:empty") is False
    session.close()


async def test_receive_dispatch_exception_marks_dead(redis, session_factory):
    session = session_factory()

    async def bad_dispatch(event_type, payload, session):
        raise RuntimeError("boom")

    event_id = await emit_event(redis, session, "evt", {"x": 1}, "s:events")
    try:
        await receive_one(redis, session, "g1", "c1", bad_dispatch, "s:events")
    except RuntimeError:
        pass
    else:
        raise AssertionError("should re-raise")
    log = session.get(EventLog, event_id)
    assert log.status == EventStatus.DEAD
    session.close()
