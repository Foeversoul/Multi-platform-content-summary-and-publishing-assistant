from app.orchestrator.registry import SkillRegistry
from app.storage.models import Article, ArticleStatus


async def test_dispatch_calls_handler(session_factory):
    registry = SkillRegistry()
    seen = []

    async def handler(payload, session):
        seen.append(payload["x"])

    registry.register("evt", handler)
    session = session_factory()
    outcome = await registry.dispatch("evt", {"x": 1}, session)
    assert outcome == "ok"
    assert seen == [1]
    session.close()


async def test_dispatch_no_handler_is_noop(session_factory):
    registry = SkillRegistry()
    session = session_factory()
    assert await registry.dispatch("unknown", {}, session) == "noop"
    session.close()


async def test_dispatch_marks_article_dead_after_retries(session_factory):
    registry = SkillRegistry()

    async def failing_handler(payload, session):
        raise RuntimeError("boom")

    registry.register("evt", failing_handler)
    session = session_factory()
    art = Article(url="https://x/1", title="t", text="c", content_hash="h", simhash_value=0, status=ArticleStatus.PENDING)
    session.add(art)
    session.commit()
    outcome = await registry.dispatch("evt", {"article_id": art.id}, session, retries=2, base_seconds=0)
    assert outcome == "dead"
    session.refresh(art)
    assert art.status == ArticleStatus.DEAD_LETTER
    session.close()
