from app.adapter.platforms import PlatformConfig
from app.reviewer.service import ReviewerService, register_reviewer_handlers
from app.storage.models import Article, ArticleStatus, PlatformCopy, Summary, SummaryStatus, Verdict


def _platforms():
    return {"weibo": PlatformConfig(id="weibo", name="微博", min_chars=1, max_chars=140, min_tags=1, max_tags=3)}


async def _seed(session_factory):
    session = session_factory()
    art = Article(url="https://x/rv1", title="t", text="正文", content_hash="c", simhash_value=1, status=ArticleStatus.ADAPTED)
    session.add(art)
    session.flush()
    summary = Summary(
        article_id=art.id,
        summary_text="摘要内容" * 20,
        key_points=["要点一", "要点二"],
        short_title="标题",
        scores={},
        status=SummaryStatus.SUMMARIZED,
    )
    session.add(summary)
    session.flush()
    copy = PlatformCopy(summary_id=summary.id, platform="weibo", text="今日热点：#科技# 核心信息。", status="adapted")
    session.add(copy)
    session.commit()
    return session, art, copy


async def test_review_copy_writes_review_and_advances_article(session_factory, redis, settings):
    session, art, copy = await _seed(session_factory)
    service = ReviewerService(settings, redis, platforms=_platforms())
    review = await service.review_copy(session, copy.id)
    assert review.verdict == Verdict.PENDING
    session.refresh(copy)
    assert copy.status == "reviewed"
    session.refresh(art)
    assert art.status == ArticleStatus.REVIEWED
    assert await redis.xlen(settings.event_stream) == 1
    session.close()


async def test_register_reviewer_handlers(session_factory, redis, settings):
    from app.orchestrator.registry import SkillRegistry

    registry = SkillRegistry()
    register_reviewer_handlers(registry, settings, redis)
    assert registry.has("copy.adapted")
