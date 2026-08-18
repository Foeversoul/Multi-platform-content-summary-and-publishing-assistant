from sqlalchemy import select

from app.adapter.platforms import PlatformConfig
from app.adapter.service import AdapterService, register_adapter_handlers
from app.storage.models import Article, ArticleStatus, PlatformCopy, Summary, SummaryStatus


class FakeProvider:
    async def chat(self, messages, temperature=0.7):
        return '{"text": "今日分享：#科技# 研究成果发布，非常实用。"}'


def _platforms():
    return {"weibo": PlatformConfig(id="weibo", name="微博", min_chars=1, max_chars=140, min_tags=1, max_tags=3)}


async def test_adapt_summary_creates_copies_and_advances_article(session_factory, redis, settings):
    session = session_factory()
    art = Article(url="https://x/ad1", title="t", text="正文", content_hash="c", simhash_value=1, status=ArticleStatus.SUMMARIZED)
    session.add(art)
    session.flush()
    summary = Summary(
        article_id=art.id,
        summary_text="张三团队发布研究成果。",
        key_points=["要点一", "要点二"],
        short_title="成果",
        scores={},
        status=SummaryStatus.SUMMARIZED,
    )
    session.add(summary)
    session.commit()
    service = AdapterService(settings, redis, provider=FakeProvider(), platforms=_platforms())
    copy_ids = await service.adapt_summary(session, summary.id)
    assert len(copy_ids) == 1
    copy = session.get(PlatformCopy, copy_ids[0])
    assert copy.platform == "weibo"
    assert copy.status == "adapted"
    session.refresh(art)
    assert art.status == ArticleStatus.ADAPTED
    assert await redis.xlen(settings.event_stream) == 1
    # 幂等：再次调用不重复创建
    assert await service.adapt_summary(session, summary.id) == []
    assert len(session.scalars(select(PlatformCopy)).all()) == 1
    session.close()


async def test_register_adapter_handlers(session_factory, redis, settings):
    from app.orchestrator.registry import SkillRegistry

    registry = SkillRegistry()
    register_adapter_handlers(registry, settings, redis, provider=FakeProvider())
    assert registry.has("summary.generated")
