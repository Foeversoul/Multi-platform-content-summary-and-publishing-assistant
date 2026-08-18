import pytest
from sqlalchemy import select

from app.orchestrator.state import InvalidTransitionError, transition
from app.storage.models import (
    Article,
    ArticleStatus,
    CopyStatus,
    EventLog,
    EventStatus,
    PlatformCopy,
    Review,
    Source,
    Summary,
    SummaryStatus,
    Verdict,
    utcnow,
)


def test_valid_transition():
    transition(ArticleStatus.PENDING, ArticleStatus.CRAWLED)  # 不抛异常


def test_invalid_transition_raises():
    with pytest.raises(InvalidTransitionError):
        transition(ArticleStatus.PENDING, ArticleStatus.PUBLISHED)


def test_source_article_event_crud(session_factory):
    session = session_factory()
    src = Source(external_id="demo", name="示例", type="rss", url="https://x/feed")
    session.add(src)
    session.flush()
    art = Article(
        source_id=src.id,
        url="https://x/a",
        title="标题",
        text="正文",
        content_hash="abc",
        simhash_value=42,
        status=ArticleStatus.PENDING,
        created_at=utcnow(),
    )
    session.add(art)
    session.add(EventLog(id="e1", event_type="crawl.requested", payload="{}", status=EventStatus.QUEUED))
    session.commit()
    rows = session.scalars(select(Article)).all()
    assert len(rows) == 1
    assert rows[0].status == ArticleStatus.PENDING
    assert session.scalar(select(EventLog).where(EventLog.id == "e1")).status == EventStatus.QUEUED
    session.close()


def test_summary_crud(session_factory):
    session = session_factory()
    art = Article(url="https://x/s1", title="标题", text="正文", content_hash="c", simhash_value=1, status=ArticleStatus.CRAWLED)
    session.add(art)
    session.flush()
    summary = Summary(
        article_id=art.id,
        summary_text="这是摘要内容，长度符合规范。",
        key_points=["要点一", "要点二", "要点三"],
        short_title="精简标题",
        scores={"summary_len": 15},
        status=SummaryStatus.SUMMARIZED,
    )
    session.add(summary)
    session.commit()
    row = session.scalar(select(Summary).where(Summary.article_id == art.id))
    assert row.key_points == ["要点一", "要点二", "要点三"]
    assert row.scores["summary_len"] == 15
    assert row.status == SummaryStatus.SUMMARIZED
    session.close()


def test_copy_and_review_crud(session_factory):
    session = session_factory()
    art = Article(url="https://x/a1", title="t", text="正文", content_hash="c", simhash_value=1, status=ArticleStatus.SUMMARIZED)
    session.add(art)
    session.flush()
    summary = Summary(
        article_id=art.id,
        summary_text="摘要内容" * 30,
        key_points=["要点一", "要点二", "要点三"],
        short_title="标题",
        scores={},
        status=SummaryStatus.SUMMARIZED,
    )
    session.add(summary)
    session.flush()
    copy = PlatformCopy(summary_id=summary.id, platform="weibo", text="文案内容", status=CopyStatus.ADAPTED)
    session.add(copy)
    session.flush()
    review = Review(copy_id=copy.id, verdict=Verdict.PENDING, scores={"style_score": 90})
    session.add(review)
    session.commit()
    row = session.scalar(select(Review).where(Review.copy_id == copy.id))
    assert row.verdict == Verdict.PENDING
    assert row.scores["style_score"] == 90
    session.close()
