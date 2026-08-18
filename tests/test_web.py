from fastapi.testclient import TestClient
from sqlalchemy import select

from app.storage.models import (
    Article,
    ArticleStatus,
    CopyStatus,
    PlatformCopy,
    Publish,
    PublishStatus,
    Review,
    Summary,
    SummaryStatus,
    Verdict,
)
from app.web.main import create_app


def _client(session_factory, redis):
    return TestClient(create_app(session_factory, redis))


def _seed_pending(session_factory):
    session = session_factory()
    art = Article(url="https://x/w1", title="标题", text="正文", content_hash="c", simhash_value=1, status=ArticleStatus.REVIEWED)
    session.add(art)
    session.flush()
    summary = Summary(article_id=art.id, summary_text="摘要内容" * 20, key_points=["要点一"], short_title="短标题", scores={}, status=SummaryStatus.SUMMARIZED)
    session.add(summary)
    session.flush()
    copy = PlatformCopy(summary_id=summary.id, platform="weibo", text="今日热点：#科技# 核心信息。", status=CopyStatus.REVIEWED)
    session.add(copy)
    session.flush()
    session.add(Review(copy_id=copy.id, verdict=Verdict.PENDING, scores={"style_score": 100}))
    session.commit()
    session.close()


def test_list_shows_pending_only(session_factory, redis):
    _seed_pending(session_factory)
    client = _client(session_factory, redis)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "今日热点" in resp.text
    assert "weibo" in resp.text


def test_detail_shows_copy_and_article(session_factory, redis):
    _seed_pending(session_factory)
    client = _client(session_factory, redis)
    resp = client.get("/copy/1")
    assert resp.status_code == 200
    assert "今日热点" in resp.text
    assert "短标题" in resp.text


def test_detail_unknown_copy_404(session_factory, redis):
    client = _client(session_factory, redis)
    assert client.get("/copy/999").status_code == 404


def test_publish_marks_review_and_leaves_list(session_factory, redis):
    _seed_pending(session_factory)
    client = _client(session_factory, redis)
    resp = client.post("/copy/1/publish", follow_redirects=False)
    assert resp.status_code == 303
    session = session_factory()
    publish = session.scalar(select(Publish).where(Publish.copy_id == 1))
    assert publish.status == PublishStatus.PUBLISHED
    assert publish.published_at is not None
    review = session.scalar(select(Review).where(Review.copy_id == 1))
    assert review.verdict == Verdict.PASS
    session.close()
    assert "今日热点" not in client.get("/").text


def test_reject_writes_comment(session_factory, redis):
    _seed_pending(session_factory)
    client = _client(session_factory, redis)
    assert client.post("/copy/1/reject", data={"comment": "风格不符"}, follow_redirects=False).status_code == 303
    session = session_factory()
    review = session.scalar(select(Review).where(Review.copy_id == 1))
    assert review.verdict == Verdict.REJECT
    assert review.comment == "风格不符"
    session.close()


def test_status_page_shows_counts(session_factory, redis):
    _seed_pending(session_factory)
    client = _client(session_factory, redis)
    resp = client.get("/status")
    assert resp.status_code == 200
    assert "reviewed" in resp.text
    assert "队列" in resp.text
