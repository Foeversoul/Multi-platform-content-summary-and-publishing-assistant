# S4 Web 审核台 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 FastAPI + Jinja2 审核台：待审列表、详情预览+一键复制、标记发布/驳回、运行状态页，供人工辅助发布使用。

**Architecture:** 新增 `app/web/`（应用工厂 `create_app(session_factory, redis, event_stream)` + 模板）；新增 `publish` 表（Alembic 迁移）；路由为同步 `def`（FastAPI 线程池），测试用 TestClient + fakeredis + SQLite。

**Tech Stack:** FastAPI · Jinja2 · Uvicorn（运行）· python-multipart（表单）· httpx TestClient（测试）

**Spec:** [docs/superpowers/specs/2026-08-18-s4-web-review-dashboard-design.md](../specs/2026-08-18-s4-web-review-dashboard-design.md)

## Global Constraints

- 新增模块 `app/web/`；`create_app(session_factory, redis, event_stream="assistant:events")` 工厂；依赖经 `app.state` 注入（测试可替换）。
- 路由：`GET /`（待审列表）、`GET /copy/{id}`（详情）、`POST /copy/{id}/publish`（303 跳回详情）、`POST /copy/{id}/reject`（comment 表单，303）、`GET /status`。
- 语义：publish 动作 → `review.verdict=pass` + `publish` 行（published/published_at，幂等 upsert）；reject → `review.verdict=reject` + comment(≤500)。
- 模板：`app/web/templates/{base,list,detail,status}.html`；复制按钮用 `navigator.clipboard`，数据经 `data-text` 传入（Jinja 自动转义）。
- 未知 copy_id → HTTP 404。
- 测试：TestClient 端到端（列表只含 pending；发布后离开列表；驳回写备注；状态页计数；404）；每任务必须提交。

## File Structure

| 文件 | 职责 |
| --- | --- |
| `app/storage/models.py` | 追加 `Publish` / `PublishStatus` |
| `app/web/__init__.py` | 包标记 |
| `app/web/main.py` | `create_app` + 全部路由 |
| `app/web/templates/base.html` | 布局 + 导航 |
| `app/web/templates/list.html` | 待审列表 |
| `app/web/templates/detail.html` | 详情预览 + 复制 + 发布/驳回表单 |
| `app/web/templates/status.html` | 运行状态 |
| `tests/test_web.py` | TestClient 测试 |
| `README.md` | 启动方式 |

---

### Task 1: Publish 模型与迁移

**Files:** `app/storage/models.py`、`tests/test_models_state.py`、`migrations/`

- [ ] **Step 1: 失败测试（追加）**

```python
def test_publish_crud(session_factory):
    session = session_factory()
    art = Article(url="https://x/pb1", title="t", text="正文", content_hash="c", simhash_value=1, status=ArticleStatus.REVIEWED)
    session.add(art); session.flush()
    summary = Summary(article_id=art.id, summary_text="摘要" * 30, key_points=["a"], short_title="t", scores={}, status=SummaryStatus.SUMMARIZED)
    session.add(summary); session.flush()
    copy = PlatformCopy(summary_id=summary.id, platform="weibo", text="文案", status=CopyStatus.REVIEWED)
    session.add(copy); session.flush()
    publish = Publish(copy_id=copy.id, status=PublishStatus.PUBLISHED, published_at=utcnow())
    session.add(publish); session.commit()
    row = session.scalar(select(Publish).where(Publish.copy_id == copy.id))
    assert row.status == PublishStatus.PUBLISHED
    session.close()
```

- [ ] **Step 2: 运行确认失败** → `cannot import name 'Publish'`
- [ ] **Step 3: 实现**

```python
class PublishStatus(StrEnum):
    PENDING = "pending"
    PUBLISHED = "published"
    SKIPPED = "skipped"


class Publish(Base):
    __tablename__ = "publish"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    copy_id: Mapped[int] = mapped_column(ForeignKey("platform_copy.id"), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default=PublishStatus.PENDING)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
```

迁移：`alembic revision --autogenerate -m "add publish"` + `upgrade head`

- [ ] **Step 4: 运行确认通过**（test_models_state 7 passed）
- [ ] **Step 5: 提交** `git commit -m "feat: publish model and migration"`

---

### Task 2: Web 应用骨架与待审列表

**Files:** `app/web/__init__.py`、`app/web/main.py`、`app/web/templates/base.html`、`app/web/templates/list.html`、`tests/test_web.py`

- [ ] **Step 1: 失败测试**

```python
# tests/test_web.py
from fastapi.testclient import TestClient

from app.web.main import create_app
from app.storage.models import Article, ArticleStatus, CopyStatus, PlatformCopy, Review, Summary, SummaryStatus, Verdict


def _client(session_factory, redis):
    return TestClient(create_app(session_factory, redis))


async def _seed_pending(session_factory):
    session = session_factory()
    art = Article(url="https://x/w1", title="标题", text="正文", content_hash="c", simhash_value=1, status=ArticleStatus.REVIEWED)
    session.add(art); session.flush()
    summary = Summary(article_id=art.id, summary_text="摘要内容" * 20, key_points=["要点一"], short_title="短标题", scores={}, status=SummaryStatus.SUMMARIZED)
    session.add(summary); session.flush()
    copy = PlatformCopy(summary_id=summary.id, platform="weibo", text="今日热点：#科技# 核心信息。", status=CopyStatus.REVIEWED)
    session.add(copy); session.flush()
    session.add(Review(copy_id=copy.id, verdict=Verdict.PENDING, scores={"style_score": 100}))
    session.commit()
    return session


def test_list_shows_pending_only(session_factory, redis):
    _seed_pending(session_factory)
    client = _client(session_factory, redis)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "今日热点" in resp.text
    assert "weibo" in resp.text
```

