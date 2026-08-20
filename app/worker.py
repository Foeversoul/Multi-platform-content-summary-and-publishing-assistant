"""Worker 进程：批量消费事件 + 定时调度采集 + 崩溃恢复 + 补偿投递 + 优雅停机。"""

import asyncio
import logging
import os
import signal

from redis.asyncio import Redis

from app.adapter.service import register_adapter_handlers
from app.collector.service import build_registry, upsert_sources
from app.collector.sources import load_sources
from app.config import get_settings
from app.log import setup_logging
from app.orchestrator.scheduler import start_scheduler
from app.processor.service import register_processor_handlers
from app.reviewer.service import register_reviewer_handlers
from app.storage.db import build_session_factory
from app.storage.queue import (
    recover_pending_events,
    redeliver_stale_events,
)
from app.storage.queue import (
    run_once as consume_batch,
)

logger = logging.getLogger(__name__)

# 维护周期（秒）
RECOVER_EVERY_SECONDS = 60  # PEL 崩溃恢复认领周期
REDELIVER_EVERY_SECONDS = 300  # 补偿投递周期


async def run_once(registry, settings, redis: Redis, session_factory) -> bool:
    """兼容旧接口：消费一批事件，处理了任意事件返回 True，否则返回 False。

    仅作为集成测试与脚本调用的便捷入口；worker 主循环使用 run_once_loop。
    """
    with session_factory() as session:
        processed = await consume_batch(
            redis,
            session,
            settings.event_group,
            "cli",
            registry.dispatch,
            settings.event_stream,
        )
    return processed > 0


async def run_once_loop(
    registry,
    settings,
    redis: Redis,
    session_factory,
    consumer: str,
) -> int:
    """批量消费一批事件，返回处理条数（业务异常已逐条标记 DEAD，不中断进程）。"""
    with session_factory() as session:
        return await consume_batch(
            redis,
            session,
            settings.event_group,
            consumer,
            registry.dispatch,
            settings.event_stream,
        )


def _install_stop_handler(stop: asyncio.Event) -> None:
    """注册 SIGINT/SIGTERM 优雅停机信号（Windows 仅支持 SIGINT/SIGBREAK）。"""
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, ValueError, RuntimeError):
            logger.debug("signal handler not supported: %s", sig)


async def main() -> None:
    settings = get_settings()
    setup_logging(settings.data_dir / "logs")
    session_factory = build_session_factory(settings.database_url)
    with session_factory() as session:
        upsert_sources(session, load_sources(settings.sources_file))
    redis = Redis.from_url(settings.redis_url)
    registry = build_registry(settings, redis)
    register_processor_handlers(registry, settings, redis)
    register_adapter_handlers(registry, settings, redis)
    register_reviewer_handlers(registry, settings, redis)

    consumer = f"worker-{os.getpid()}"
    # 定时调度采集（PRD 按数据源频率自动采集）
    scheduler = start_scheduler(settings, redis, session_factory)
    scheduler.start()

    stop = asyncio.Event()
    _install_stop_handler(stop)
    loop = asyncio.get_running_loop()
    last_recover = loop.time()
    last_redeliver = loop.time()

    while not stop.is_set():
        processed = 0
        try:
            # 定期认领失联消费者的 PEL 消息（崩溃恢复）
            if loop.time() - last_recover >= RECOVER_EVERY_SECONDS:
                await recover_pending_events(
                    redis, settings.event_group, consumer, settings.event_stream
                )
                last_recover = loop.time()
            # 定期补偿投递 QUEUED 超时未处理的事件（两阶段一致性兜底）
            if loop.time() - last_redeliver >= REDELIVER_EVERY_SECONDS:
                await redeliver_stale_events(redis, session_factory, settings.event_stream)
                last_redeliver = loop.time()
            processed = await run_once_loop(registry, settings, redis, session_factory, consumer)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("worker loop error, continue")
        if not processed:
            await asyncio.sleep(1)

    scheduler.shutdown(wait=False)
    await redis.aclose()
    logger.info("worker stopped gracefully")


if __name__ == "__main__":
    asyncio.run(main())
