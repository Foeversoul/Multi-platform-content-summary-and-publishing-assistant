from pathlib import Path

from sqlalchemy import select

from app.collector.service import CollectorService, build_registry, upsert_sources
from app.collector.sources import load_sources
from app.storage.models import Article, ArticleStatus
from app.storage.queue import emit_event
from app.worker import run_once


def _seed_sources(tmp_path: Path) -> Path:
    sources_file = tmp_path / "sources.yaml"
    sources_file.write_text(
        f"""
sources:
  - id: local-rss
    name: 本地RSS
    type: rss
    url: {tmp_path / "feed.xml"}
    frequency_minutes: 60
""",
        encoding="utf-8",
    )
    return sources_file


async def test_rss_crawl_end_to_end(settings, session_factory, redis, tmp_path: Path):
    feed = tmp_path / "feed.xml"
    feed.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>频道</title>
  <item><title>集成文章一</title><link>https://example.com/i1</link><description>第一篇文章的正文摘要</description></item>
  <item><title>集成文章二</title><link>https://example.com/i2</link><description>第二篇文章的正文摘要</description></item>
</channel></rss>""",
        encoding="utf-8",
    )
    settings.sources_file = _seed_sources(tmp_path)
    session = session_factory()
    upsert_sources(session, load_sources(settings.sources_file))
    registry = build_registry(settings, redis)
    await emit_event(redis, session, "crawl.requested", {"source_id": "local-rss"}, settings.event_stream)
    assert await run_once(registry, settings, redis, session_factory) is True
    articles = session.scalars(select(Article)).all()
    assert len(articles) == 2
    assert all(a.status == ArticleStatus.CRAWLED for a in articles)
    assert all(a.content_hash for a in articles)
    assert all(a.raw_path for a in articles)
    assert all(a.source_id is not None for a in articles)
    # 重复采集：再跑一次同一事件，去重后不新增
    assert await run_once(registry, settings, redis, session_factory) is True  # 幂等：事件已处理，直接 ack
    assert len(session.scalars(select(Article)).all()) == 2
    # 模拟新一轮采集：直接调 service，确认去重生效
    service = CollectorService(settings, redis)
    ids = await service.crawl_by_id(session, "local-rss")
    assert ids == []
    session.close()


async def test_manual_url_crawl_end_to_end(settings, session_factory, redis):
    from app.collector.base import Candidate

    class FakeSpider:
        source_type = "web"

        async def fetch(self, source):
            return [Candidate(url="https://example.com/manual", title="手动文章", text="手动提交文章的正文内容。")]

    service = CollectorService(settings, redis, spiders={"web": FakeSpider()})
    session = session_factory()
    ids = await service.crawl_url(session, "https://example.com/manual")
    assert len(ids) == 1
    art = session.get(Article, ids[0])
    assert art.title == "手动文章"
    session.close()
