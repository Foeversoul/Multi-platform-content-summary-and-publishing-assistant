# S5 运维优化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 收尾加固：统一 structlog 日志、死信/失败人工兜底（Web 重跑/放弃）、接入与运维文档。

**Architecture:** 新增 `app/log.py`（日志配置）；扩展 `app/orchestrator/state.py`（DEAD_LETTER 出口）；扩展 `app/web/main.py`（/failed 与操作路由）；worker 启动时初始化日志。

**Spec:** [docs/superpowers/specs/2026-08-18-s5-operations-hardening-design.md](../specs/2026-08-18-s5-operations-hardening-design.md)

## Global Constraints

- 状态机：`FAILED → {PENDING, DEAD_LETTER}`；`DEAD_LETTER → {PENDING, REJECTED}`（新增）。
- 路由：`GET /failed`、`POST /failed/{id}/retry`（状态→pending + emit crawl.requested，303）、`POST /failed/{id}/discard`（dead_letter→rejected，303）。
- 日志：`setup_logging(log_dir=None, level=INFO)`；有 log_dir 时写 `data/logs/app.log`（RotatingFileHandler 5MB×3）；structlog 输出含 level/time/message。
- worker main 启动即调用 `setup_logging(settings.data_dir / "logs")`。
- 测试：状态机两出口、/failed 列表与操作、日志文件生成；每任务必须提交。

---

### Task 1: 统一日志配置

**Files:** `app/log.py`、`tests/test_log.py`、`app/worker.py`

- [ ] **Step 1: 失败测试**

```python
# tests/test_log.py
from app.log import setup_logging


def test_setup_logging_creates_file(tmp_path):
    log_dir = tmp_path / "logs"
    setup_logging(log_dir=log_dir)
    assert (log_dir / "app.log").exists()


def test_setup_logging_without_dir_ok():
    setup_logging(log_dir=None)  # 不抛异常
```

- [ ] **Step 2: 运行确认失败** → `ModuleNotFoundError: app.log`
- [ ] **Step 3: 实现**

```python
# app/log.py
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

import structlog


def setup_logging(log_dir: Path | None = None, level: int = logging.INFO) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(log_dir / "app.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"))
    logging.basicConfig(level=level, handlers=handlers, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
    )
```

`app/worker.py` main 开头：`setup_logging(settings.data_dir / "logs")`。

- [ ] **Step 4: 运行确认通过**（2 passed）
- [ ] **Step 5: 提交** `git commit -m "feat: structured logging setup"`

---

### Task 2: 状态机扩展

**Files:** `app/orchestrator/state.py`、`tests/test_models_state.py`

- [ ] **Step 1: 失败测试（追加）**

```python
def test_dead_letter_manual_outlets():
    transition(ArticleStatus.DEAD_LETTER, ArticleStatus.PENDING)
    transition(ArticleStatus.DEAD_LETTER, ArticleStatus.REJECTED)
```

- [ ] **Step 2: 运行确认失败**（InvalidTransitionError）
- [ ] **Step 3: 实现**：`ArticleStatus.DEAD_LETTER: {ArticleStatus.PENDING, ArticleStatus.REJECTED}`。
- [ ] **Step 4: 运行确认通过**（8 passed）
- [ ] **Step 5: 提交** `git commit -m "feat: manual retry and discard outlets for dead letters"`

---

### Task 3: /failed 列表页

**Files:** `app/web/main.py`、`app/web/templates/failed.html`、`tests/test_web.py`

- [ ] **Step 1: 失败测试**

```python
def test_failed_list_shows_dead_letters(session_factory, redis):
    session = session_factory()
    session.add(Article(url="https://x/f1", title="失败文章", text="t", content_hash="c", simhash_value=1, status=ArticleStatus.FAILED))
    session.commit()
    client = _client(session_factory, redis)
    resp = client.get("/failed")
    assert resp.status_code == 200
    assert "失败文章" in resp.text
```

