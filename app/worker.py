import asyncio
import os

from redis.asyncio import Redis

from app.adapter.service import register_adapter_handlers
from app.collector.service import build_registry, upsert_sources
from app.collector.sources import load_sources
from app.config import get_settings
from app.processor.service import register_processor_handlers
from app.reviewer.service import register_reviewer_handlers
from app.storage.db import build_session_factory
from app.storage.queue import receive_one


async def run_once(registry, settings, redis: Redis, session_factory) -> bool:
    with session_factory() as session:
        return await receive_one(
            redis,
            session,
            settings.event_group,
            f"worker-{os.getpid()}",
            registry.dispatch,
            settings.event_stream,
        )


async def main() -> None:
    settings = get_settings()
    session_factory = build_session_factory(settings.database_url)
    with session_factory() as session:
        upsert_sources(session, load_sources(settings.sources_file))
    redis = Redis.from_url(settings.redis_url)
    registry = build_registry(settings, redis)
    register_processor_handlers(registry, settings, redis)
    register_adapter_handlers(registry, settings, redis)
    register_reviewer_handlers(registry, settings, redis)
    while True:
        processed = await run_once(registry, settings, redis, session_factory)
        if not processed:
            await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
