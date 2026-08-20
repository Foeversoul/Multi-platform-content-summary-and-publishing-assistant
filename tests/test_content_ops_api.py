"""内容 AI 处理 / 回收站 / 手动上传 API 测试。

覆盖：摘要重生成与编辑（含级联扩写）、扩写重生成与预览、删除与回收站、手动内容上传（文本/文件/docx）。
AI 均以无 LLM key（provider fallback）模式验证，保证确定性。
"""

import io
import zipfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import Settings
from app.storage.models import (
    Article,
    ArticleStatus,
    CopyStatus,
    PlatformCopy,
    Review,
    Summary,
    SummaryStatus,
    Verdict,
)
from app.web.content_ops import extract_docx_text
from app.web.main import create_app


@pytest.fixture
def client(session_factory, redis):
    app = create_app(session_factory, redis, settings=Settings())
    return TestClient(app)


def _seed_pending(session_factory, text: str | None = None):
    session = session_factory()
    art = Article(url="https://x/w1", title="测试标题", text=text or "这是一段用于测试的正文内容。" * 30, content_hash="c", simhash_value=1, status=ArticleStatus.REVIEWED)
    session.add(art)
    session.flush()
    summary = Summary(article_id=art.id, summary_text="旧摘要内容" * 20, key_points=["旧要点"], short_title="旧短标题", scores={}, status=SummaryStatus.SUMMARIZED)
    session.add(summary)
    session.flush()
    copy = PlatformCopy(summary_id=summary.id, platform="weibo", text="旧文案内容", status=CopyStatus.REVIEWED)
    session.add(copy)
    session.flush()
    session.add(Review(copy_id=copy.id, verdict=Verdict.PENDING, scores={}))
    session.commit()
    session.close()
    return {"article_id": art.id, "summary_id": summary.id, "copy_id": copy.id}


# ---------- 摘要重生成 / 编辑 ----------

def test_regenerate_summary(session_factory, client):
    ids = _seed_pending(session_factory)
    c = client
    resp = c.post(f"/api/reviews/{ids['copy_id']}/summary/regenerate")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["summary_id"] == ids["summary_id"]
    assert data["summary_text"] != "旧摘要内容" * 20
    session = session_factory()
    summary = session.get(Summary, ids["summary_id"])
    assert summary.scores  # 重算质量分
    assert summary.status == SummaryStatus.SUMMARIZED
    session.close()