- [ ] **Step 2: 运行确认失败** → `ModuleNotFoundError: app.web.main`
- [ ] **Step 3: 实现**

```python
# app/web/main.py
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app.storage.models import Article, PlatformCopy, Review, Summary, Verdict

TEMPLATES = Jinja2Templates(directory=Path(__file__).parent / "templates")


def create_app(session_factory, redis, event_stream: str = "assistant:events"):
    app = FastAPI(title="content-assistant")
    app.state.session_factory = session_factory
    app.state.redis = redis
    app.state.event_stream = event_stream

    def _session():
        return app.state.session_factory()

    @app.get("/")
    def list_pending(request: Request):
        with _session() as session:
            rows = session.execute(
                select(Review, PlatformCopy, Summary, Article)
                .join(PlatformCopy, PlatformCopy.id == Review.copy_id)
                .join(Summary, Summary.id == PlatformCopy.summary_id)
                .join(Article, Article.id == Summary.article_id)
                .where(Review.verdict == Verdict.PENDING)
                .order_by(Review.created_at.desc())
            ).all()
        return TEMPLATES.TemplateResponse(request, "list.html", {"rows": rows})

    return app
```

`templates/base.html`：HTML5 + 导航（待审列表 / 运行状态）+ `{% block content %}`。
`templates/list.html`：表格（平台、标题、文案摘要、风格分、详情链接）；空状态"暂无待审文案"。

- [ ] **Step 4: 运行确认通过**（1 passed）
- [ ] **Step 5: 提交** `git commit -m "feat: web app skeleton and pending list"`

---

### Task 3: 详情页 + 复制

**Files:** `app/web/main.py`、`templates/detail.html`、`tests/test_web.py`

- [ ] **Step 1: 失败测试**

```python
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
```

- [ ] **Step 2: 运行确认失败**
- [ ] **Step 3: 实现**：`GET /copy/{copy_id}` 查询 Review/PlatformCopy/Summary/Article + Publish；模板展示文章标题、摘要、要点、平台、文案、scores（style_score/length_ok/敏感词/广告法命中）、复制按钮（`data-text` + clipboard JS）、发布/驳回表单、返回链接。
- [ ] **Step 4: 运行确认通过**（2 passed）
- [ ] **Step 5: 提交** `git commit -m "feat: copy detail preview with clipboard"`

---

### Task 4: 发布与驳回动作

**Files:** `app/web/main.py`、`tests/test_web.py`

- [ ] **Step 1: 失败测试**

```python
from datetime import UTC, datetime
from app.storage.models import Publish, PublishStatus


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
```

- [ ] **Step 2: 运行确认失败**
- [ ] **Step 3: 实现**：POST publish（upsert Publish + review=pass）；POST reject（review=reject + comment 截断 500）；均 303 回详情。
- [ ] **Step 4: 运行确认通过**（2 passed）
- [ ] **Step 5: 提交** `git commit -m "feat: publish and reject actions"`

---

### Task 5: 运行状态页

**Files:** `app/web/main.py`、`templates/status.html`、`tests/test_web.py`

- [ ] **Step 1: 失败测试**

```python
def test_status_page_shows_counts(session_factory, redis):
    _seed_pending(session_factory)
    client = _client(session_factory, redis)
    resp = client.get("/status")
    assert resp.status_code == 200
    assert "pending" in resp.text
    assert "queue" in resp.text.lower() or "队列" in resp.text
```

- [ ] **Step 2: 运行确认失败**
- [ ] **Step 3: 实现**：`GET /status` — article/copy/event 状态计数（GROUP BY）、pending review 数、`XLEN(event_stream)`（redis 不可达时容错为 0）、最近 10 条 event_log（类型/状态/时间）。
- [ ] **Step 4: 运行确认通过**（1 passed）
- [ ] **Step 5: 提交** `git commit -m "feat: status dashboard"`

---

### Task 6: 运行说明与全量验证

**Files:** `README.md`、`.env.example`（无需新增）

- [ ] **Step 1: README 追加**

```markdown
## S4：Web 审核台
启动：`python -m uvicorn app.web.main:app --host 127.0.0.1 --port 8000`
- `/` 待审列表；`/copy/{id}` 预览+复制；标记发布/驳回；`/status` 运行状态
```

- [ ] **Step 2: 全量验证**：`pytest -q` ALL PASS；`ruff check --no-cache app tests` clean
- [ ] **Step 3: 提交** `git commit -m "docs: s4 web dashboard usage"`

---

## Self-Review 结论

1. **Spec 覆盖**：列表/详情/复制/发布/驳回/状态页与 publish 模型均有任务；404 与幂等 upsert 在 Task 3/4 覆盖。
2. **占位符扫描**：无 TBD/TODO；代码步骤完整。
3. **类型一致性**：`create_app(session_factory, redis, event_stream)` 在 Task 2/5 一致；`Publish`/`PublishStatus` 字段跨任务一致。
