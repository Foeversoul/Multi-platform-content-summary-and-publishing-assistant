import json
import logging
import uuid
from datetime import datetime, timezone

from redis.asyncio import Redis
from redis.exceptions import ResponseError
from sqlalchemy.orm import Session

from app.storage.models import EventLog, EventStatus

logger = logging.getLogger(__name__)


async def emit_event(
    redis: Redis,
    session: Session,
    event_type: str,
    payload: dict,
    stream: str,
) -> str:
    event_id = uuid.uuid4().hex
    session.add(
        EventLog(
            id=event_id,
            event_type=event_type,
            payload=json.dumps(payload, ensure_ascii=False),
            status=EventStatus.QUEUED,
        )
    )
    session.commit()
    await redis.xadd(
        stream,
        {
            "event_id": event_id,
            "event_type": event_type,
            "payload": json.dumps(payload, ensure_ascii=False),
        },
    )
    return event_id


async def receive_one(
    redis: Redis,
    session: Session,
    group: str,
    consumer: str,
    dispatch,
    stream: str,
) -> bool:
    try:
        await redis.xgroup_create(stream, group, id="0", mkstream=True)
    except ResponseError:
        pass  # 消费组已存在
    result = await redis.xreadgroup(group, consumer, {stream: ">"}, count=1)
    if not result:
        return False
    _, entries = result[0]
    msg_id, fields = entries[0]
    event_id = fields[b"event_id"].decode()
    log = session.get(EventLog, event_id)
    if log is None or log.status in (EventStatus.PROCESSED, EventStatus.DEAD):
        await redis.xack(stream, group, msg_id)
        session.commit()
        return True
    try:
        outcome = await dispatch(log.event_type, json.loads(log.payload), session)
    except Exception:
        session.rollback()
        log.status = EventStatus.DEAD
        log.processed_at = datetime.now(timezone.utc)
        session.commit()
        await redis.xack(stream, group, msg_id)
        raise
    log.status = EventStatus.PROCESSED if outcome in ("ok", "noop") else EventStatus.DEAD
    log.processed_at = datetime.now(timezone.utc)
    session.commit()
    await redis.xack(stream, group, msg_id)
    return True
