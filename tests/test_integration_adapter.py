from sqlalchemy import select

from app.adapter.service import register_adapter_handlers
from app.collector.service import build_registry
from app.reviewer.service import register_reviewer_handlers
from app.storage.models import Article, ArticleStatus, PlatformCopy, Review, Summary, SummaryStatus
from app.storage.queue import emit_event
from app.worker import run_once


class FakeProvider:
    async def chat(self, messages, temperature=0.7):
        return '{"text": "今天分享：#科技# #AI# 研究成果发布，内容实用，值得关注。"}'


async def test_summary_to_reviewed_end_to_end(settings, session_factory, redis, tmp_path):
    platforms_file = tmp_path / "platforms.yaml"
    platforms_file.write_text(
        """
platforms:
  weibo:
    name: 微博
    min_chars: 1
    max_chars: 140
    min_tags: 1
    max_tags: 3
    style_prompt: 微博风格
""",
        encoding="utf-8",
    )
    settings.platforms_file = platforms_file
    session = session_factory()
    art = Article(url="https://x/e2e3", title="t", text="正文", content_hash="c3", simhash_value=3, status=ArticleStatus.SUMMARIZED)
    session.add(art)
    session.flush()
    summary = Summary(
        article_id=art.id,
        summary_text="张三团队发布研究成果，市场反响积极。" * 6,
        key_points=["要点一", "要点二"],
        short_title="成果",
        scores={},
        status=SummaryStatus.SUMMARIZED,
    )
    session.add(summary)
    session.commit()
    registry = build_registry(settings, redis)
    register_adapter_handlers(registry, settings, redis, provider=FakeProvider())
    register_reviewer_handlers(registry, settings, redis)
    await emit_event(redis, session, "summary.generated", {"summary_id": summary.id}, settings.event_stream)
    assert await run_once(registry, settings, redis, session_factory) is True  # 适配
    copies = session.scalars(select(PlatformCopy)).all()
    assert len(copies) == 1
    assert await run_once(registry, settings, redis, session_factory) is True  # 审核该 copy
    review = session.scalar(select(Review))
    assert review is not None
    assert review.scores["style_score"] >= 0
    session.refresh(art)
    assert art.status == ArticleStatus.REVIEWED
    session.close()
