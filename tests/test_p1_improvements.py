"""P1 批次测试（T3/T4/T5）：调度注册、发布幂等、HTML retry/discard、CSRF 同源防护。"""

import yaml
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.orchestrator.scheduler import start_scheduler
from app.storage.models import (
    Article,
    ArticleStatus,
    PlatformCopy,
    Publish,
    PublishStatus,
    Review,
    Summary,
    SummaryStatus,
    Verdict,
)
from app.web.actions import publish_copy
from app.web.main import create_app


def _seed_pending(session_factory, copy_id: int = 1):
    session = session_factory()
    art = Article(url=f"https://x/w{copy_id}", title="标题", text="正文", content_hash=f"c{copy_id}", simhash_value=copy_id, status=ArticleStatus.REVIEWED)
    session.add(art)
    session.flush()
    summary = Summary(article_id=art.id, summary_text="摘要内容" * 20, key_points=["要点一"], short_title="短标题", scores={}, status=SummaryStatus.SUMMARIZED)
    session.add(summary)
    session.flush()
    copy = PlatformCopy(summary_id=summary.id, platform="weibo", text="今日热点文案", status="reviewed")
    session.add(copy)
    session.flush()
    session.add(Review(copy_id=copy.id, verdict=Verdict.PENDING, scores={}))
    session.commit()
    session.close()
    return copy.id


def _seed_failed_article(session_factory, url: str = "https://x/f1") -> int:
    session = session_factory()
    art = Article(url=url, title="失败文章", text="t", content_hash="h1", simhash_value=9, status=ArticleStatus.FAILED)
    session.add(art)
    session.commit()
    article_id = art.id
    session.close()
    return article_id


def _client(session_factory, redis):
    return TestClient(create_app(session_factory, redis))


# ---------- T3 定时调度注册 ----------

async def test_start_scheduler_registers_jobs(settings, session_factory, redis, tmp_path):
    sources = [
        {"id": "s1", "name": "源一", "type": "rss", "url": "https://a.example/rss", "frequency_minutes": 10, "enabled": True},
        {"id": "s2", "name": "源二", "type": "web", "url": "https://b.example", "frequency_minutes": 30, "enabled": True},
        {"id": "s3", "name": "源三", "type": "web", "url": "https://c.example", "frequency_minutes": 60, "enabled": False},
    ]
    sources_file = tmp_path / "sources.yaml"
    sources_file.write_text(yaml.safe_dump({"sources": sources}), encoding="utf-8")
    settings.sources_file = sources_file
    scheduler = start_scheduler(settings, redis, session_factory)
    scheduler.start()
    try:
        jobs = scheduler.get_jobs()
        job_ids = {job.id for job in jobs}
        assert job_ids == {"crawl-s1", "crawl-s2"}  # 仅启用源注册；未启用源不注册
    finally:
        scheduler.shutdown(wait=False)


# ---------- T4 发布幂等 ----------

def test_publish_twice_idempotent(session_factory):
    copy_id = _seed_pending(session_factory)
    with session_factory() as session:
        publish_copy(session, copy_id)
    with session_factory() as session:
        publish_copy(session, copy_id)  # 重复发布：不报错、不产生重复记录（E3）
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Publish)) == 1
        publish = session.scalar(select(Publish))
        assert publish.status == PublishStatus.PUBLISHED
        assert publish.published_at is not None
        review = session.scalar(select(Review).where(Review.copy_id == copy_id))
        assert review.verdict == Verdict.PASS


def test_api_publish_twice_idempotent(session_factory, redis):
    copy_id = _seed_pending(session_factory)
    client = _client(session_factory, redis)
    assert client.post(f"/api/reviews/{copy_id}/publish").status_code == 200
    assert client.post(f"/api/reviews/{copy_id}/publish").status_code == 200  # 幂等
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Publish)) == 1


# ---------- T5 HTML 路由 ----------

async def test_html_retry_article_emits_event(session_factory, redis):
    article_id = _seed_failed_article(session_factory, "https://x/manual-1")
    client = _client(session_factory, redis)
    resp = client.post(f"/failed/{article_id}/retry", follow_redirects=False)
    assert resp.status_code == 303
    with session_factory() as session:
        assert session.get(Article, article_id).status == ArticleStatus.PENDING  # 回到待爬取
    assert len(await redis.xrange("assistant:events")) == 1  # crawl.requested 事件已入队


def test_html_discard_article(session_factory, redis):
    article_id = _seed_failed_article(session_factory)
    client = _client(session_factory, redis)
    resp = client.post(f"/failed/{article_id}/discard", follow_redirects=False)
    assert resp.status_code == 303
    with session_factory() as session:
        assert session.get(Article, article_id).status == ArticleStatus.REJECTED


def test_html_reject_requires_comment(session_factory, redis):
    copy_id = _seed_pending(session_factory)
    client = _client(session_factory, redis)
    resp = client.post(f"/copy/{copy_id}/reject", data={"comment": ""}, follow_redirects=False)
    assert resp.status_code == 303
    assert "error=comment_required" in resp.headers["location"]  # AC-IF-02
    with session_factory() as session:
        assert session.scalar(select(Review).where(Review.copy_id == copy_id)).verdict == Verdict.PENDING  # 未驳回


def test_html_reject_with_comment(session_factory, redis):
    copy_id = _seed_pending(session_factory)
    client = _client(session_factory, redis)
    resp = client.post(f"/copy/{copy_id}/reject", data={"comment": "风格不符"}, follow_redirects=False)
    assert resp.status_code == 303
    with session_factory() as session:
        review = session.scalar(select(Review).where(Review.copy_id == copy_id))
        assert review.verdict == Verdict.REJECT
        assert review.comment == "风格不符"


def test_html_csrf_rejects_cross_origin(session_factory, redis):
    copy_id = _seed_pending(session_factory)
    client = _client(session_factory, redis)
    resp = client.post(f"/copy/{copy_id}/publish", headers={"origin": "https://evil.example"}, follow_redirects=False)
    assert resp.status_code == 403  # 跨站提交被拒（S2）
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Publish)) == 0


def test_api_token_enforced(session_factory, redis):
    """配置 API Token 后，未携带凭证的 /api 请求返回 401（S1）。"""
    from app.config import Settings

    app = create_app(session_factory, redis, settings=Settings(api_token="secret"))
    client = TestClient(app)
    assert client.get("/api/status").status_code == 401
    assert client.get("/api/status", headers={"x-api-token": "secret"}).status_code == 200
    assert client.get("/api/health").status_code == 200  # 健康检查豁免鉴权


def test_api_health_check(session_factory, redis):
    client = _client(session_factory, redis)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == {"database": True, "redis": True}
