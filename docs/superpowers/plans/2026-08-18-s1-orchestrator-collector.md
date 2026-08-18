# S1 调度协调与信息采集 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现"调度协调 + 信息采集"闭环：数据源（RSS/网页/手动 URL）经事件驱动流水线采集、去重、入库，状态正确流转到 `crawled`，并为后续 S2~S4 提供稳定接口。

**Architecture:** 单体 Python 包 `app`，五个逻辑模块之一"调度协调"以 Redis Streams 事件总线驱动"信息采集"。PostgreSQL 存状态与元数据、本地磁盘存原始文件；测试用 SQLite + fakeredis 跑通全链路，生产用 Postgres + Redis。

**Tech Stack:** Python 3.12 · SQLAlchemy 2.0 + Alembic · Redis Streams（redis-py async）· feedparser · httpx + readability-lxml · jieba · APScheduler · pydantic-settings · pytest + pytest-asyncio + fakeredis

**Spec:** [docs/superpowers/specs/2026-08-18-multiplatform-content-summary-publisher-design.md](../specs/2026-08-18-multiplatform-content-summary-publisher-design.md)

## Global Constraints

- Python 3.12 及以上；包根为 `app`，模块目录：`orchestrator` / `collector` / `processor` / `adapter` / `reviewer` / `web` / `storage` / `llm`（S1 只建 orchestrator、collector、storage 三个）。
- 状态机只允许合法迁移（`app/orchestrator/state.py`），非法迁移抛 `InvalidTransitionError`。
- 去重：URL 永久去重；content_hash 精确去重 + simhash 近似去重（hamming ≤ 3，等价相似度 ≥ 0.95），时间窗口 30 天。
- 合规限速：单域名请求间隔 ≥ 1 秒（QPS≤1），每次请求随机延时 3~8 秒（测试可配 0），遵守 robots.txt，UA 池轮换。
- 重试：采集 4xx 直接失败、5xx/网络错误指数退避重试 3 次（2s/4s/8s）；handler 级重试 2 次（1s/2s）；重试耗尽 → `dead_letter`。
- 幂等：事件按 `event_id` 去重消费；同一事件重复投递只处理一次。
- 事件模型：`crawl.requested`（payload: `source_id` 或 `url`）→ `article.crawled`（payload: `article_id`）。
- 测试：核心路径覆盖率 ≥ 80%；测试环境 SQLite（`:memory:` + StaticPool）+ fakeredis，不需要外部服务。
- 目录：`data/`、`.env` 已被 `.gitignore` 忽略；配置经 `app/config.py` 的 pydantic-settings 读取。
- 每个任务结束必须提交（提交信息见各任务 Step 5）。

## File Structure

| 文件 | 职责 |
| --- | --- |
| `pyproject.toml` | 依赖、pytest 配置（asyncio_mode=auto）、ruff |
| `app/config.py` | pydantic-settings 全局配置（含 .env 支持） |
| `app/collector/sources.py` | `SourceConfig` 模型与 `load_sources()`（sources.yaml） |
| `app/storage/models.py` | `Source` / `Article` / `EventLog` 表 + 状态枚举 |
| `app/orchestrator/state.py` | 状态机：`VALID_TRANSITIONS` + `transition()` |
| `app/storage/db.py` | engine / session_factory / session_scope |
| `app/storage/queue.py` | Redis Streams 事件总线：`emit_event` / `receive_one` |
| `app/orchestrator/registry.py` | `SkillRegistry`：事件注册、分发、重试、死信 |
| `app/collector/politeness.py` | 限速器 + 域名暂停 |
| `app/collector/robots.py` | robots.txt 解析与放行判断 |
| `app/collector/web_spider.py` | 网页采集：httpx + readability + UA 轮换 + 重试 |
| `app/collector/rss_spider.py` | RSS 采集：feedparser 解析条目 |
| `app/collector/dedup.py` | content_hash / simhash / hamming / 30 天窗口去重 |
| `app/collector/service.py` | 采集流水线：crawl_source / crawl_by_id / crawl_url / upsert_sources / build_registry |
| `app/orchestrator/scheduler.py` | APScheduler 定时触发 `crawl.requested` |
| `app/cli.py` | `python -m app.cli crawl --source-id X [--sync]`、`--url` |
| `app/worker.py` | 事件消费主循环 + `run_once`（测试入口） |
| `docker-compose.yml` | postgres / redis / worker（S1；app 审核台在 S4 加入） |
| `tests/` | 每个模块一个测试文件 + `conftest.py` + fixtures |

---

### Task 1: 项目脚手架与配置

**Files:**
- Create: `pyproject.toml`
- Create: `app/__init__.py`
- Create: `app/config.py`
- Create: `app/collector/__init__.py`
- Create: `app/collector/sources.py`
- Create: `sources.yaml`（示例配置）
- Create: `.env.example`
- Test: `tests/conftest.py`、`tests/test_config.py`

**Interfaces:**
- Consumes: 无（首个任务）。
- Produces:
  - `get_settings() -> Settings`（`app/config.py`）
  - `Settings` 字段：`database_url`、`redis_url`、`data_dir`、`sources_file`、`event_stream`、`event_group`、`min_domain_interval_seconds`、`random_delay_min_seconds`、`random_delay_max_seconds`、`request_timeout_seconds`、`crawl_retries`、`retry_base_seconds`、`domain_pause_minutes`、`dedup_window_days`、`simhash_threshold`、`user_agents`、`max_rss_entries`
  - `SourceConfig`（`app/collector/sources.py`）：`id`、`name`、`type: "rss"|"web"`、`url`、`frequency_minutes=60`、`enabled=True`、`render=False`
  - `load_sources(path: Path) -> list[SourceConfig]`
  - `session_factory` fixture（`tests/conftest.py`，SQLite 内存库）、`redis` fixture（fakeredis）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_config.py
from pathlib import Path
from app.config import Settings
from app.collector.sources import SourceConfig, load_sources

def test_settings_defaults():
    s = Settings()
    assert s.dedup_window_days == 30
    assert s.event_stream == "assistant:events"
    assert s.min_domain_interval_seconds == 1.0

def test_load_sources(tmp_path):
    p = tmp_path / "sources.yaml"
    p.write_text("""
sources:
  - id: demo-news
    name: 示例新闻
    type: rss
    url: https://example.com/feed.xml
    frequency_minutes: 60
  - id: demo-blog
    name: 示例博客
    type: web
    url: https://example.com/blog
""", encoding="utf-8")
    sources = load_sources(p)
    assert len(sources) == 2
    assert sources[0].type == "rss"
    assert sources[0].enabled is True
    assert sources[1].frequency_minutes == 60

