import pytest

from app.cli import run_crawl_command
from app.collector.sources import SourceConfig
from app.collector.service import upsert_sources


async def test_run_crawl_sync_unknown_source_raises(settings, session_factory, redis):
    class Args:
        source_id = "missing"
        url = None
        sync = True

    with pytest.raises(ValueError):
        await run_crawl_command(Args(), settings, session_factory, redis)


async def test_run_crawl_queue_path_emits_event(settings, session_factory, redis):
    session = session_factory()
    upsert_sources(session, [SourceConfig(id="s1", name="S", type="rss", url="https://x/feed")])
    session.close()

    class Args:
        source_id = "s1"
        url = None
        sync = False

    ids = await run_crawl_command(Args(), settings, session_factory, redis)
    assert ids == []  # 入队模式立即返回
    assert await redis.xlen(settings.event_stream) == 1
