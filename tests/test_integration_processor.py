from sqlalchemy import select

from app.collector.service import build_registry
from app.processor.service import register_processor_handlers
from app.storage.models import Article, ArticleStatus, Summary, SummaryStatus
from app.storage.queue import emit_event
from app.worker import run_once


class FakeProvider:
    async def chat(self, messages, temperature=0.7):
        return '{"summary": "' + "这是集成测试生成的摘要内容。" * 16 + '", "key_points": ["要点一", "要点二", "要点三"], "short_title": "集成标题"}'


async def test_article_crawled_to_summarized_end_to_end(settings, session_factory, redis):
    session = session_factory()
    art = Article(
        url="https://x/e2e",
        title="集成文章",
        text="张三团队在北京市发布人工智能研究成果，2026年8月18日举行发布会，参会人数500人。" * 10,
        content_hash="e2e",
        simhash_value=7,
        status=ArticleStatus.CRAWLED,
    )
    session.add(art)
    session.commit()
    registry = build_registry(settings, redis)
    register_processor_handlers(registry, settings, redis, provider=FakeProvider())
    await emit_event(redis, session, "article.crawled", {"article_id": art.id}, settings.event_stream)
    assert await run_once(registry, settings, redis, session_factory) is True
    summary = session.scalar(select(Summary).where(Summary.article_id == art.id))
    assert summary is not None
    assert summary.status == SummaryStatus.SUMMARIZED
    assert len(summary.key_points) == 3
    assert summary.scores["length_ok"] is True
    session.refresh(art)
    assert art.status == ArticleStatus.SUMMARIZED
    session.close()