def test_invalid_source_type_rejected(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("sources:\n  - id: x\n    name: x\n    type: ftp\n    url: http://a\n", encoding="utf-8")
    try:
        load_sources(p)
    except Exception as exc:
        assert "type" in str(exc)
    else:
        raise AssertionError("ftp type should be rejected")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'app.config'`

- [ ] **Step 3: 最小实现**

```toml
# pyproject.toml
[project]
name = "content-assistant"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "sqlalchemy>=2.0",
  "alembic>=1.13",
  "pydantic>=2.7",
  "pydantic-settings>=2.3",
  "redis>=5.0",
  "feedparser>=6.0",
  "httpx>=0.27",
  "readability-lxml>=0.8",
  "beautifulsoup4>=4.12",
  "jieba>=0.42",
  "PyYAML>=6.0",
  "apscheduler>=3.10",
  "structlog>=24.1",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-asyncio>=0.23", "fakeredis>=2.23", "ruff>=0.4", "coverage[toml]>=7.5"]

[build-system]
requires = ["setuptools>=69"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["app*"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 120
```

```python
# app/config.py
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite:///./data/dev.db"
    redis_url: str = "redis://localhost:6379/0"
    data_dir: Path = Path("data")
    sources_file: Path = Path("sources.yaml")
    event_stream: str = "assistant:events"
    event_group: str = "workers"
    min_domain_interval_seconds: float = 1.0
    random_delay_min_seconds: float = 3.0
    random_delay_max_seconds: float = 8.0
    request_timeout_seconds: float = 15.0
    crawl_retries: int = 3
    retry_base_seconds: float = 2.0
    domain_pause_minutes: int = 30
    dedup_window_days: int = 30
    simhash_threshold: int = 3
    max_rss_entries: int = 50
    user_agents: list[str] = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    ]

    model_config = {"env_file": ".env", "env_prefix": "ASSISTANT_"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

```python
# app/collector/sources.py
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel


class SourceConfig(BaseModel):
    id: str
    name: str
    type: Literal["rss", "web"]
    url: str
    frequency_minutes: int = 60
    enabled: bool = True
    render: bool = False


class SourcesFile(BaseModel):
    sources: list[SourceConfig]


def load_sources(path: Path) -> list[SourceConfig]:
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return SourcesFile.model_validate(raw).sources
```

```yaml
# sources.yaml
sources:
  - id: demo-news
    name: 示例新闻
    type: rss
    url: https://example.com/feed.xml
    frequency_minutes: 60
  - id: demo-blog
    name: 示例博客
    type: web
    url: https://example.com/blog
    frequency_minutes: 120
```

```text
# .env.example
ASSISTANT_DATABASE_URL=postgresql+psycopg://assistant:assistant@localhost:5432/assistant
ASSISTANT_REDIS_URL=redis://localhost:6379/0
ASSISTANT_DATA_DIR=data
ASSISTANT_SOURCES_FILE=sources.yaml
```

```python
# tests/conftest.py
from pathlib import Path

import pytest

from app.config import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url="sqlite:///:memory:",
        redis_url="redis://localhost:6379/0",
        data_dir=tmp_path / "data",
        sources_file=tmp_path / "sources.yaml",
        random_delay_min_seconds=0.0,
        random_delay_max_seconds=0.0,
        retry_base_seconds=0.01,
    )
```

> `session_factory` 与 `redis` fixture 分别在 Task 2、Task 3 加入 conftest（见对应任务）。

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add pyproject.toml app/ sources.yaml .env.example tests/conftest.py tests/test_config.py
git commit -m "feat: scaffold project and config loading"
```

---

### Task 2: 数据模型、状态机与数据库

**Files:**
- Create: `app/storage/__init__.py`
- Create: `app/storage/models.py`
- Create: `app/orchestrator/__init__.py`
- Create: `app/orchestrator/state.py`
- Create: `app/storage/db.py`
- Create: `alembic.ini`、`migrations/`（alembic init 生成并接线）
- Test: `tests/test_models_state.py`

**Interfaces:**
- Consumes: `Settings`（Task 1）。
- Produces:
  - `ArticleStatus`（枚举：pending/crawled/summarized/adapted/reviewed/published/failed/dead_letter/rejected）、`EventStatus`（queued/processed/dead）
  - `Source` / `Article` / `EventLog` ORM 模型（`app/storage/models.py`）
  - `transition(current: ArticleStatus, target: ArticleStatus) -> None`、`InvalidTransitionError`（`app/orchestrator/state.py`）
  - `build_session_factory(database_url) -> sessionmaker`、`session_scope(factory)`（`app/storage/db.py`；SQLite 内存库使用 StaticPool）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_models_state.py
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_models_state.py -v`
Expected: FAIL，`ModuleNotFoundError: app.storage`

- [ ] **Step 3: 最小实现**

```python
# app/storage/models.py
from datetime import datetime, timezone
from enum import StrEnum

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class ArticleStatus(StrEnum):
    PENDING = "pending"
    CRAWLED = "crawled"
    SUMMARIZED = "summarized"
    ADAPTED = "adapted"
    REVIEWED = "reviewed"
    PUBLISHED = "published"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"
    REJECTED = "rejected"


class EventStatus(StrEnum):
    QUEUED = "queued"
    PROCESSED = "processed"
    DEAD = "dead"


class Source(Base):
    __tablename__ = "source"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    type: Mapped[str] = mapped_column(String(16))
    url: Mapped[str] = mapped_column(String(1024))
    frequency_minutes: Mapped[int] = mapped_column(Integer, default=60)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_crawled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Article(Base):
    __tablename__ = "article"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("source.id"), nullable=True)
    url: Mapped[str] = mapped_column(String(2048), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(500))
    publish_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    simhash_value: Mapped[int] = mapped_column(BigInteger, index=True)
    raw_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default=ArticleStatus.PENDING, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class EventLog(Base):
    __tablename__ = "event_log"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default=EventStatus.QUEUED, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

```python
# app/orchestrator/state.py
from app.storage.models import ArticleStatus


VALID_TRANSITIONS: dict[ArticleStatus, set[ArticleStatus]] = {
    ArticleStatus.PENDING: {ArticleStatus.CRAWLED, ArticleStatus.FAILED, ArticleStatus.DEAD_LETTER},
    ArticleStatus.CRAWLED: {ArticleStatus.SUMMARIZED, ArticleStatus.FAILED, ArticleStatus.DEAD_LETTER},
    ArticleStatus.SUMMARIZED: {ArticleStatus.ADAPTED, ArticleStatus.FAILED, ArticleStatus.DEAD_LETTER},
    ArticleStatus.ADAPTED: {ArticleStatus.REVIEWED, ArticleStatus.FAILED, ArticleStatus.DEAD_LETTER},
    ArticleStatus.REVIEWED: {ArticleStatus.PUBLISHED, ArticleStatus.REJECTED},
    ArticleStatus.FAILED: {ArticleStatus.PENDING, ArticleStatus.DEAD_LETTER},
    ArticleStatus.DEAD_LETTER: set(),
    ArticleStatus.REJECTED: set(),
}


class InvalidTransitionError(ValueError):
    pass


def transition(current: ArticleStatus, target: ArticleStatus) -> None:
    if target not in VALID_TRANSITIONS.get(current, set()):
        raise InvalidTransitionError(f"invalid transition: {current.value} -> {target.value}")
```

```python
# app/storage/db.py
from contextlib import contextmanager
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.storage.models import Base


def build_engine(database_url: str):
    kwargs = {}
    if database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    if database_url == "sqlite:///:memory:":
        kwargs["poolclass"] = StaticPool
    return create_engine(database_url, future=True, **kwargs)


def build_session_factory(database_url: str):
    engine = build_engine(database_url)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def session_scope(factory: sessionmaker) -> Iterator[Session]:
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
```

同时把 `session_factory` fixture 加入 `tests/conftest.py`：

```python
# tests/conftest.py（追加）
import pytest

from app.storage.db import build_session_factory


@pytest.fixture
def session_factory():
    return build_session_factory("sqlite:///:memory:")
```

`alembic init migrations` 后修改 `migrations/env.py`：

```python
# migrations/env.py（关键改动，其余保持默认）
from app.config import get_settings
from app.storage.models import Base

config.set_main_option("sqlalchemy.url", get_settings().database_url)
target_metadata = Base.metadata
```

然后运行 `alembic revision --autogenerate -m "initial tables"` 与 `alembic upgrade head`（开发库为 `data/dev.db`，生产库在 compose 启动时执行）。

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_models_state.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add app/storage app/orchestrator alembic.ini migrations tests/test_models_state.py
git commit -m "feat: db models, state machine and session factory"
```

---

### Task 3: Redis Streams 事件总线

**Files:**
- Create: `app/storage/queue.py`
- Test: `tests/test_queue.py`

**Interfaces:**
- Consumes: `EventLog` / `EventStatus`（Task 2）、`Session`。
- Produces:
  - `emit_event(redis, session, event_type, payload, stream) -> str`：写 EventLog(QUEUED) → commit → XADD；返回 event_id。
  - `receive_one(redis, session, group, consumer, dispatch, stream) -> bool`：从消费组取 1 条 → 幂等检查 → `await dispatch(event_type, payload, session)` → PROCESSED/DEAD → XACK；无消息返回 False。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_queue.py
import json

from app.storage.models import EventLog, EventStatus
from app.storage.queue import emit_event, receive_one


async def test_emit_writes_log_and_stream(redis, session_factory):
    session = session_factory()
    event_id = await emit_event(redis, session, "crawl.requested", {"source_id": "demo"}, "s:events")
    log = session.get(EventLog, event_id)
    assert log is not None
    assert log.status == EventStatus.QUEUED
    entries = await redis.xrange("s:events")
    assert len(entries) == 1
    session.close()


async def test_receive_dispatch_and_idempotency(redis, session_factory):
    session = session_factory()
    seen = []

    async def dispatch(event_type, payload, session):
        seen.append((event_type, payload["source_id"]))
        return "ok"

    event_id = await emit_event(redis, session, "crawl.requested", {"source_id": "demo"}, "s:events")
    ok1 = await receive_one(redis, session, "g1", "c1", dispatch, "s:events")
    assert ok1 is True
    assert seen == [("crawl.requested", "demo")]
    log = session.get(EventLog, event_id)
    assert log.status == EventStatus.PROCESSED
    # 同一事件再次投递到流中也不会重复处理
    await redis.xadd("s:events", {"event_id": event_id, "event_type": "crawl.requested", "payload": json.dumps({"source_id": "demo"})})
    ok2 = await receive_one(redis, session, "g1", "c1", dispatch, "s:events")
    assert ok2 is True
    assert len(seen) == 1
    session.close()


async def test_receive_no_message(redis, session_factory):
    session = session_factory()

    async def dispatch(event_type, payload, session):
        raise AssertionError("should not dispatch")

    assert await receive_one(redis, session, "g1", "c1", dispatch, "s:empty") is False
    session.close()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_queue.py -v`
Expected: FAIL，`ModuleNotFoundError: app.storage.queue`

- [ ] **Step 3: 最小实现**

```python
# app/storage/queue.py
import json
import logging
import uuid
from datetime import datetime, timezone

from redis.asyncio import Redis
from redis.exceptions import ResponseError
from sqlalchemy.orm import Session

from app.storage.models import EventLog, EventStatus

logger = logging.getLogger(__name__)


async def emit_event(
    redis: Redis,
    session: Session,
    event_type: str,
    payload: dict,
    stream: str,
) -> str:
    event_id = uuid.uuid4().hex
    session.add(
        EventLog(
            id=event_id,
            event_type=event_type,
            payload=json.dumps(payload, ensure_ascii=False),
            status=EventStatus.QUEUED,
        )
    )
    session.commit()
    await redis.xadd(
        stream,
        {
            "event_id": event_id,
            "event_type": event_type,
            "payload": json.dumps(payload, ensure_ascii=False),
        },
    )
    return event_id


async def receive_one(
    redis: Redis,
    session: Session,
    group: str,
    consumer: str,
    dispatch,
    stream: str,
) -> bool:
    try:
        await redis.xgroup_create(stream, group, id="0", mkstream=True)
    except ResponseError:
        pass  # 消费组已存在
    result = await redis.xreadgroup(group, consumer, {stream: ">"}, count=1)
    if not result:
        return False
    _, entries = result[0]
    msg_id, fields = entries[0]
    event_id = fields[b"event_id"].decode()
    log = session.get(EventLog, event_id)
    if log is None or log.status in (EventStatus.PROCESSED, EventStatus.DEAD):
        await redis.xack(stream, group, msg_id)
        session.commit()
        return True
    try:
        outcome = await dispatch(log.event_type, json.loads(log.payload), session)
    except Exception:
        session.rollback()
        log.status = EventStatus.DEAD
        log.processed_at = datetime.now(timezone.utc)
        session.commit()
        await redis.xack(stream, group, msg_id)
        raise
    log.status = EventStatus.PROCESSED if outcome in ("ok", "noop") else EventStatus.DEAD
    log.processed_at = datetime.now(timezone.utc)
    session.commit()
    await redis.xack(stream, group, msg_id)
    return True
```

同时把 `redis` fixture 加入 `tests/conftest.py`：

```python
# tests/conftest.py（追加）
import fakeredis
import fakeredis.aioredis
import pytest


@pytest.fixture
async def redis():
    server = fakeredis.FakeServer()
    client = fakeredis.aioredis.FakeRedis(server=server)
    yield client
    await client.aclose()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_queue.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add app/storage/queue.py tests/test_queue.py
git commit -m "feat: redis streams event bus with idempotent consumption"
```

---

### Task 4: 调度协调核心（事件注册与死信）

**Files:**
- Create: `app/orchestrator/registry.py`
- Create: `app/worker.py`
- Test: `tests/test_registry.py`

**Interfaces:**
- Consumes: `transition` / `ArticleStatus`（Task 2）、`receive_one`（Task 3）。
- Produces:
  - `SkillRegistry.register(event_type, handler)`、`SkillRegistry.has(event_type)`、`SkillRegistry.dispatch(event_type, payload, session, retries=2, base_seconds=1.0) -> Literal["ok","dead","noop"]`
  - handler 签名：`async def handler(payload: dict, session: Session) -> None`
  - `run_once(registry, settings, redis, session_factory) -> bool`（`app/worker.py`）
  - `main()`（worker 主循环，S1 先以 `run_once` 为测试入口）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_registry.py
from app.orchestrator.registry import SkillRegistry
from app.storage.models import Article, ArticleStatus


async def test_dispatch_calls_handler(session_factory):
    registry = SkillRegistry()
    seen = []

    async def handler(payload, session):
        seen.append(payload["x"])

    registry.register("evt", handler)
    session = session_factory()
    outcome = await registry.dispatch("evt", {"x": 1}, session)
    assert outcome == "ok"
    assert seen == [1]
    session.close()


async def test_dispatch_no_handler_is_noop(session_factory):
    registry = SkillRegistry()
    session = session_factory()
    assert await registry.dispatch("unknown", {}, session) == "noop"
    session.close()


async def test_dispatch_marks_article_dead_after_retries(session_factory):
    registry = SkillRegistry()

    async def failing_handler(payload, session):
        raise RuntimeError("boom")

    registry.register("evt", failing_handler)
    session = session_factory()
    art = Article(url="https://x/1", title="t", text="c", content_hash="h", simhash_value=0, status=ArticleStatus.PENDING)
    session.add(art)
    session.commit()
    outcome = await registry.dispatch("evt", {"article_id": art.id}, session, retries=2, base_seconds=0)
    assert outcome == "dead"
    session.refresh(art)
    assert art.status == ArticleStatus.DEAD_LETTER
    session.close()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_registry.py -v`
Expected: FAIL，`ModuleNotFoundError: app.orchestrator.registry`

- [ ] **Step 3: 最小实现**

```python
# app/orchestrator/registry.py
import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Literal

from sqlalchemy.orm import Session

from app.orchestrator.state import transition
from app.storage.models import Article, ArticleStatus

logger = logging.getLogger(__name__)

Handler = Callable[[dict, Session], Awaitable[None]]
Outcome = Literal["ok", "dead", "noop"]


class SkillRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, Handler] = {}

    def register(self, event_type: str, handler: Handler) -> None:
        self._handlers[event_type] = handler

    def has(self, event_type: str) -> bool:
        return event_type in self._handlers

    async def dispatch(
        self,
        event_type: str,
        payload: dict,
        session: Session,
        retries: int = 2,
        base_seconds: float = 1.0,
    ) -> Outcome:
        handler = self._handlers.get(event_type)
        if handler is None:
            logger.warning("no handler registered", extra={"event_type": event_type})
            return "noop"
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                await handler(payload, session)
                return "ok"
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt < retries:
                    await asyncio.sleep(base_seconds * (2**attempt))
        article_id = payload.get("article_id")
        if article_id:
            article = session.get(Article, article_id)
            if article is not None:
                transition(ArticleStatus(article.status), ArticleStatus.DEAD_LETTER)
                article.status = ArticleStatus.DEAD_LETTER
                session.commit()
        logger.error(
            "handler failed permanently",
            extra={"event_type": event_type, "error": repr(last_error)},
        )
        return "dead"
```

```python
# app/worker.py
import asyncio
import os

from redis.asyncio import Redis

from app.collector.service import build_registry, upsert_sources
from app.collector.sources import load_sources
from app.config import get_settings
from app.storage.db import build_session_factory
from app.storage.queue import receive_one


async def run_once(registry, settings, redis: Redis, session_factory) -> bool:
    with session_factory() as session:
        return await receive_one(
            redis,
            session,
            settings.event_group,
            f"worker-{os.getpid()}",
            registry.dispatch,
            settings.event_stream,
        )


async def main() -> None:
    settings = get_settings()
    session_factory = build_session_factory(settings.database_url)
    with session_factory() as session:
        upsert_sources(session, load_sources(settings.sources_file))
    redis = Redis.from_url(settings.redis_url)
    registry = build_registry(settings, redis)
    while True:
        processed = await run_once(registry, settings, redis, session_factory)
        if not processed:
            await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
```

> 注：`build_registry` / `upsert_sources` 在 Task 9 实现；Task 9 完成后 `python -m app.worker` 即可运行。

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_registry.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add app/orchestrator/registry.py app/worker.py tests/test_registry.py
git commit -m "feat: event registry with retry and dead-letter"
```

---

### Task 5: 采集合规与限速（robots / 频控 / 域名暂停）

**Files:**
- Create: `app/collector/politeness.py`
- Create: `app/collector/robots.py`
- Test: `tests/test_politeness.py`

**Interfaces:**
- Consumes: 无（纯工具模块）。
- Produces:
  - `RateLimiter(min_interval_seconds, random_min_seconds, random_max_seconds)`，`async wait(url)`：按域名限频 + 随机延时。
  - `DomainPauseRegistry(pause_minutes)`：`pause(domain)`、`is_paused(domain) -> bool`（到期自动解除）。
  - `RobotsPolicy.from_text(content: str)`：`can_fetch(user_agent, url) -> bool`（缺省 Allow）；`fetch_robots_text(client, base_url, user_agent) -> str | None`（404/网络失败返回 None）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_politeness.py
import time

from app.collector.politeness import DomainPauseRegistry, RateLimiter
from app.collector.robots import RobotsPolicy


async def test_rate_limiter_enforces_min_interval():
    limiter = RateLimiter(min_interval_seconds=0.1, random_min_seconds=0.0, random_max_seconds=0.0)
    start = time.monotonic()
    await limiter.wait("https://a.com/1")
    await limiter.wait("https://a.com/2")
    assert time.monotonic() - start >= 0.1


async def test_domain_pause_registry():
    reg = DomainPauseRegistry(pause_minutes=30)
    reg.pause("a.com")
    assert reg.is_paused("a.com")
    assert not reg.is_paused("b.com")


def test_robots_policy():
    policy = RobotsPolicy.from_text(
        "User-agent: *\nDisallow: /private/\nDisallow: /api\n"
    )
    assert policy.can_fetch("test-bot", "https://x.com/private/1") is False
    assert policy.can_fetch("test-bot", "https://x.com/public") is True


def test_robots_policy_empty_text_allows_all():
    policy = RobotsPolicy.from_text("")
    assert policy.can_fetch("test-bot", "https://x.com/anything") is True
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_politeness.py -v`
Expected: FAIL，`ModuleNotFoundError: app.collector.politeness`

- [ ] **Step 3: 最小实现**

```python
# app/collector/politeness.py
import asyncio
import random
import time
from urllib.parse import urlparse


def domain_of(url: str) -> str:
    return urlparse(url).netloc.lower()


class RateLimiter:
    def __init__(
        self,
        min_interval_seconds: float = 1.0,
        random_min_seconds: float = 3.0,
        random_max_seconds: float = 8.0,
    ) -> None:
        self.min_interval_seconds = min_interval_seconds
        self.random_min_seconds = random_min_seconds
        self.random_max_seconds = random_max_seconds
        self._last: dict[str, float] = {}

    async def wait(self, url: str) -> None:
        domain = domain_of(url)
        now = time.monotonic()
        if domain in self._last:
            wait = self._last[domain] + self.min_interval_seconds - now
            if wait > 0:
                await asyncio.sleep(wait)
        delay = random.uniform(self.random_min_seconds, self.random_max_seconds)
        if delay > 0:
            await asyncio.sleep(delay)
        self._last[domain] = time.monotonic()


class DomainPauseRegistry:
    def __init__(self, pause_minutes: int = 30) -> None:
        self.pause_minutes = pause_minutes
        self._until: dict[str, float] = {}

    def pause(self, domain: str, minutes: int | None = None) -> None:
        self._until[domain] = time.monotonic() + (minutes or self.pause_minutes) * 60

    def is_paused(self, domain: str) -> bool:
        until = self._until.get(domain, 0.0)
        return time.monotonic() < until
```

```python
# app/collector/robots.py
from urllib.parse import urlparse, urlunparse
from urllib.robotparser import RobotFileParser


class RobotsPolicy:
    def __init__(self, parser: RobotFileParser) -> None:
        self._parser = parser

    @classmethod
    def from_text(cls, content: str) -> "RobotsPolicy":
        parser = RobotFileParser()
        parser.parse(content.splitlines())
        return cls(parser)

    def can_fetch(self, user_agent: str, url: str) -> bool:
        return self._parser.can_fetch(user_agent, url)


def robots_url_for(base_url: str) -> str:
    parsed = urlparse(base_url)
    return urlunparse((parsed.scheme, parsed.netloc, "/robots.txt", "", "", ""))


async def fetch_robots_text(client, base_url: str, user_agent: str) -> str | None:
    try:
        resp = await client.get(robots_url_for(base_url), headers={"User-Agent": user_agent})
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.text
    except Exception:
        return None
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_politeness.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: 提交**

```bash
git add app/collector/politeness.py app/collector/robots.py tests/test_politeness.py
git commit -m "feat: rate limiting, robots policy and domain pause"
```

---

### Task 6: 网页采集器（httpx + readability + 重试）

**Files:**
- Create: `app/collector/base.py`
- Create: `app/collector/web_spider.py`
- Create: `tests/fixtures/page.html`
- Test: `tests/test_web_spider.py`

**Interfaces:**
- Consumes: `RateLimiter` / `DomainPauseRegistry`（Task 5）、`RobotsPolicy` / `fetch_robots_text`（Task 5）、`Settings`。
- Produces:
  - `Candidate` dataclass（`app/collector/base.py`）：`url`、`title`、`text`、`publish_time: datetime | None`、`source_id: int | None`
  - `WebSpider(settings, transport=None)`：`source_type = "web"`，`async fetch(source: SourceConfig) -> list[Candidate]`
  - `FetchError`（4xx/重试耗尽/域名暂停/robots 拒绝）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_web_spider.py
import httpx
import pytest

from app.collector.sources import SourceConfig
from app.collector.web_spider import FetchError, WebSpider

HTML = """<html><head><title>测试文章</title></head>
<body>
  <nav>导航链接</nav>
  <article>
    <h1>测试文章</h1>
    <p>第一段正文内容，包含关键信息。</p>
    <p>第二段正文内容，继续说明。</p>
  </article>
  <footer>版权信息</footer>
</body></html>"""


def _spider(settings):
    def handler(request):
        return httpx.Response(200, text=HTML, request=request)

    return WebSpider(settings, transport=httpx.MockTransport(handler))


async def test_web_spider_extracts_text(settings):
    spider = _spider(settings)
    source = SourceConfig(id="w1", name="网页", type="web", url="https://example.com/a")
    candidates = await spider.fetch(source)
    assert len(candidates) == 1
    assert candidates[0].title == "测试文章"
    assert "第一段正文内容" in candidates[0].text
    assert "导航链接" not in candidates[0].text


async def test_web_spider_4xx_fails_fast(settings):
    def handler(request):
        return httpx.Response(404, text="not found", request=request)

    spider = WebSpider(settings, transport=httpx.MockTransport(handler))
    source = SourceConfig(id="w2", name="网页", type="web", url="https://example.com/missing")
    with pytest.raises(FetchError):
        await spider.fetch(source)


async def test_web_spider_retries_5xx(settings):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503, text="busy", request=request)
        return httpx.Response(200, text=HTML, request=request)

    spider = WebSpider(settings, transport=httpx.MockTransport(handler))
    source = SourceConfig(id="w3", name="网页", type="web", url="https://example.com/retry")
    candidates = await spider.fetch(source)
    assert len(candidates) == 1
    assert calls["n"] == 3
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_web_spider.py -v`
Expected: FAIL，`ModuleNotFoundError: app.collector.web_spider`

- [ ] **Step 3: 最小实现**

```python
# app/collector/base.py
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.collector.sources import SourceConfig


@dataclass
class Candidate:
    url: str
    title: str
    text: str
    publish_time: datetime | None = None
    source_id: int | None = None


class Spider(Protocol):
    source_type: str

    async def fetch(self, source: SourceConfig) -> list[Candidate]: ...
```

```python
# app/collector/web_spider.py
import asyncio
import random
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from readability import Document

from app.collector.base import Candidate
from app.collector.politeness import DomainPauseRegistry, RateLimiter
from app.collector.robots import RobotsPolicy, fetch_robots_text
from app.collector.sources import SourceConfig
from app.config import Settings


class FetchError(RuntimeError):
    pass


def normalize_text(text: str) -> str:
    return " ".join(text.split())


class WebSpider:
    source_type = "web"

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.settings = settings
        self.transport = transport
        self.limiter = RateLimiter(
            settings.min_domain_interval_seconds,
            settings.random_delay_min_seconds,
            settings.random_delay_max_seconds,
        )
        self.pauses = DomainPauseRegistry(settings.domain_pause_minutes)

    def _ua(self) -> str:
        return random.choice(self.settings.user_agents)

    async def _get_with_retry(self, client: httpx.AsyncClient, url: str) -> str:
        attempts = max(1, self.settings.crawl_retries + 1)  # 1 次初试 + 3 次重试
        for attempt in range(attempts):
            try:
                resp = await client.get(url, timeout=self.settings.request_timeout_seconds)
                if resp.status_code in (403, 429):
                    self.pauses.pause(urlparse(url).netloc)
                    raise FetchError(f"blocked by server ({resp.status_code}): {url}")
                if 400 <= resp.status_code < 500:
                    raise FetchError(f"http {resp.status_code}: {url}")
                resp.raise_for_status()
                return resp.text
            except httpx.HTTPError as exc:
                if attempt == attempts - 1:
                    raise FetchError(f"fetch failed after {attempts} attempts: {url} ({exc!r})") from exc
                await asyncio.sleep(self.settings.retry_base_seconds * (2**attempt))
        raise FetchError(f"unreachable: {url}")

    async def fetch(self, source: SourceConfig) -> list[Candidate]:
        if source.render:
            raise FetchError(f"source {source.id}: render=true 暂不支持（S1 仅静态抓取）")
        domain = urlparse(source.url).netloc
        if self.pauses.is_paused(domain):
            raise FetchError(f"domain paused: {domain}")
        ua = self._ua()
        headers = {"User-Agent": ua}
        async with httpx.AsyncClient(headers=headers, transport=self.transport, follow_redirects=True) as client:
            robots_text = await fetch_robots_text(client, source.url, ua)
            if robots_text is not None and not RobotsPolicy.from_text(robots_text).can_fetch(ua, source.url):
                raise FetchError(f"robots.txt disallows: {source.url}")
            await self.limiter.wait(source.url)
            html = await self._get_with_retry(client, source.url)
        doc = Document(html)
        title = normalize_text(doc.title() or source.url)
        summary_html = doc.summary(html_partial=True)
        soup = BeautifulSoup(summary_html, "html.parser")
        for tag in soup(["nav", "footer", "aside", "form"]):
            tag.decompose()
        text = normalize_text(soup.get_text(" ", strip=True))
        if not text:
            return []
        return [Candidate(url=source.url, title=title[:500], text=text)]
```

`tests/fixtures/page.html`：

```html
<!-- tests/fixtures/page.html -->
<html><head><title>测试文章</title></head>
<body>
  <nav>导航链接</nav>
  <article>
    <h1>测试文章</h1>
    <p>第一段正文内容，包含关键信息。</p>
    <p>第二段正文内容，继续说明。</p>
  </article>
  <footer>版权信息</footer>
</body></html>
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_web_spider.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add app/collector/base.py app/collector/web_spider.py tests/fixtures/page.html tests/test_web_spider.py
git commit -m "feat: web spider with readability extraction and retries"
```

---

### Task 7: RSS 采集器（feedparser）

**Files:**
- Create: `app/collector/rss_spider.py`
- Create: `tests/fixtures/feed.xml`
- Test: `tests/test_rss_spider.py`

**Interfaces:**
- Consumes: `Candidate`（Task 6）、`Settings`。
- Produces: `RssSpider(settings)`：`source_type = "rss"`，`async fetch(source: SourceConfig) -> list[Candidate]`（文本取 entry content > summary，均做 HTML 转文本；无文本的条目跳过；取前 `max_rss_entries` 条）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_rss_spider.py
from pathlib import Path

from app.collector.rss_spider import RssSpider
from app.collector.sources import SourceConfig


async def test_rss_spider_parses_entries(settings, tmp_path: Path):
    feed = tmp_path / "feed.xml"
    feed.write_text("""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>示例频道</title>
  <item>
    <title>第一条新闻</title>
    <link>https://example.com/1</link>
    <pubDate>Mon, 17 Aug 2026 08:00:00 GMT</pubDate>
    <description>第一条的&lt;b&gt;摘要&lt;/b&gt;内容</description>
  </item>
  <item>
    <title>第二条新闻</title>
    <link>https://example.com/2</link>
    <description></description>
  </item>
</channel></rss>""", encoding="utf-8")
    source = SourceConfig(id="r1", name="RSS", type="rss", url=str(feed))
    spider = RssSpider(settings)
    candidates = await spider.fetch(source)
    assert len(candidates) == 1  # 第二条无文本被跳过
    assert candidates[0].url == "https://example.com/1"
    assert candidates[0].title == "第一条新闻"
    assert "摘要" in candidates[0].text
    assert candidates[0].publish_time is not None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_rss_spider.py -v`
Expected: FAIL，`ModuleNotFoundError: app.collector.rss_spider`

- [ ] **Step 3: 最小实现**

```python
# app/collector/rss_spider.py
import asyncio
from datetime import datetime, timezone

import feedparser
from bs4 import BeautifulSoup

from app.collector.base import Candidate
from app.collector.sources import SourceConfig
from app.config import Settings


def html_to_text(html: str) -> str:
    return " ".join(BeautifulSoup(html or "", "html.parser").get_text(" ", strip=True).split())


class RssSpider:
    source_type = "rss"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def fetch(self, source: SourceConfig) -> list[Candidate]:
        feed = await asyncio.to_thread(feedparser.parse, source.url)
        candidates: list[Candidate] = []
        for entry in feed.entries[: self.settings.max_rss_entries]:
            text = ""
            if entry.get("content"):
                text = html_to_text(entry.content[0].get("value", ""))
            elif entry.get("summary"):
                text = html_to_text(entry.summary)
            if not text:
                continue
            title = html_to_text(entry.get("title", "")) or source.url
            parsed = entry.get("published_parsed") or entry.get("updated_parsed")
            publish_time = datetime(*parsed[:6], tzinfo=timezone.utc) if parsed else None
            candidates.append(
                Candidate(
                    url=entry.get("link") or source.url,
                    title=title[:500],
                    text=text,
                    publish_time=publish_time,
                )
            )
        return candidates
```

`tests/fixtures/feed.xml`：

```xml
<!-- tests/fixtures/feed.xml -->
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>示例频道</title>
  <item>
    <title>第一条新闻</title>
    <link>https://example.com/1</link>
    <pubDate>Mon, 17 Aug 2026 08:00:00 GMT</pubDate>
    <description>第一条的&lt;b&gt;摘要&lt;/b&gt;内容</description>
  </item>
  <item>
    <title>第二条新闻</title>
    <link>https://example.com/2</link>
    <description></description>
  </item>
</channel></rss>
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_rss_spider.py -v`
Expected: PASS（1 passed）

- [ ] **Step 5: 提交**

```bash
git add app/collector/rss_spider.py tests/fixtures/feed.xml tests/test_rss_spider.py
git commit -m "feat: rss spider via feedparser"
```

---

### Task 8: 去重服务（content_hash + simhash）

**Files:**
- Create: `app/collector/dedup.py`
- Test: `tests/test_dedup.py`

**Interfaces:**
- Consumes: `Article`（Task 2）、`Settings`。
- Produces:
  - `normalize_text(text) -> str`、`hash_content(text) -> str`（sha256）
  - `tokenize(text) -> list[str]`（jieba 分词）、`simhash(text) -> int`（64 位）、`hamming(a, b) -> int`
  - `DedupService(window_days=30, threshold=3)`：`is_duplicate(session, url, content_hash, simhash_value) -> bool`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_dedup.py
from app.collector.dedup import DedupService, hash_content, hamming, simhash
from app.storage.models import Article, ArticleStatus, utcnow


def test_hash_and_simhash_stable():
    text = "这是一篇关于人工智能的测试文章，讨论大模型的应用。"
    assert hash_content(text) == hash_content(text)
    assert hamming(simhash(text), simhash(text)) == 0


def test_near_duplicate_simhash_close():
    a = "今天股市大涨，科技板块领涨，投资者情绪乐观，多家机构发布研报看好后市，成交量显著放大，北向资金持续流入。"
    b = "今天股市大涨，科技板块领涨，投资者情绪非常乐观，多家机构发布研报看好后市，成交量显著放大，北向资金持续流入。"
    assert hamming(simhash(a), simhash(b)) <= 3


def test_dedup_service_exact_hash(session_factory):
    session = session_factory()
    text = "唯一正文内容用于去重测试。"
    ch = hash_content(text)
    session.add(
        Article(
            url="https://x/1",
            title="t",
            text=text,
            content_hash=ch,
            simhash_value=simhash(text),
            status=ArticleStatus.CRAWLED,
            created_at=utcnow(),
        )
    )
    session.commit()
    svc = DedupService(window_days=30, threshold=3)
    assert svc.is_duplicate(session, "https://x/1", ch, simhash(text)) is True  # URL 重复
    assert svc.is_duplicate(session, "https://x/2", ch, simhash(text)) is True  # 哈希重复
    assert svc.is_duplicate(session, "https://x/3", hash_content("完全不同的正文内容。"), simhash("完全不同的正文内容。")) is False
    session.close()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_dedup.py -v`
Expected: FAIL，`ModuleNotFoundError: app.collector.dedup`

- [ ] **Step 3: 最小实现**

```python
# app/collector/dedup.py
import hashlib
import re
from datetime import datetime, timedelta, timezone

import jieba
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.storage.models import Article


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def hash_content(text: str) -> str:
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def tokenize(text: str) -> list[str]:
    return [t for t in jieba.cut(normalize_text(text)) if t.strip()]


def simhash(text: str, bits: int = 64) -> int:
    v = [0] * bits
    for token in tokenize(text):
        h = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16)
        for i in range(bits):
            v[i] += 1 if (h >> i) & 1 else -1
    out = 0
    for i in range(bits):
        if v[i] > 0:
            out |= 1 << i
    return out


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


class DedupService:
    def __init__(self, window_days: int = 30, threshold: int = 3) -> None:
        self.window_days = window_days
        self.threshold = threshold

    def is_duplicate(self, session: Session, url: str, content_hash: str, simhash_value: int) -> bool:
        if session.scalar(select(Article.id).where(Article.url == url)) is not None:
            return True
        since = datetime.now(timezone.utc) - timedelta(days=self.window_days)
        rows = session.execute(
            select(Article.content_hash, Article.simhash_value).where(Article.created_at >= since)
        ).all()
        for existing_hash, existing_simhash in rows:
            if existing_hash == content_hash:
                return True
            if hamming(simhash_value, existing_simhash) <= self.threshold:
                return True
        return False
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_dedup.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add app/collector/dedup.py tests/test_dedup.py
git commit -m "feat: content hash and simhash dedup service"
```

---

### Task 9: 采集流水线（CollectorService + 事件接线）

**Files:**
- Create: `app/collector/service.py`
- Test: `tests/test_service.py`

**Interfaces:**
- Consumes: `SourceConfig` / `load_sources`（Task 1）、`Source` / `Article` / `EventStatus`（Task 2）、`emit_event`（Task 3）、`WebSpider` / `RssSpider`（Task 6/7）、`DedupService`（Task 8）。
- Produces:
  - `upsert_sources(session, sources: list[SourceConfig]) -> None`
  - `CollectorService(settings, redis, spiders=None, dedup=None)`：
    - `async crawl_source(session, source: SourceConfig) -> list[int]`（新文章 id 列表；重复自动跳过；每篇发 `article.crawled`）
    - `async crawl_by_id(session, source_id: str) -> list[int]`
    - `async crawl_url(session, url: str) -> list[int]`
  - `build_registry(settings, redis) -> SkillRegistry`：注册 `crawl.requested` handler（payload 含 `source_id` 或 `url`）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_service.py
from sqlalchemy import select

from app.collector.service import CollectorService, build_registry, upsert_sources
from app.collector.sources import SourceConfig
from app.storage.models import Article, ArticleStatus, Source


def test_upsert_sources_creates_and_updates(session_factory):
    session = session_factory()
    upsert_sources(session, [SourceConfig(id="s1", name="旧名", type="rss", url="https://x/feed")])
    upsert_sources(session, [SourceConfig(id="s1", name="新名", type="rss", url="https://x/feed2")])
    rows = session.scalars(select(Source)).all()
    assert len(rows) == 1  # 同一 external_id 只保留一行
    src = rows[0]
    assert src.name == "新名"
    assert src.url == "https://x/feed2"
    session.close()


async def test_crawl_source_stores_article_and_emits(session_factory, redis, settings):
    from app.collector.base import Candidate

    class FakeSpider:
        source_type = "web"

        async def fetch(self, source):
            return [Candidate(url="https://x/a", title="文章A", text="这是第一篇文章的正文内容。")]

    class FakeDedup:
        def is_duplicate(self, session, url, content_hash, simhash_value):
            return False

    service = CollectorService(settings, redis, spiders={"web": FakeSpider()}, dedup=FakeDedup())
    session = session_factory()
    source = SourceConfig(id="w1", name="网页", type="web", url="https://x/a")
    ids = await service.crawl_source(session, source)
    assert len(ids) == 1
    art = session.get(Article, ids[0])
    assert art.status == ArticleStatus.CRAWLED
    assert art.content_hash
    raw_file = settings.data_dir / "raw" / f"{art.id}.txt"
    assert raw_file.exists()
    assert raw_file.read_text(encoding="utf-8") == "这是第一篇文章的正文内容。"
    events = await redis.xrange(settings.event_stream)
    assert len(events) == 1
    session.close()


async def test_crawl_by_id_unknown_raises(session_factory, redis, settings):
    service = CollectorService(settings, redis)
    session = session_factory()
    try:
        await service.crawl_by_id(session, "missing")
    except ValueError:
        pass
    else:
        raise AssertionError("unknown source_id should raise")
    session.close()


async def test_build_registry_handles_crawl_requested(session_factory, redis, settings):
    registry = build_registry(settings, redis)
    assert registry.has("crawl.requested")
    session = session_factory()
    outcome = await registry.dispatch("crawl.requested", {"source_id": "missing"}, session, retries=0)
    assert outcome == "dead"
    session.close()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_service.py -v`
Expected: FAIL，`ModuleNotFoundError: app.collector.service`

- [ ] **Step 3: 最小实现**

```python
# app/collector/service.py
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collector.dedup import DedupService, hash_content, simhash
from app.collector.rss_spider import RssSpider
from app.collector.sources import SourceConfig, load_sources
from app.collector.web_spider import WebSpider
from app.config import Settings
from app.orchestrator.registry import SkillRegistry
from app.orchestrator.state import transition
from app.storage.models import Article, ArticleStatus, Source
from app.storage.queue import emit_event

logger = logging.getLogger(__name__)

SPIDERS = {"web": WebSpider, "rss": RssSpider}


def upsert_sources(session: Session, sources: list[SourceConfig]) -> None:
    for cfg in sources:
        row = session.scalar(select(Source).where(Source.external_id == cfg.id))
        if row is None:
            session.add(
                Source(
                    external_id=cfg.id,
                    name=cfg.name,
                    type=cfg.type,
                    url=cfg.url,
                    frequency_minutes=cfg.frequency_minutes,
                    enabled=cfg.enabled,
                )
            )
        else:
            row.name = cfg.name
            row.type = cfg.type
            row.url = cfg.url
            row.frequency_minutes = cfg.frequency_minutes
            row.enabled = cfg.enabled
    session.commit()


class CollectorService:
    def __init__(self, settings: Settings, redis, spiders: dict | None = None, dedup: DedupService | None = None) -> None:
        self.settings = settings
        self.redis = redis
        self.spiders = spiders or {key: cls(settings) for key, cls in SPIDERS.items()}
        self.dedup = dedup or DedupService(settings.dedup_window_days, settings.simhash_threshold)

    async def crawl_source(self, session: Session, source: SourceConfig) -> list[int]:
        spider = self.spiders.get(source.type)
        if spider is None:
            raise ValueError(f"unknown source type: {source.type}")
        candidates = await spider.fetch(source)
        src_row = session.scalar(select(Source).where(Source.external_id == source.id))
        source_db_id = src_row.id if src_row is not None else None
        raw_dir = self.settings.data_dir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        new_ids: list[int] = []
        for cand in candidates:
            if not cand.text:
                continue
            ch = hash_content(cand.text)
            sh = simhash(cand.text)
            if self.dedup.is_duplicate(session, cand.url, ch, sh):
                logger.info("duplicate skipped", extra={"url": cand.url})
                continue
            article = Article(
                source_id=source_db_id if source_db_id is not None else cand.source_id,
                url=cand.url,
                title=cand.title[:500],
                publish_time=cand.publish_time,
                text=cand.text,
                content_hash=ch,
                simhash_value=sh,
                status=ArticleStatus.PENDING,
            )
            session.add(article)
            session.flush()
            transition(ArticleStatus.PENDING, ArticleStatus.CRAWLED)
            article.status = ArticleStatus.CRAWLED
            raw_path = raw_dir / f"{article.id}.txt"
            raw_path.write_text(cand.text, encoding="utf-8")
            article.raw_path = str(raw_path)
            await emit_event(self.redis, session, "article.crawled", {"article_id": article.id}, self.settings.event_stream)
            new_ids.append(article.id)
        session.commit()
        return new_ids

    async def crawl_by_id(self, session: Session, source_id: str) -> list[int]:
        row = session.scalar(select(Source).where(Source.external_id == source_id))
        if row is None:
            raise ValueError(f"unknown source_id: {source_id}")
        if not row.enabled:
            return []
        cfg = SourceConfig(
            id=row.external_id,
            name=row.name,
            type=row.type,
            url=row.url,
            frequency_minutes=row.frequency_minutes,
            enabled=row.enabled,
        )
        return await self.crawl_source(session, cfg)

    async def crawl_url(self, session: Session, url: str) -> list[int]:
        cfg = SourceConfig(id=f"manual-{uuid.uuid4().hex[:8]}", name="manual", type="web", url=url)
        return await self.crawl_source(session, cfg)


def build_registry(settings: Settings, redis) -> SkillRegistry:
    service = CollectorService(settings, redis)
    registry = SkillRegistry()

    async def handle_crawl_requested(payload: dict, session: Session) -> None:
        url = payload.get("url")
        source_id = payload.get("source_id")
        if url:
            await service.crawl_url(session, url)
        elif source_id:
            await service.crawl_by_id(session, source_id)
        else:
            raise ValueError("crawl.requested requires 'url' or 'source_id'")

    registry.register("crawl.requested", handle_crawl_requested)
    return registry
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_service.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: 提交**

```bash
git add app/collector/service.py tests/test_service.py
git commit -m "feat: collector pipeline and crawl event handler"
```

---

### Task 10: 定时调度（APScheduler）

**Files:**
- Create: `app/orchestrator/scheduler.py`
- Test: `tests/test_scheduler.py`

**Interfaces:**
- Consumes: `load_sources`（Task 1）、`upsert_sources`（Task 9）、`emit_event`（Task 3）。
- Produces:
  - `JobSpec(source_id: str, interval_minutes: int)` dataclass
  - `build_job_specs(sources: list[SourceConfig]) -> list[JobSpec]`（仅 enabled）
  - `start_scheduler(settings, redis, session_factory) -> AsyncIOScheduler`：先 upsert 数据源，再按频率注册 `interval` 任务，任务体向事件流 emit `crawl.requested`（payload `{"source_id": ...}`）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_scheduler.py
from app.collector.sources import SourceConfig
from app.orchestrator.scheduler import build_job_specs


def test_build_job_specs_only_enabled():
    sources = [
        SourceConfig(id="a", name="A", type="rss", url="https://x/a", frequency_minutes=30),
        SourceConfig(id="b", name="B", type="web", url="https://x/b", frequency_minutes=60, enabled=False),
    ]
    specs = build_job_specs(sources)
    assert [s.source_id for s in specs] == ["a"]
    assert specs[0].interval_minutes == 30
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_scheduler.py -v`
Expected: FAIL，`ModuleNotFoundError: app.orchestrator.scheduler`

- [ ] **Step 3: 最小实现**

```python
# app/orchestrator/scheduler.py
from dataclasses import dataclass

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.collector.service import upsert_sources
from app.collector.sources import SourceConfig, load_sources
from app.storage.queue import emit_event


@dataclass
class JobSpec:
    source_id: str
    interval_minutes: int


def build_job_specs(sources: list[SourceConfig]) -> list[JobSpec]:
    return [JobSpec(s.id, s.frequency_minutes) for s in sources if s.enabled]


def start_scheduler(settings, redis, session_factory) -> AsyncIOScheduler:
    sources = load_sources(settings.sources_file)
    with session_factory() as session:
        upsert_sources(session, sources)
    scheduler = AsyncIOScheduler()

    async def trigger_crawl(source_id: str) -> None:
        with session_factory() as session:
            await emit_event(redis, session, "crawl.requested", {"source_id": source_id}, settings.event_stream)

    for spec in build_job_specs(sources):
        scheduler.add_job(
            trigger_crawl,
            "interval",
            minutes=spec.interval_minutes,
            args=[spec.source_id],
            id=f"crawl-{spec.source_id}",
            replace_existing=True,
        )
    return scheduler
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_scheduler.py -v`
Expected: PASS（1 passed）

- [ ] **Step 5: 提交**

```bash
git add app/orchestrator/scheduler.py tests/test_scheduler.py
git commit -m "feat: apscheduler source crawl jobs"
```

---

### Task 11: CLI 入口

**Files:**
- Create: `app/cli.py`
- Create: `app/__main__.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `get_settings`（Task 1）、`emit_event`（Task 3）、`CollectorService` / `upsert_sources`（Task 9）、`load_sources`（Task 1）。
- Produces:
  - `run_crawl_command(args, settings, session_factory, redis) -> list[int]`：`--source-id` / `--url`，`--sync` 直接采集，否则入队后返回 `[]`
  - `main(argv=None)`：argparse 入口，`python -m app.cli crawl --source-id demo --sync` / `python -m app crawl ...`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_cli.py
import pytest

from app.cli import run_crawl_command
from app.collector.sources import SourceConfig
from app.collector.service import upsert_sources


async def test_run_crawl_sync_unknown_source_raises(settings, session_factory, redis):
    class Args:
        source_id = "missing"
        url = None
        sync = True

    with pytest.raises(ValueError):
        await run_crawl_command(Args(), settings, session_factory, redis)


async def test_run_crawl_queue_path_emits_event(settings, session_factory, redis):
    session = session_factory()
    upsert_sources(session, [SourceConfig(id="s1", name="S", type="rss", url="https://x/feed")])
    session.close()

    class Args:
        source_id = "s1"
        url = None
        sync = False

    ids = await run_crawl_command(Args(), settings, session_factory, redis)
    assert ids == []  # 入队模式立即返回
    assert await redis.xlen(settings.event_stream) == 1
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_cli.py -v`
Expected: FAIL，`ModuleNotFoundError: app.cli`

- [ ] **Step 3: 最小实现**

```python
# app/cli.py
import argparse
import asyncio

from redis.asyncio import Redis

from app.collector.service import CollectorService, upsert_sources
from app.collector.sources import load_sources
from app.config import Settings, get_settings
from app.storage.db import build_session_factory
from app.storage.queue import emit_event


async def run_crawl_command(args, settings: Settings, session_factory, redis: Redis) -> list[int]:
    service = CollectorService(settings, redis)
    with session_factory() as session:
        upsert_sources(session, load_sources(settings.sources_file))
        if not args.sync:
            payload = {"source_id": args.source_id} if args.source_id else {"url": args.url}
            if not any(payload.values()):
                raise ValueError("crawl requires --source-id or --url")
            await emit_event(redis, session, "crawl.requested", payload, settings.event_stream)
            return []
        if args.url:
            return await service.crawl_url(session, args.url)
        return await service.crawl_by_id(session, args.source_id)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="content-assistant")
    sub = parser.add_subparsers(dest="command", required=True)
    crawl = sub.add_parser("crawl", help="触发采集")
    crawl.add_argument("--source-id", help="数据源 id（来自 sources.yaml）")
    crawl.add_argument("--url", help="手动提交的 URL")
    crawl.add_argument("--sync", action="store_true", help="同步执行，不走事件队列")
    args = parser.parse_args(argv)
    settings = get_settings()
    session_factory = build_session_factory(settings.database_url)
    redis = Redis.from_url(settings.redis_url)
    try:
        if args.command == "crawl":
            ids = asyncio.run(run_crawl_command(args, settings, session_factory, redis))
            print("new_article_ids:", ids)
    finally:
        asyncio.run(redis.aclose())


if __name__ == "__main__":
    main()
```

```python
# app/__main__.py
from app.cli import main

main()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_cli.py -v`
Expected: PASS（1 passed）

- [ ] **Step 5: 提交**

```bash
git add app/cli.py app/__main__.py tests/test_cli.py
git commit -m "feat: cli for crawl trigger"
```

---

### Task 12: 集成测试与部署文件

**Files:**
- Create: `docker-compose.yml`
- Create: `README.md`
- Create: `tests/test_integration.py`
- Modify: `tests/fixtures/feed.xml`、`tests/fixtures/page.html`（若 Task 6/7 未写则补齐）

**Interfaces:**
- Consumes: Task 1~11 全部产物。
- Produces: 端到端验证（RSS fixture → 事件 → 入库 `crawled`；重复采集被去重）；单机部署编排。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_integration.py
from pathlib import Path

from sqlalchemy import select

from app.collector.sources import SourceConfig, load_sources
from app.collector.service import CollectorService, build_registry, upsert_sources
from app.storage.models import Article, ArticleStatus
from app.storage.queue import emit_event
from app.worker import run_once


def _seed_sources(tmp_path: Path) -> Path:
    sources_file = tmp_path / "sources.yaml"
    sources_file.write_text(
        f"""
sources:
  - id: local-rss
    name: 本地RSS
    type: rss
    url: {tmp_path / "feed.xml"}
    frequency_minutes: 60
""",
        encoding="utf-8",
    )
    return sources_file


async def test_rss_crawl_end_to_end(settings, session_factory, redis, tmp_path: Path):
    feed = tmp_path / "feed.xml"
    feed.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>频道</title>
  <item><title>集成文章一</title><link>https://example.com/i1</link><description>第一篇文章的正文摘要</description></item>
  <item><title>集成文章二</title><link>https://example.com/i2</link><description>第二篇文章的正文摘要</description></item>
</channel></rss>""",
        encoding="utf-8",
    )
    settings.sources_file = _seed_sources(tmp_path)
    session = session_factory()
    upsert_sources(session, load_sources(settings.sources_file))
    registry = build_registry(settings, redis)
    await emit_event(redis, session, "crawl.requested", {"source_id": "local-rss"}, settings.event_stream)
    assert await run_once(registry, settings, redis, session_factory) is True
    articles = session.scalars(select(Article)).all()
    assert len(articles) == 2
    assert all(a.status == ArticleStatus.CRAWLED for a in articles)
    assert all(a.content_hash for a in articles)
    assert all(a.raw_path for a in articles)
    assert all(a.source_id is not None for a in articles)
    # 重复采集：再跑一次同一事件，去重后不新增
    assert await run_once(registry, settings, redis, session_factory) is True  # 幂等：事件已处理，直接 ack
    assert len(session.scalars(select(Article)).all()) == 2
    # 模拟新一轮采集：直接调 service，确认去重生效
    service = CollectorService(settings, redis)
    ids = await service.crawl_by_id(session, "local-rss")
    assert ids == []
    session.close()


async def test_manual_url_crawl_end_to_end(settings, session_factory, redis):
    from app.collector.base import Candidate

    class FakeSpider:
        source_type = "web"

        async def fetch(self, source):
            return [Candidate(url="https://example.com/manual", title="手动文章", text="手动提交文章的正文内容。")]

    service = CollectorService(settings, redis, spiders={"web": FakeSpider()})
    session = session_factory()
    ids = await service.crawl_url(session, "https://example.com/manual")
    assert len(ids) == 1
    art = session.get(Article, ids[0])
    assert art.title == "手动文章"
    session.close()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_integration.py -v`
Expected: FAIL 或部分失败（事件链路尚未完整打通时断言不满足）

- [ ] **Step 3: 实现部署与文档**

```yaml
# docker-compose.yml
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: assistant
      POSTGRES_PASSWORD: assistant
      POSTGRES_DB: assistant
    ports: ["5432:5432"]
    volumes: ["pgdata:/var/lib/postgresql/data"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U assistant"]
      interval: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]

  worker:
    build: .
    command: sh -c "alembic upgrade head && python -m app.worker"
    environment:
      ASSISTANT_DATABASE_URL: postgresql+psycopg://assistant:assistant@postgres:5432/assistant
      ASSISTANT_REDIS_URL: redis://redis:6379/0
      ASSISTANT_SOURCES_FILE: /app/sources.yaml
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_started
    volumes: ["./sources.yaml:/app/sources.yaml:ro", "./data:/app/data"]

volumes:
  pgdata:
```

```dockerfile
# Dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./
COPY sources.yaml ./
RUN pip install --no-cache-dir . "psycopg[binary]"
```

> `docker-compose.yml` 暂不包含 `app`（Web 审核台）服务，S4 加入；S1 由 `worker` 承担消费。`psycopg[binary]` 在生产依赖安装（或加入 pyproject 的 `[project.optional-dependencies] prod`）。

`README.md` 快速开始：

```markdown
# 多平台内容总结与发布助手（S1：调度协调 + 信息采集）

## 本地开发
1. `pip install -e ".[dev]"`
2. 复制 `.env.example` 为 `.env`，按需修改
3. 配置 `sources.yaml`
4. 采集一个数据源：`python -m app.cli crawl --source-id demo-news --sync`
5. 手动提交 URL：`python -m app.cli crawl --url https://example.com/article --sync`
6. 跑测试：`python -m pytest`

## Docker 部署（S1）
`docker compose up -d postgres redis worker`
```

- [ ] **Step 4: 运行全部测试确认通过**

Run: `python -m pytest -v`
Expected: ALL PASS（含集成测试）

Run: `python -m coverage run -m pytest && python -m coverage report --fail-under=80`
Expected: 核心模块（app/）覆盖率 ≥ 80%

- [ ] **Step 5: 提交**

```bash
git add docker-compose.yml Dockerfile README.md tests/test_integration.py tests/fixtures
git commit -m "feat: s1 integration tests and deployment files"
```

---

## Self-Review 结论（计划编写者自查）

1. **Spec 覆盖**：S1 范围（事件路由/状态机/重试/死信、RSS+网页采集、URL+哈希+simhash 去重、QPS≤1/随机延时/robots/UA、30 天窗口、手动 URL、PostgreSQL+Redis+本地磁盘、pytest 闭环）均有对应任务；原始文本在 Task 9 落盘 `data/raw/<article_id>.txt` 并写入 `article.raw_path`。
2. **占位符扫描**：无 TBD/TODO；每个代码步骤均含完整可运行代码。
3. **类型一致性**：`emit_event` / `receive_one` / `dispatch` 的签名在 Task 3/4/9/10/11/12 中一致；`Candidate` 字段、`SourceConfig` 字段、状态枚举名跨任务一致；`CollectorService` 构造参数（settings, redis, spiders, dedup）在 Task 9/12 一致。