- [ ] **Step 2: 运行确认失败**
- [ ] **Step 3: 实现**：`GET /failed` 查询 status in (failed, dead_letter) 的 article（含 source 名），模板表格 + 重跑/放弃按钮。
- [ ] **Step 4: 运行确认通过**
- [ ] **Step 5: 提交** `git commit -m "feat: failed and dead-letter list page"`

---

### Task 4: 重跑与放弃动作

**Files:** `app/web/main.py`、`tests/test_web.py`

- [ ] **Step 1: 失败测试**

```python
def test_retry_failed_article_requeues(session_factory, redis):
    session = session_factory()
    session.add(Article(url="https://x/f2", title="重跑", text="t", content_hash="c", simhash_value=2, status=ArticleStatus.FAILED))
    session.commit()
    client = _client(session_factory, redis)
    assert client.post("/failed/1/retry", follow_redirects=False).status_code == 303
    session2 = session_factory()
    art = session2.get(Article, 1)
    assert art.status == ArticleStatus.PENDING
    session2.close()


def test_discard_dead_letter_marks_rejected(session_factory, redis):
    session = session_factory()
    session.add(Article(url="https://x/f3", title="放弃", text="t", content_hash="c", simhash_value=3, status=ArticleStatus.DEAD_LETTER))
    session.commit()
    client = _client(session_factory, redis)
    assert client.post("/failed/1/discard", follow_redirects=False).status_code == 303
    session2 = session_factory()
    assert session2.get(Article, 1).status == ArticleStatus.REJECTED
    session2.close()
```

- [ ] **Step 2: 运行确认失败**
- [ ] **Step 3: 实现**

```python
@app.post("/failed/{article_id}/retry")
def retry_article(article_id: int):
    with _session() as session:
        article = session.get(Article, article_id)
        if article is None:
            raise HTTPException(404, "article not found")
        transition(ArticleStatus(article.status), ArticleStatus.PENDING)
        article.status = ArticleStatus.PENDING
        source = session.get(Source, article.source_id) if article.source_id else None
        payload = {"source_id": source.external_id} if source else {"url": article.url}
        asyncio.run(emit_event(app.state.redis, session, "crawl.requested", payload, app.state.event_stream))
        session.commit()
    return RedirectResponse("/failed", status_code=303)


@app.post("/failed/{article_id}/discard")
def discard_article(article_id: int):
    with _session() as session:
        article = session.get(Article, article_id)
        if article is None:
            raise HTTPException(404, "article not found")
        transition(ArticleStatus(article.status), ArticleStatus.REJECTED)
        article.status = ArticleStatus.REJECTED
        session.commit()
    return RedirectResponse("/failed", status_code=303)
```

（需 `from app.orchestrator.state import transition`、`from app.storage.models import Source`、`from app.storage.queue import emit_event`；`asyncio` 已在 main.py 导入。）

- [ ] **Step 4: 运行确认通过**（2 passed）
- [ ] **Step 5: 提交** `git commit -m "feat: manual retry and discard actions"`

---

### Task 5: 接入与运维文档

**Files:** `README.md`

- [ ] **Step 1: README 追加**：接入新数据源（`sources.yaml` + SpiderInterface）、接入新平台（`platforms.yaml` + PlatformAdapter）、覆盖率补跑命令（`coverage run --source=app -m pytest`）、一周稳定性检查清单（日志无 ERROR 堆积/死信可人工处理/队列无积压/内存稳定）。
- [ ] **Step 2: 全量验证**：`pytest -q` ALL PASS；`ruff check --no-cache app tests` clean
- [ ] **Step 3: 提交** `git commit -m "docs: s5 operations and onboarding guide"`

---

## Self-Review 结论

1. **Spec 覆盖**：日志/状态机扩展//failed 列表/重跑/放弃/文档均有任务；Ruling P 的覆盖率补跑命令写入文档。
2. **占位符扫描**：无 TBD/TODO。
3. **类型一致性**：`transition`、`emit_event(redis, session, event_type, payload, stream)` 与既有签名一致；`create_app` 的 `app.state.event_stream` 由 Task 3/4 复用。
