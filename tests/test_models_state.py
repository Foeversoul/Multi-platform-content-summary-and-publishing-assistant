import pytest
from sqlalchemy import select

from app.orchestrator.state import InvalidTransitionError, transition
from app.storage.models import Article, ArticleStatus, EventLog, EventStatus, Source, utcnow


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