def test_edit_summary_updates_and_rewrites_copies(session_factory, client):
    ids = _seed_pending(session_factory)
    c = client
    resp = c.put(
        f"/api/reviews/{ids['copy_id']}/summary",
        json={"summary_text": "手动修改后的摘要内容", "key_points": ["新要点甲", "新要点乙"], "short_title": "新标题"},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["summary_text"] == "手动修改后的摘要内容"
    assert data["key_points"] == ["新要点甲", "新要点乙"]
    session = session_factory()
    copy = session.get(PlatformCopy, ids["copy_id"])
    # 摘要变更后文案被级联重写（fallback 模板），不再是旧文案
    assert copy.text != "旧文案内容"
    session.close()


def test_edit_summary_requires_text(session_factory, client):
    ids = _seed_pending(session_factory)
    assert client.put(f"/api/reviews/{ids['copy_id']}/summary", json={"summary_text": "   "}).status_code == 400


# ---------- 扩写重生成 / 风格预览 ----------

def test_regenerate_copy(session_factory, client):
    ids = _seed_pending(session_factory)
    c = client
    resp = c.post(f"/api/reviews/{ids['copy_id']}/copy/regenerate")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["copy_id"] == ids["copy_id"]
    assert data["text"]
    session = session_factory()
    review = session.scalar(select(Review).where(Review.copy_id == ids["copy_id"]))
    assert review.verdict == Verdict.PENDING  # 重生成后重置为待审
    session.close()


def test_regenerate_published_copy_forbidden(session_factory, client):
    ids = _seed_pending(session_factory)
    c = client
    assert c.post(f"/api/reviews/{ids['copy_id']}/publish").status_code == 200
    assert c.post(f"/api/reviews/{ids['copy_id']}/copy/regenerate").status_code == 400


def test_preview_copy(session_factory, client):
    ids = _seed_pending(session_factory)
    c = client
    resp = c.post(f"/api/reviews/{ids['copy_id']}/copy/preview", json={"platform": "xhs"})
    assert resp.status_code == 200
    assert resp.json()["data"]["text"]
    assert resp.json()["data"]["platform"] == "xhs"
    assert c.post(f"/api/reviews/{ids['copy_id']}/copy/preview", json={"platform": "nope"}).status_code == 400


# ---------- 删除 / 回收站 ----------

def test_delete_moves_to_recycle(session_factory, client):
    ids = _seed_pending(session_factory)
    c = client
    assert c.post(f"/api/reviews/{ids['copy_id']}/delete").status_code == 200
    assert c.get("/api/reviews").json()["data"]["total"] == 0  # 待审列表不再显示
    assert c.get(f"/api/reviews/{ids['copy_id']}").status_code == 404  # 详情拒绝
    recycle = c.get("/api/recycle").json()["data"]
    assert recycle["total"] == 1
    assert recycle["items"][0]["copy_id"] == ids["copy_id"]
    session = session_factory()
    assert session.get(PlatformCopy, ids["copy_id"]).deleted_at is not None
    session.close()


def test_batch_delete(session_factory, client):
    _seed_pending(session_factory)
    c = client
    resp = c.post("/api/reviews/batch-delete", json={"copy_ids": [1]})
    assert resp.status_code == 200
    assert resp.json()["data"]["deleted"] == 1
    assert c.post("/api/reviews/batch-delete", json={}).status_code == 400
    assert c.post("/api/reviews/batch-delete", json={"copy_ids": ["x"]}).status_code == 400


def test_restore_from_recycle(session_factory, client):
    ids = _seed_pending(session_factory)
    c = client
    c.post(f"/api/reviews/{ids['copy_id']}/delete")
    assert c.post(f"/api/recycle/{ids['copy_id']}/restore").status_code == 200
    assert c.get("/api/reviews").json()["data"]["total"] == 1  # 恢复后回到待审列表
    assert c.get("/api/recycle").json()["data"]["total"] == 0


def test_purge_from_recycle(session_factory, client):
    ids = _seed_pending(session_factory)
    c = client
    c.post(f"/api/reviews/{ids['copy_id']}/delete")
    assert c.delete(f"/api/recycle/{ids['copy_id']}").status_code == 200
    session = session_factory()
    assert session.get(PlatformCopy, ids["copy_id"]) is None
    assert session.scalar(select(Review).where(Review.copy_id == ids["copy_id"])) is None  # 审核记录级联清理
    session.close()
    assert c.get("/api/recycle").json()["data"]["total"] == 0


# ---------- 手动内容上传 ----------

def test_manual_content_enters_review_list(session_factory, client):
    c = client
    resp = c.post("/api/content/manual", json={"title": "手动文章", "content": "这是手动上传的内容正文。" * 40})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data["copy_ids"]) == 3  # weibo / moments / xhs 三个平台
    reviews = c.get("/api/reviews").json()["data"]
    assert reviews["total"] == 3  # 全部进入待审列表
    session = session_factory()
    article = session.get(Article, data["article_id"])
    assert article.title == "手动文章"
    assert article.status == ArticleStatus.SUMMARIZED
    session.close()


def test_manual_content_empty_rejected(client):
    assert client.post("/api/content/manual", json={"content": "   "}).status_code == 400


def _docx_bytes() -> bytes:
    """构造最小可解析的 .docx（zip 内含 word/document.xml）。"""
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
        "<w:p><w:r><w:t>第一段：Word 文档内容</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>第二段：更多内容</w:t></w:r></w:p>"
        "</w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("word/document.xml", xml)
    return buffer.getvalue()


def test_docx_text_extraction():
    text = extract_docx_text(_docx_bytes())
    assert text == "第一段：Word 文档内容\n第二段：更多内容"


def test_manual_file_upload_txt(session_factory, client):
    c = client
    resp = c.post(
        "/api/content/manual/file",
        files={"file": ("note.txt", "文件标题\n文件正文内容".encode() * 10, "text/plain")},
    )
    assert resp.status_code == 200
    assert len(resp.json()["data"]["copy_ids"]) == 3


def test_manual_file_upload_docx(session_factory, client):
    c = client
    resp = c.post(
        "/api/content/manual/file",
        files={"file": ("doc.docx", _docx_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data["copy_ids"]) == 3
    session = session_factory()
    article = session.get(Article, data["article_id"])
    assert "Word 文档内容" in article.text
    session.close()


def test_manual_file_unsupported_ext(client):
    resp = client.post("/api/content/manual/file", files={"file": ("a.exe", b"x", "application/octet-stream")})
    assert resp.status_code == 400


def test_manual_file_oversize(client):
    resp = client.post("/api/content/manual/file", files={"file": ("big.txt", b"x" * (2 * 1024 * 1024 + 1), "text/plain")})
    assert resp.status_code == 400
