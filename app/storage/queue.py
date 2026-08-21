"""Redis Streams 事件总线：投递（含重试与补偿）、消费（批量 + PEL 崩溃恢复 + 保活）。"""

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime

from redis.asyncio import Redis
from redis.exceptions import ResponseError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.storage.models import EventLog, EventStatus

logger = logging.getLogger(__name__)

# 业务 dispatch 成功态（返回其他值视为处理失败，标记 DEAD）
_OK_OUTCOMES = ("ok", "noop")

# 投递重试参数
_DELIVERY_RETRIES = 3
_DELIVERY_RETRY_BASE = 0.2


class EventDeliveryError(RuntimeError):
    """事件投递失败（Redis 不可用等）。事件保留 QUEUED，交由补偿机制重投，不标记 DEAD。"""


def _payload_json(event_type: str, payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


async def emit_event(
    redis: Redis,
    session: Session,
    event_type: str,
    payload: dict,
    stream: str,
    retries: int = _DELIVERY_RETRIES,
    retry_base: float = _DELIVERY_RETRY_BASE,
) -> str:
    """写入 EventLog（QUEUED）并投递到事件流；xadd 失败指数退避重试后仍失败抛 EventDeliveryError。"""
    event_id = uuid.uuid4().hex
    payload_text = _payload_json(event_type, payload)
    session.add(
        EventLog(
            id=event_id,
            event_type=event_type,
            payload=payload_text,
            status=EventStatus.QUEUED,
        )
    )
    session.commit()
    fields = {"event_id": event_id, "event_type": event_type, "payload": payload_text}
    for attempt in range(retries):
        try:
            await redis.xadd(stream, fields)
            return event_id
        except Exception as exc:
            if attempt == retries - 1:
                raise EventDeliveryError(
                    f"xadd failed after {retries} attempts: {exc!r}"
                ) from exc
            await asyncio.sleep(retry_base * (2**attempt))
    return event_id


async def _ensure_group(redis: Redis, stream: str, group: str) -> None:
    # 先检查消费组是否已存在，避免 xgroup_create 触发 BUSYGROUP
    # （fakeredis 在 error reply 后会关闭连接，导致后续命令全部失败）
    try:
        groups = await redis.xinfo_groups(stream)
    except ResponseError:
        groups = []
    group_bytes = group.encode() if isinstance(group, str) else group
    for g in groups:
        name = g.get(b"name") or g.get("name")
        if name in (group, group_bytes):
            return
    try:
        await redis.xgroup_create(stream, group, id="0", mkstream=True)
    except ResponseError:
        pass  # 消费组已存在（并发创建竞态）


def _short_error(exc: Exception, limit: int = 500) -> str:
    """S4：错误信息收敛——仅保留异常类名与首行摘要，完整堆栈由日志承载。"""
    text = " ".join(str(exc).strip().split())[:limit]
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def _mark_dead(session: Session, event_id: str, exc: Exception) -> None:
    log = session.get(EventLog, event_id)
    if log is None:
        return
    session.rollback()
    log.status = EventStatus.DEAD
    log.error = _short_error(exc)
    log.processed_at = datetime.now(UTC)
    session.commit()


async def _best_effort_ack(redis: Redis, stream: str, group: str, msg_id: str) -> None:
    """尽力确认已处理消息；ack 失败不阻断流程，交由 PEL 恢复机制兜底。"""
    try:
        await redis.xack(stream, group, msg_id)
    except Exception:  # noqa: BLE001
        logger.debug("xack failed, message will be reclaimed: %s/%s", stream, msg_id)


async def _consume_one(
    redis: Redis,
    session: Session,
    stream: str,
    group: str,
    msg_id: str,
    fields: dict,
    dispatch,
) -> bool:
    """处理单条消息。返回 False 表示幂等跳过；业务异常向上抛由调用方标记 DEAD。"""
    event_id = fields[b"event_id"].decode()
    log = session.get(EventLog, event_id)
    if log is None or log.status in (
        EventStatus.PROCESSED,
        EventStatus.DEAD,
        EventStatus.DISCARDED,
    ):
        await redis.xack(stream, group, msg_id)
        return False
    outcome = await dispatch(log.event_type, json.loads(log.payload), session)
    log.status = EventStatus.PROCESSED if outcome in _OK_OUTCOMES else EventStatus.DEAD
    log.processed_at = datetime.now(UTC)
    session.commit()
    await redis.xack(stream, group, msg_id)
    return True


async def _read_group(
    redis: Redis,
    group: str,
    consumer: str,
    stream: str,
    read_id: str,
    count: int,
    block_ms: int | None = None,
) -> list[tuple[str, dict]]:
    kwargs = {}
    if block_ms is not None:
        kwargs["block"] = block_ms
    result = await redis.xreadgroup(group, consumer, {stream: read_id}, count=count, **kwargs)
    if not result:
        return []
    # Normalize across RESP2/RESP3 and fakeredis wire quirks:
    # RESP2 list: [(stream, entries)]
    # RESP3 dict: {stream: entries}
    # fakeredis RESP3 double-wraps entries in an extra list
    if isinstance(result, dict):
        entries = next(iter(result.values()))
    else:
        _, entries = result[0]
    if entries and isinstance(entries[0], list):
        entries = entries[0]
    return [(entry[0], entry[1]) for entry in entries]


async def receive_one(
    redis: Redis,
    session: Session,
    group: str,
    consumer: str,
    dispatch,
    stream: str,
) -> bool:
    """消费一条新消息。兼容旧接口：业务异常标记 DEAD 后 re-raise，投递异常保持 QUEUED 并 re-raise。"""
    await _ensure_group(redis, stream, group)
    entries = await _read_group(redis, group, consumer, stream, ">", 1)
    if not entries:
        return False
    msg_id, fields = entries[0]
    try:
        await _consume_one(redis, session, stream, group, msg_id, fields, dispatch)
    except EventDeliveryError:
        session.rollback()
        raise
    except Exception as exc:
        _mark_dead(session, fields[b"event_id"].decode(), exc)
        await _best_effort_ack(redis, stream, group, msg_id)
        raise
    return True


async def run_once(
    redis: Redis,
    session: Session,
    group: str,
    consumer: str,
    dispatch,
    stream: str,
    batch_size: int = 10,
    block_ms: int = 2000,
) -> int:
    """批量消费：优先 PEL（崩溃恢复），其次阻塞读新消息。

    业务异常逐条标记 DEAD 后继续（保活）；投递异常保持 QUEUED 并抛给上层。
    """
    await _ensure_group(redis, stream, group)
    entries = await _read_group(redis, group, consumer, stream, "0", batch_size)
    if not entries:
        entries = await _read_group(
            redis, group, consumer, stream, ">", batch_size, block_ms
        )
    if not entries:
        return 0
    processed = 0
    for msg_id, fields in entries:
        try:
            await _consume_one(redis, session, stream, group, msg_id, fields, dispatch)
        except EventDeliveryError:
            session.rollback()
            raise
        except Exception as exc:  # noqa: BLE001 业务异常标记 DEAD 后继续（保活）
            _mark_dead(session, fields[b"event_id"].decode(), exc)
            await _best_effort_ack(redis, stream, group, msg_id)
        processed += 1
    return processed


async def recover_pending_events(
    redis: Redis,
    group: str,
    consumer: str,
    stream: str,
    min_idle_ms: int = 30_000,
    count: int = 50,
) -> int:
    """将超时未 ack 的消息（失联消费者遗留的 PEL）认领给当前消费者。

    认领后由 run_once 的 PEL（read_id="0"）通道消费，保证处理中崩溃的事件可恢复。
    """
    try:
        pending = await redis.xpending_range(stream, group, min="-", max="+", count=count)
    except ResponseError:
        return 0
    if not pending:
        return 0
    message_ids = [item.get(b"message_id") or item.get("message_id") for item in pending]
    claimed = await redis.xclaim(stream, group, consumer, min_idle_ms, message_ids)
    return len(claimed)


def _age_seconds(dt: datetime | None, now: datetime) -> float:
    if dt is None:
        return 0.0
    if dt.tzinfo is None:  # SQLite 丢失时区信息时按 UTC 解释
        dt = dt.replace(tzinfo=UTC)
    return (now - dt).total_seconds()


async def redeliver_stale_events(
    redis: Redis,
    session_factory: sessionmaker,
    stream: str,
    timeout_seconds: int = 60,
    limit: int = 50,
) -> int:
    """补偿投递：将 QUEUED 且超过超时时间仍未处理的事件重新写入事件流。

    幂等：重复投递的事件在消费端按 EventLog 状态去重（已处理则 xack 跳过）。
    """
    now = datetime.now(UTC)
    with session_factory() as session:
        logs = session.scalars(
            select(EventLog)
            .where(EventLog.status == EventStatus.QUEUED)
            .order_by(EventLog.created_at)
            .limit(limit * 10)
        ).all()
        stale = [log for log in logs if _age_seconds(log.created_at, now) > timeout_seconds]
        for log in stale[:limit]:
            await redis.xadd(
                stream,
                {
                    "event_id": log.id,
                    "event_type": log.event_type,
                    "payload": log.payload,
                },
            )
        return len(stale[:limit])
