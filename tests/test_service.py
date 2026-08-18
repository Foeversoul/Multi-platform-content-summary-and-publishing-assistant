from sqlalchemy import select

from app.collector.service import CollectorService, build_registry, upsert_sources
from app.collector.sources import SourceConfig
from app.storage.models import Article, ArticleStatus, Source


def test_upsert_sources_creates_and_updates(session_factory):
    session = session_factory()
    upsert_sources(session, [SourceConfig(id="s1", name="旧名", type="rss", url="https://x/feed")])
    upsert_sources(session, [SourceConfig(id="s1", name="新名", type="rss", url="https://x/feed2")])
    rows = session.scalars(select(Source)).all()
    assert len(rows) == 1  # 同一 external_id 只保留一行
    src = rows[0]
    assert src.name == "新名"
    assert src.url == "https://x/feed2"
    session.close()


async def test_crawl_source_stores_article_and_emits(session_factory, redis, settings):
    from app.collector.base import Candidate

    class FakeSpider:
        source_type = "web"

        async def fetch(self, source):
            return [Candidate(url="https://x/a", title="文章A", text="这是第一篇文章的正文内容。")]

    class FakeDedup:
        def is_duplicate(self, session, url, content_hash, simhash_value):
            return False

    service = CollectorService(settings, redis, spiders={"web": FakeSpider()}, dedup=FakeDedup())
    session = session_factory()
    source = SourceConfig(id="w1", name="网页", type="web", url="https://x/a")
    ids = await service.crawl_source(session, source)
    assert len(ids) == 1
    art = session.get(Article, ids[0])
    assert art.status == ArticleStatus.CRAWLED
    assert art.content_hash
    raw_file = settings.data_dir / "raw" / f"{art.id}.txt"
    assert raw_file.exists()
    assert raw_file.read_text(encoding="utf-8") == "这是第一篇文章的正文内容。"
    events = await redis.xrange(settings.event_stream)
    assert len(events) == 1
    session.close()


async def test_crawl_by_id_unknown_raises(session_factory, redis, settings):
    service = CollectorService(settings, redis)
    session = session_factory()
    try:
        await service.crawl_by_id(session, "missing")
    except ValueError:
        pass
    else:
        raise AssertionError("unknown source_id should raise")
    session.close()


async def test_build_registry_handles_crawl_requested(session_factory, redis, settings):
    registry = build_registry(settings, redis)
    assert registry.has("crawl.requested")
    session = session_factory()
    outcome = await registry.dispatch("crawl.requested", {"source_id": "missing"}, session, retries=0)
    assert outcome == "dead"
    session.close()


async def test_crawl_source_unknown_type_raises(session_factory, redis, settings):
    from app.collector.base import Candidate

    class FakeSpider:
        source_type = "rss"

        async def fetch(self, source):
            return [Candidate(url="https://x/r", title="R", text="rss 文本")]

    service = CollectorService(settings, redis, spiders={"rss": FakeSpider()})
    session = session_factory()
    source = SourceConfig(id="w1", name="网页", type="web", url="https://x/a")
    try:
        await service.crawl_source(session, source)
    except ValueError:
        pass
    else:
        raise AssertionError("unknown source type should raise")
    session.close()


async def test_crawl_by_id_disabled_source_returns_empty(session_factory, redis, settings):
    session = session_factory()
    upsert_sources(session, [SourceConfig(id="off", name="关", type="rss", url="https://x/feed", enabled=False)])
    service = CollectorService(settings, redis)
    assert await service.crawl_by_id(session, "off") == []
    session.close()


async def test_crawl_source_skips_duplicates(session_factory, redis, settings):
    from app.collector.base import Candidate

    class FakeSpider:
        source_type = "web"

        async def fetch(self, source):
            return [Candidate(url="https://x/dup", title="重复", text="重复正文内容。")]

    class AlwaysDuplicate:
        def is_duplicate(self, session, url, content_hash, simhash_value):
            return True

    service = CollectorService(settings, redis, spiders={"web": FakeSpider()}, dedup=AlwaysDuplicate())
    session = session_factory()
    source = SourceConfig(id="w1", name="网页", type="web", url="https://x/dup")
    assert await service.crawl_source(session, source) == []
    assert session.scalars(select(Article)).all() == []
    assert await redis.xlen(settings.event_stream) == 0
    session.close()
