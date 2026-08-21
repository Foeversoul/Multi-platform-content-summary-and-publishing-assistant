"""REST API 契约测试（PRD IF-01~13 / AC-IF-01/02/03）。"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import Settings
from app.storage.models import (
    Article,
    ArticleStatus,
    CopyStatus,
    EventLog,
    EventStatus,
    PlatformCopy,
    Publish,
    PublishStatus,
    Review,
    ScrapeJob,
    ScrapeJobItem,
    Summary,
    SummaryStatus,
    Verdict,
)
from app.web.main import create_app


class FakeScrapeService:
    """测试替身：仅创建任务，不执行真实爬取。"""

    def __init__(self) -> None:
        self.created: list[int] = []
        self._next_id = 1

    def create_job(self, session, urls):
        job = ScrapeJob(id=self._next_id, status="pending", url_count=len(urls))
        self._next_id += 1
        self.created.append(job.id)
        return job, 0

    def create_retry_job(self, session, item_id):
        return self.create_job(session, ["https://a.com/retry"])[0]

    def get_job(self, session, job_id):
        if job_id != 1:
            return None
        return ScrapeJob(id=job_id, status="succeeded", url_count=1, succeeded_count=1, failed_count=0)

    def get_items(self, session, job_id, page=1, page_size=20, status=None):
        return [], 0

    def get_item(self, session, item_id):
        return ScrapeJobItem(
            id=item_id, job_id=1, url="https://a.com/x", status="failed",
            error_code="HTTP_403", error_message="访问被拒绝（403）：目标站点禁止抓取",
        )

    async def run_job(self, session_factory, job_id):
        return None


@pytest.fixture
def client(session_factory, redis):
    fake = FakeScrapeService()
    app = create_app(session_factory, redis, settings=Settings(), scrape_service=fake)
    return TestClient(app), fake


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


# ---------- IF-01~05 审核与状态 ----------

def test_reviews_list(session_factory, client):
    _seed_pending(session_factory)
    c, _ = client
    resp = c.get("/api/reviews")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["total"] == 1
    item = body["data"]["items"][0]
    assert item["platform"] == "weibo"
    assert item["verdict"] == Verdict.PENDING.value
    assert item["article_title"] == "标题"


def test_reviews_list_filter_platform(session_factory, client):
    _seed_pending(session_factory)
    c, _ = client
    assert c.get("/api/reviews", params={"platform": "moments"}).json()["data"]["total"] == 0
    assert c.get("/api/reviews", params={"platform": "weibo"}).json()["data"]["total"] == 1


def test_review_detail(session_factory, client):
    _seed_pending(session_factory)
    c, _ = client
    body = c.get("/api/reviews/1").json()
    assert body["code"] == 0
    data = body["data"]
    assert data["copy"]["text"] == "今日热点：#科技# 核心信息。"
    assert data["article"]["title"] == "标题"
    assert data["summary"]["short_title"] == "短标题"


def test_review_detail_404(client):
    c, _ = client
    assert c.get("/api/reviews/999").status_code == 404


def test_publish(session_factory, client):
    _seed_pending(session_factory)
    c, _ = client
    resp = c.post("/api/reviews/1/publish")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["verdict"] == "pass"
    assert data["published_at"] is not None
    session = session_factory()
    review = session.scalar(select(Review).where(Review.copy_id == 1))
    assert review.verdict == Verdict.PASS
    publish = session.scalar(select(Publish).where(Publish.copy_id == 1))
    assert publish.status == PublishStatus.PUBLISHED
    session.close()


def test_one_click_publish_all_pending(session_factory, client):
    session = session_factory()
    for i in range(2):
        art = Article(
            url=f"https://x/b{i}",
            title=f"标题{i}",
            text="正文",
            content_hash=f"c{i}",
            simhash_value=i + 1,
            status=ArticleStatus.REVIEWED,
        )
        session.add(art)
        session.flush()
        summary = Summary(
            article_id=art.id,
            summary_text="摘要内容" * 20,
            key_points=["要点"],
            short_title="短标题",
            scores={},
            status=SummaryStatus.SUMMARIZED,
        )
        session.add(summary)
        session.flush()
        copy = PlatformCopy(summary_id=summary.id, platform="weibo", text=f"文案{i}", status=CopyStatus.REVIEWED)
        session.add(copy)
        session.flush()
        session.add(Review(copy_id=copy.id, verdict=Verdict.PENDING, scores={"style_score": 100}))
    # 已通过的不应被重复处理
    session.add(Review(copy_id=999999, verdict=Verdict.PASS, scores={}))
    session.commit()
    session.close()

    c, _ = client
    resp = c.post("/api/reviews/batch-publish")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["published"] == 2

    session = session_factory()
    pending = session.scalars(select(Review).where(Review.verdict == Verdict.PENDING)).all()
    assert pending == []
    passed = session.scalars(select(Review).where(Review.verdict == Verdict.PASS)).all()
    assert len(passed) == 3  # 2 一键通过 + 1 原有已通过
    session.close()


def test_reject_requires_comment(session_factory, client):
    _seed_pending(session_factory)
    c, _ = client
    assert c.post("/api/reviews/1/reject", json={}).status_code == 400  # AC-IF-02
    assert c.post("/api/reviews/1/reject", json={"comment": "   "}).status_code == 400
    resp = c.post("/api/reviews/1/reject", json={"comment": "风格不符"})
    assert resp.status_code == 200
    assert resp.json()["data"]["verdict"] == "reject"


def test_api_status(session_factory, client):
    _seed_pending(session_factory)
    c, _ = client
    data = c.get("/api/status").json()["data"]
    assert "event_counts" in data
    assert "article_counts" in data
    assert data["pending_reviews"] == 1


# ---------- IF-06~08 死信 ----------

def _seed_dead_event(session_factory, event_id="dead1"):
    session = session_factory()
    session.add(EventLog(id=event_id, event_type="article.crawled", payload="{}", status=EventStatus.DEAD, error="连接失败"))
    session.commit()
    session.close()


def test_failed_list(session_factory, client):
    _seed_dead_event(session_factory)
    c, _ = client
    data = c.get("/api/failed").json()["data"]
    assert data["total"] == 1
    assert data["items"][0]["event_id"] == "dead1"
    assert data["items"][0]["error"] == "连接失败"


def test_failed_discard(session_factory, client):
    _seed_dead_event(session_factory)
    c, _ = client
    assert c.post("/api/failed/dead1/discard").json()["data"]["status"] == "discarded"
    session = session_factory()
    assert session.get(EventLog, "dead1").status == EventStatus.DISCARDED
    session.close()


def test_failed_retry_requeues(session_factory, client, monkeypatch):
    _seed_dead_event(session_factory)

    async def fake_emit(redis, session, event_type, payload, stream):
        return "new-event-id"

    monkeypatch.setattr("app.web.api.emit_event", fake_emit)
    c, _ = client
    data = c.post("/api/failed/dead1/retry").json()["data"]
    assert data["status"] == "retried"
    session = session_factory()
    assert session.get(EventLog, "dead1").status == EventStatus.PROCESSED
    session.close()


def test_failed_retry_unknown_404(client):
    c, _ = client
    assert c.post("/api/failed/nope/retry").status_code == 404


# ---------- IF-09~13 爬取任务 ----------

def test_scrape_create_job(client):
    c, fake = client
    resp = c.post("/api/scrape/jobs", json={"urls": ["https://a.com/1", "https://a.com/2"]})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["url_count"] == 2
    assert data["status"] == "pending"
    assert data["job_id"] in fake.created


def test_scrape_create_invalid_body(client):
    c, _ = client
    assert c.post("/api/scrape/jobs", json={}).status_code == 400  # AC-IF-03
    assert c.post("/api/scrape/jobs", json={"urls": []}).status_code == 400
    assert c.post("/api/scrape/jobs", json={"urls": "not-a-list"}).status_code == 400


def test_scrape_job_detail(client):
    c, _ = client
    data = c.get("/api/scrape/jobs/1").json()["data"]
    assert data["status"] == "succeeded"
    assert data["succeeded_count"] == 1
    assert c.get("/api/scrape/jobs/999").status_code == 404


def test_scrape_job_items(client):
    c, _ = client
    data = c.get("/api/scrape/jobs/1/items").json()["data"]
    assert data["total"] == 0


def test_scrape_item_detail(client):
    c, _ = client
    data = c.get("/api/scrape/items/1").json()["data"]
    assert data["error_code"] == "HTTP_403"
    assert "403" in data["error_message"]


def test_scrape_item_retry(client):
    c, fake = client
    resp = c.post("/api/scrape/jobs/1/items/1/retry")
    assert resp.status_code == 200
    assert resp.json()["data"]["new_job_id"] in fake.created


# ---------- AI 对话助手：按需求执行模块功能 ----------

def test_chat_publish_single_copy(session_factory, client):
    _seed_pending(session_factory)
    c, _ = client
    data = c.post("/api/chat", json={"message": "发布文案 #1"}).json()["data"]
    assert data["source"] == "action"
    assert data["kind"] == "publish"
    assert "已发布" in data["reply"]
    session = session_factory()
    review = session.scalar(select(Review).where(Review.copy_id == 1))
    assert review.verdict == Verdict.PASS
    session.close()


def test_chat_publish_all(session_factory, client):
    _seed_pending(session_factory)
    c, _ = client
    data = c.post("/api/chat", json={"message": "一键通过所有待审"}).json()["data"]
    assert data["kind"] == "publish_all"
    assert data["data"]["published"] == 1
    session = session_factory()
    assert session.scalar(select(Review).where(Review.copy_id == 1)).verdict == Verdict.PASS
    session.close()


def test_chat_import_content(session_factory, redis):
    app = create_app(session_factory, redis, settings=Settings(llm_api_key=""), scrape_service=FakeScrapeService())
    c = TestClient(app)
    content = "这是一段通过对话助手导入的长文本正文内容。"
    data = c.post("/api/chat", json={"message": f"导入内容：{content * 4}"}).json()["data"]
    assert data["kind"] == "import"
    assert len(data["data"]["copy_ids"]) == 3
    # 一篇内容的多平台文案聚合为一组待审记录
    assert c.get("/api/reviews").json()["data"]["total"] == 1


def test_chat_pending_list(session_factory, client):
    _seed_pending(session_factory)
    c, _ = client
    data = c.post("/api/chat", json={"message": "列一下待审列表"}).json()["data"]
    assert data["kind"] == "pending_list"
    assert data["data"]["items"][0][0] == 1
    assert "标题" in data["reply"]


def test_chat_status_count(session_factory, client):
    _seed_pending(session_factory)
    c, _ = client
    data = c.post("/api/chat", json={"message": "当前待审数量是多少"}).json()["data"]
    assert data["kind"] == "status"
    assert data["data"]["pending"] == 1


def test_chat_regenerate_summary(session_factory, client):
    _seed_pending(session_factory)
    c, _ = client
    data = c.post("/api/chat", json={"message": "重新生成摘要 #1"}).json()["data"]
    assert data["kind"] == "regenerate_summary"
    assert "摘要" in data["reply"]


def test_chat_falls_back_to_qa_for_question(client):
    c, _ = client
    data = c.post("/api/chat", json={"message": "这个项目能做什么？"}).json()["data"]
    assert data["kind"] == "qa"
    assert data["source"] == "fallback"
    assert "多平台内容总结" in data["reply"]


def test_chat_scrape_creates_job(client):
    c, fake = client
    data = c.post("/api/chat", json={"message": "帮我爬取一个 https://example.com/article"}).json()["data"]
    assert data["kind"] == "scrape"
    assert data["data"]["job_id"] in fake.created
    assert data["data"]["urls"] == ["https://example.com/article"]
    assert "爬取" in data["reply"]


def test_chat_scrape_without_url_prompts_for_link(client):
    c, fake = client
    data = c.post("/api/chat", json={"message": "帮我爬取一个链接"}).json()["data"]
    assert data["kind"] == "scrape"
    assert "链接" in data["reply"]
    assert fake.created == []


def test_chat_scrape_text_finds_and_crawls_links(client):
    c, fake = client
    data = c.post(
        "/api/chat",
        json={"message": "帮我爬取 这篇报道不错 https://example.com/a，还有这篇 https://example.com/b"},
    ).json()["data"]
    assert data["kind"] == "scrape"
    assert data["data"]["urls"] == ["https://example.com/a", "https://example.com/b"]
    assert data["data"]["job_id"] in fake.created
    assert "2 个链接" in data["reply"]


def test_chat_crawl_question_falls_back(client):
    c, fake = client
    data = c.post("/api/chat", json={"message": "如何爬取内容？"}).json()["data"]
    assert data["kind"] == "qa"
    assert fake.created == []


def test_chat_publish_all_question_does_not_trigger_action(session_factory, client):
    _seed_pending(session_factory)
    c, _ = client
    data = c.post("/api/chat", json={"message": "如何一键通过所有待审？"}).json()["data"]
    assert data["kind"] == "qa"
    session = session_factory()
    review = session.scalar(select(Review).where(Review.copy_id == 1))
    assert review.verdict == Verdict.PENDING
    session.close()


def test_chat_regenerate_summary_question_does_not_trigger_action(session_factory, client):
    _seed_pending(session_factory)
    c, _ = client
    data = c.post("/api/chat", json={"message": "怎么重新生成摘要 #1？"}).json()["data"]
    assert data["kind"] == "qa"


def test_chat_regenerate_copy_question_does_not_trigger_action(session_factory, client):
    _seed_pending(session_factory)
    c, _ = client
    data = c.post("/api/chat", json={"message": "如何重新扩写 #1？"}).json()["data"]
    assert data["kind"] == "qa"
