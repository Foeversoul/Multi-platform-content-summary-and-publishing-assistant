from app.orchestrator.state import InvalidTransitionError
from app.processor.service import ProcessorService, register_processor_handlers
from app.storage.models import Article, ArticleStatus, SummaryStatus


class FakeProvider:
    async def chat(self, messages, temperature=0.7):
        return '{"summary": "' + "这是一段两百字左右的摘要内容。" * 16 + '", "key_points": ["要点一", "要点二", "要点三"], "short_title": "精简标题"}'


async def test_process_article_creates_summary_and_updates_state(session_factory, redis, settings):
    session = session_factory()
    art = Article(
        url="https://x/p1",
        title="研究标题",
        text="张三团队在北京市发布人工智能研究成果，2026年8月18日举行发布会，参会人数500人。" * 10,
        content_hash="h",
        simhash_value=1,
        status=ArticleStatus.CRAWLED,
    )
    session.add(art)
    session.commit()
    service = ProcessorService(settings, redis, provider=FakeProvider())
    summary = await service.process_article(session, art.id)
    assert summary.status == SummaryStatus.SUMMARIZED
    session.refresh(art)
    assert art.status == ArticleStatus.SUMMARIZED
    events = await redis.xrange(settings.event_stream)
    assert len(events) == 1
    session.close()


async def test_process_article_unknown_raises(session_factory, redis, settings):
    service = ProcessorService(settings, redis, provider=FakeProvider())
    session = session_factory()
    try:
        await service.process_article(session, 999)
    except ValueError:
        pass
    else:
        raise AssertionError("unknown article_id should raise")
    session.close()


async def test_process_article_invalid_state_raises(session_factory, redis, settings):
    session = session_factory()
    art = Article(
        url="https://x/p2",
        title="标题",
        text="正文内容足够长。" * 30,
        content_hash="h2",
        simhash_value=2,
        status=ArticleStatus.SUMMARIZED,
    )
    session.add(art)
    session.commit()
    service = ProcessorService(settings, redis, provider=FakeProvider())
    try:
        await service.process_article(session, art.id)
    except InvalidTransitionError:
        pass
    else:
        raise AssertionError("invalid transition should raise")
    session.close()


async def test_register_processor_handlers(session_factory, redis, settings):
    from app.orchestrator.registry import SkillRegistry

    registry = SkillRegistry()
    register_processor_handlers(registry, settings, redis, provider=FakeProvider())
    assert registry.has("article.crawled")
    outcome = await registry.dispatch("article.crawled", {"article_id": 999}, session_factory(), retries=0)
    assert outcome == "dead"
