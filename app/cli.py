import argparse
import asyncio

from redis.asyncio import Redis

from app.collector.service import CollectorService, upsert_sources
from app.collector.sources import load_sources
from app.config import Settings, get_settings
from app.events import EVENT_CRAWL_REQUESTED
from app.storage.db import build_session_factory
from app.storage.queue import emit_event


async def run_crawl_command(args, settings: Settings, session_factory, redis: Redis) -> list[int]:
    service = CollectorService(settings, redis)
    with session_factory() as session:
        upsert_sources(session, load_sources(settings.sources_file))
        if not args.sync:
            payload = {"source_id": args.source_id} if args.source_id else {"url": args.url}
            if not any(payload.values()):
                raise ValueError("crawl requires --source-id or --url")
            await emit_event(redis, session, EVENT_CRAWL_REQUESTED, payload, settings.event_stream)
            return []
        if args.url:
            return await service.crawl_url(session, args.url)
        return await service.crawl_by_id(session, args.source_id)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="content-assistant")
    sub = parser.add_subparsers(dest="command", required=True)
    crawl = sub.add_parser("crawl", help="触发采集")
    crawl.add_argument("--source-id", help="数据源 id（来自 sources.yaml）")
    crawl.add_argument("--url", help="手动提交的 URL")
    crawl.add_argument("--sync", action="store_true", help="同步执行，不走事件队列")
    args = parser.parse_args(argv)
    settings = get_settings()
    session_factory = build_session_factory(settings.database_url)
    redis = Redis.from_url(settings.redis_url)

    async def run() -> None:
        # 与命令执行处于同一事件循环，避免跨循环关闭连接报错
        try:
            if args.command == "crawl":
                ids = await run_crawl_command(args, settings, session_factory, redis)
                print("new_article_ids:", ids)
        finally:
            await redis.aclose()

    asyncio.run(run())


if __name__ == "__main__":
    main()
