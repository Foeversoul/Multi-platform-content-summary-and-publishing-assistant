# S3 内容适配 + 质量审核 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现内容适配与审核闭环：`summary.generated` 触发按平台（微博/朋友圈/小红书）规则+LLM 改写生成 `platform_copy`，`copy.adapted` 触发合规校验与自动评分并写入 `review`（verdict=pending 待人工），article 状态 `summarized → adapted → reviewed`，产出 `review.passed` 事件。

**Architecture:** 新增 `app/adapter/`（platforms/rules/wordlists/copywriter/compliance/service）与 `app/reviewer/`（quality/service）；沿用 S1 SkillRegistry 事件接线，新增 `summary.generated`、`copy.adapted` handlers；新增 `platform_copy`、`review` 表（Alembic 迁移）。

**Tech Stack:** Python 3.12 · pydantic/yaml（平台配置）· 标准库 re/json · LLM Provider 抽象（S2 已有）· pytest + fakeredis（沿用）

**Spec:** [docs/superpowers/specs/2026-08-18-s3-content-adaptation-review-design.md](../specs/2026-08-18-s3-content-adaptation-review-design.md)

## Global Constraints

- Python 3.12+；新增模块 `app/adapter/`、`app/reviewer/`。
- 平台规范：微博 1~140 字 + 1~3 个 `#话题#`；朋友圈 60~200 字 + 1~3 emoji；小红书 100~500 字 + 2~5 个 `#话题#` + 1~3 emoji。
- 状态机：article `summarized → adapted → reviewed`；copy `pending → adapted → reviewed`；非法迁移抛 `InvalidTransitionError`。
- 事件：`summary.generated {summary_id}` → 适配 → `copy.adapted {copy_id}` → 审核 → `review.passed {review_id, copy_id}`；无下游消费者的事件按 noop（S1 机制）。
- 幂等：`adapt_summary` 按 (summary_id, platform) 去重；article 状态仅在需要时迁移。
- 合规：敏感词 + 广告法违禁词命中即标记（记录到 review.scores），不阻断产出；所有文案默认 verdict=pending（待人工审核）。
- LLM：`generate_copy` 失败或输出非法 → 回退"标题：摘要+要点标签"截断版；测试用 FakeProvider，绝不发真实请求。
- 配置：`platforms.yaml`（平台规范）；`sensitive_words_file`/`ad_words_file` 可空（用内置默认词表）。
- 测试：核心模块覆盖率 ≥80%（本环境 jieba 已不参与 S3，可用 `coverage run --source=app` 测量；若覆盖率与其它进程互锁，按 Ruling P 记录并依赖直接单测）；每任务必须提交。

## File Structure

| 文件 | 职责 |
| --- | --- |
| `platforms.yaml` | 三平台规范（字数/标签/emoji/style_prompt） |
| `app/adapter/platforms.py` | `PlatformConfig`、`load_platforms(path)` |
| `app/adapter/wordlists.py` | 默认敏感词/广告法词表、`load_wordlist`、`find_hits` |
| `app/adapter/rules.py` | `count_tags`、`count_emojis`、`validate_text` |
| `app/adapter/copywriter.py` | `generate_copy`、`CopyResult`、`_fallback` |
| `app/adapter/compliance.py` | `check_compliance` |
| `app/adapter/service.py` | `AdapterService.adapt_summary`、`register_adapter_handlers` |
| `app/reviewer/quality.py` | `score_copy`（含 style_score 0-100） |
| `app/reviewer/service.py` | `ReviewerService.review_copy`、`register_reviewer_handlers` |
| `app/storage/models.py` | `PlatformCopy`、`Review`、`CopyStatus`、`Verdict` |
| `app/config.py` | `platforms_file`、`sensitive_words_file`、`ad_words_file` |
| `app/worker.py` | 注册 adapter/reviewer handlers |
| `tests/` | 每模块测试 + `tests/test_integration_adapter.py` |

---

### Task 1: 平台配置与加载

**Files:**
- Create: `platforms.yaml`、`app/adapter/__init__.py`、`app/adapter/platforms.py`
- Modify: `app/config.py`（追加 `platforms_file`、`sensitive_words_file`、`ad_words_file`）
- Test: `tests/test_platforms.py`

**Interfaces:**
- Produces: `PlatformConfig(id,name,min_chars,max_chars,min_tags=0,max_tags=0,min_emojis=0,max_emojis=0,style_prompt="")`；`load_platforms(path) -> dict[str, PlatformConfig]`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_platforms.py
from pathlib import Path

from app.adapter.platforms import PlatformConfig, load_platforms


def test_load_platforms(tmp_path: Path):
    p = tmp_path / "platforms.yaml"
    p.write_text(
        """
platforms:
  weibo:
    name: 微博
    min_chars: 1
    max_chars: 140
    min_tags: 1
    max_tags: 3
    style_prompt: 口语化
""",
        encoding="utf-8",
    )
    platforms = load_platforms(p)
    assert platforms["weibo"].max_chars == 140
    assert platforms["weibo"].min_tags == 1


def test_platform_config_defaults():
    cfg = PlatformConfig(id="x", name="X", min_chars=1, max_chars=10)
    assert cfg.min_tags == 0
    assert cfg.max_emojis == 0
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_platforms.py -v`
Expected: FAIL，`ModuleNotFoundError: app.adapter.platforms`

- [ ] **Step 3: 最小实现**

```yaml
# platforms.yaml
platforms:
  weibo:
    name: 微博
    min_chars: 1
    max_chars: 140
    min_tags: 1
    max_tags: 3
    style_prompt: "微博风格：核心信息先行，倒金字塔，口语化，1-3个#话题#，禁用长从句和标题党。"
  moments:
    name: 朋友圈
    min_chars: 60
    max_chars: 200
    min_emojis: 1
    max_emojis: 3
    style_prompt: "朋友圈风格：第一人称分享视角，生活化真诚，60-200字，可加1-3个emoji，禁用硬广和营销腔。"
  xhs:
    name: 小红书
    min_chars: 100
    max_chars: 500
    min_tags: 2
    max_tags: 5
    min_emojis: 1
    max_emojis: 3
    style_prompt: "小红书风格：第一人称种草分享，标题行作首行，100-500字，2-5个#话题#和1-3个emoji，禁用硬广和导流。"
```

```python
# app/adapter/platforms.py
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class PlatformConfig:
    id: str
    name: str
    min_chars: int
    max_chars: int
    min_tags: int = 0
    max_tags: int = 0
    min_emojis: int = 0
    max_emojis: int = 0
    style_prompt: str = ""


def load_platforms(path: Path) -> dict[str, PlatformConfig]:
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    platforms: dict[str, PlatformConfig] = {}
    for pid, cfg in (raw.get("platforms") or {}).items():
        platforms[pid] = PlatformConfig(id=pid, **cfg)
    return platforms
```

```python
# app/config.py（追加）
    platforms_file: Path = Path("platforms.yaml")
    sensitive_words_file: Path | None = None
    ad_words_file: Path | None = None
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_platforms.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: 提交**

```bash
git add platforms.yaml app/adapter app/config.py tests/test_platforms.py
git commit -m "feat: platform configs and loader"
```

---

### Task 2: 词表与命中检测

**Files:**
- Create: `app/adapter/wordlists.py`
- Test: `tests/test_wordlists.py`

**Interfaces:**
- Produces: `DEFAULT_SENSITIVE_WORDS`、`DEFAULT_AD_WORDS`、`load_wordlist(path | None) -> list[str]`、`find_hits(text, words) -> list[str]`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_wordlists.py
from app.adapter.wordlists import DEFAULT_AD_WORDS, DEFAULT_SENSITIVE_WORDS, find_hits, load_wordlist


def test_default_wordlists_nonempty():
    assert DEFAULT_SENSITIVE_WORDS
    assert DEFAULT_AD_WORDS


def test_find_hits():
    hits = find_hits("全网最低价，加微信详聊", DEFAULT_SENSITIVE_WORDS + DEFAULT_AD_WORDS)
    assert "加微信" in hits


def test_load_wordlist(tmp_path):
    p = tmp_path / "words.txt"
    p.write_text("词一\n词二\n\n词三\n", encoding="utf-8")
    assert load_wordlist(p) == ["词一", "词二", "词三"]
    assert load_wordlist(None) == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_wordlists.py -v`
Expected: FAIL，`ModuleNotFoundError: app.adapter.wordlists`

- [ ] **Step 3: 最小实现**

```python
# app/adapter/wordlists.py
from pathlib import Path

DEFAULT_SENSITIVE_WORDS = ["代购", "刷单", "加微信", "私聊", "返利"]
DEFAULT_AD_WORDS = ["国家级", "最高级", "最佳", "第一品牌", "顶级", "全网最低"]


def load_wordlist(path: Path | None) -> list[str]:
    if path is None or not path.exists():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def find_hits(text: str, words: list[str]) -> list[str]:
    return [w for w in words if w in text]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_wordlists.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add app/adapter/wordlists.py tests/test_wordlists.py
git commit -m "feat: wordlists and hit detection"
```

---

### Task 3: 规则引擎（字数/标签/emoji）

**Files:**
- Create: `app/adapter/rules.py`
- Test: `tests/test_rules.py`

**Interfaces:**
- Produces: `count_tags(text) -> int`、`count_emojis(text) -> int`、`RulesResult`、`validate_text(platform, text) -> RulesResult`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_rules.py
from app.adapter.platforms import PlatformConfig
from app.adapter.rules import count_emojis, count_tags, validate_text


def test_count_tags_and_emojis():
    assert count_tags("今天分享#科技#和#AI#") == 2
    assert count_emojis("好棒😀还有🚀") == 2


def test_validate_text_weibo():
    platform = PlatformConfig(id="weibo", name="微博", min_chars=1, max_chars=140, min_tags=1, max_tags=3)
    result = validate_text(platform, "今日热点：#科技# 核心信息。")
    assert result.length_ok is True
    assert result.tags_ok is True
    assert result.ok is True


def test_validate_text_rejects_long_moments():
    platform = PlatformConfig(id="moments", name="朋友圈", min_chars=60, max_chars=200, min_emojis=1, max_emojis=3)
    result = validate_text(platform, "太短")
    assert result.length_ok is False
    assert result.ok is False
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_rules.py -v`
Expected: FAIL，`ModuleNotFoundError: app.adapter.rules`

- [ ] **Step 3: 最小实现**

```python
# app/adapter/rules.py
import re
from dataclasses import dataclass

from app.adapter.platforms import PlatformConfig

TAG_RE = re.compile(r"#([^#\s]+)#")
EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF]")


def count_tags(text: str) -> int:
    return len(TAG_RE.findall(text))


def count_emojis(text: str) -> int:
    return len(EMOJI_RE.findall(text))


@dataclass
class RulesResult:
    length: int
    length_ok: bool
    tags: int
    tags_ok: bool
    emojis: int
    emojis_ok: bool
    ok: bool


def validate_text(platform: PlatformConfig, text: str) -> RulesResult:
    length = len(text)
    tags = count_tags(text)
    emojis = count_emojis(text)
    length_ok = platform.min_chars <= length <= platform.max_chars
    tags_ok = platform.min_tags <= tags <= platform.max_tags
    emojis_ok = platform.min_emojis <= emojis <= platform.max_emojis
    return RulesResult(length, length_ok, tags, tags_ok, emojis, emojis_ok, length_ok and tags_ok and emojis_ok)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_rules.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add app/adapter/rules.py tests/test_rules.py
git commit -m "feat: platform rules engine"
```

---

### Task 4: PlatformCopy / Review 模型与迁移

**Files:**
- Modify: `app/storage/models.py`
- Test: `tests/test_models_state.py`（追加）
- 迁移：`alembic revision --autogenerate -m "add platform_copy and review"` + `upgrade head`

**Interfaces:**
- Produces: `CopyStatus`（pending/adapted/reviewed）、`Verdict`（pending/pass/reject）、`PlatformCopy`、`Review`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_models_state.py（追加）
from app.storage.models import CopyStatus, PlatformCopy, Review, Verdict


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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_models_state.py -v`
Expected: FAIL，`cannot import name 'PlatformCopy'`

- [ ] **Step 3: 最小实现**

```python
# app/storage/models.py（追加）
class CopyStatus(StrEnum):
    PENDING = "pending"
    ADAPTED = "adapted"
    REVIEWED = "reviewed"


class Verdict(StrEnum):
    PENDING = "pending"
    PASS = "pass"
    REJECT = "reject"


class PlatformCopy(Base):
    __tablename__ = "platform_copy"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    summary_id: Mapped[int] = mapped_column(ForeignKey("summary.id"), index=True)
    platform: Mapped[str] = mapped_column(String(16), index=True)
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default=CopyStatus.PENDING, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Review(Base):
    __tablename__ = "review"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    copy_id: Mapped[int] = mapped_column(ForeignKey("platform_copy.id"), unique=True, index=True)
    verdict: Mapped[str] = mapped_column(String(16), default=Verdict.PENDING)
    scores: Mapped[dict] = mapped_column(JSON)
    comment: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
```

```bash
New-Item -ItemType Directory -Force -Path data | Out-Null
.\.venv\Scripts\python.exe -m alembic revision --autogenerate -m "add platform_copy and review"
.\.venv\Scripts\python.exe -m alembic upgrade head
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_models_state.py -v`
Expected: PASS（6 passed）

- [ ] **Step 5: 提交**

```bash
git add app/storage/models.py tests/test_models_state.py migrations
git commit -m "feat: platform_copy and review models"
```

---

### Task 5: LLM 改写与回退

**Files:**
- Create: `app/adapter/copywriter.py`
- Test: `tests/test_copywriter.py`

**Interfaces:**
- Produces: `CopyResult(text, source)`；`async generate_copy(provider, summary, platform) -> CopyResult`；`_fallback(summary, platform)`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_copywriter.py
from app.adapter.copywriter import generate_copy
from app.adapter.platforms import PlatformConfig
from app.llm.provider import LLMError
from app.storage.models import Summary


class FakeProvider:
    def __init__(self, content: str | Exception):
        self.content = content

    async def chat(self, messages, temperature=0.7):
        if isinstance(self.content, Exception):
            raise self.content
        return self.content


def _summary():
    return Summary(
        id=1,
        article_id=1,
        summary_text="张三团队发布人工智能研究成果。",
        key_points=["要点一", "要点二", "要点三"],
        short_title="研究成果",
        scores={},
        status="summarized",
    )


def _platform():
    return PlatformConfig(id="weibo", name="微博", min_chars=1, max_chars=140, min_tags=1, max_tags=3, style_prompt="微博风格")


async def test_generate_copy_llm_path():
    provider = FakeProvider('{"text": "今日热点：#科技# 研究成果发布。"}')
    result = await generate_copy(provider, _summary(), _platform())
    assert result.source == "llm"
    assert "#科技#" in result.text


async def test_generate_copy_falls_back_on_error():
    provider = FakeProvider(LLMError("boom"))
    result = await generate_copy(provider, _summary(), _platform())
    assert result.source == "fallback"
    assert "研究成果" in result.text


async def test_generate_copy_enforces_max_chars():
    provider = FakeProvider('{"text": "' + "很长的内容" * 100 + '"}')
    result = await generate_copy(provider, _summary(), _platform())
    assert len(result.text) <= 140
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_copywriter.py -v`
Expected: FAIL，`ModuleNotFoundError: app.adapter.copywriter`

- [ ] **Step 3: 最小实现**

```python
# app/adapter/copywriter.py
import json
from dataclasses import dataclass

from app.adapter.platforms import PlatformConfig
from app.llm.provider import ChatMessage, LLMError
from app.storage.models import Summary


@dataclass
class CopyResult:
    text: str
    source: str


def _enforce_max(text: str, max_chars: int) -> str:
    return text[:max_chars]


def _fallback(summary: Summary, platform: PlatformConfig) -> CopyResult:
    base = f"{summary.short_title}：{summary.summary_text}"
    if platform.max_tags:
        tags = " ".join(f"#{k}#" for k in (summary.key_points or [])[: platform.max_tags])
        text = base + "\n" + tags
    else:
        text = base
    return CopyResult(_enforce_max(text, platform.max_chars), "fallback")


async def generate_copy(provider, summary: Summary, platform: PlatformConfig) -> CopyResult:
    if provider is None:
        return _fallback(summary, platform)
    prompt = (
        f"{platform.style_prompt}\n"
        f"标题：{summary.short_title}\n摘要：{summary.summary_text}\n要点：{'；'.join(summary.key_points or []) or '无'}\n"
        f"字数要求：{platform.min_chars}-{platform.max_chars}字。"
        f"只输出 JSON：{{\"text\": string}}"
    )
    try:
        raw = await provider.chat([ChatMessage("user", prompt)])
        data = json.loads(raw)
        text = str(data["text"]).strip()
        if not text:
            raise ValueError("empty text")
        return CopyResult(_enforce_max(text, platform.max_chars), "llm")
    except (LLMError, ValueError, json.JSONDecodeError, KeyError):
        return _fallback(summary, platform)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_copywriter.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add app/adapter/copywriter.py tests/test_copywriter.py
git commit -m "feat: platform copywriter with llm and fallback"
```

---

### Task 6: 合规校验

**Files:**
- Create: `app/adapter/compliance.py`
- Test: `tests/test_compliance.py`

**Interfaces:**
- Produces: `check_compliance(text, sensitive_words, ad_words) -> dict`（`sensitive_hits`、`ad_hits`）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_compliance.py
from app.adapter.compliance import check_compliance


def test_check_compliance():
    result = check_compliance("全网最低价，加微信详聊", ["加微信"], ["全网最低"])
    assert result["sensitive_hits"] == ["加微信"]
    assert result["ad_hits"] == ["全网最低"]
    assert check_compliance("正常内容", [], [])["sensitive_hits"] == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_compliance.py -v`
Expected: FAIL，`ModuleNotFoundError: app.adapter.compliance`

- [ ] **Step 3: 最小实现**

```python
# app/adapter/compliance.py
from app.adapter.wordlists import find_hits


def check_compliance(text: str, sensitive_words: list[str], ad_words: list[str]) -> dict:
    return {"sensitive_hits": find_hits(text, sensitive_words), "ad_hits": find_hits(text, ad_words)}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_compliance.py -v`
Expected: PASS（1 passed）

- [ ] **Step 5: 提交**

```bash
git add app/adapter/compliance.py tests/test_compliance.py
git commit -m "feat: compliance checks"
```

---

### Task 7: 审核评分

**Files:**
- Create: `app/reviewer/__init__.py`、`app/reviewer/quality.py`
- Test: `tests/test_reviewer_quality.py`

**Interfaces:**
- Produces: `score_copy(platform, text, compliance) -> dict`（含 `style_score` 0-100）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_reviewer_quality.py
from app.adapter.platforms import PlatformConfig
from app.reviewer.quality import score_copy


def test_score_copy_clean_text():
    platform = PlatformConfig(id="weibo", name="微博", min_chars=1, max_chars=140, min_tags=1, max_tags=3)
    scores = score_copy(platform, "今日热点：#科技# 核心信息。", {"sensitive_hits": [], "ad_hits": []})
    assert scores["style_score"] == 100
    assert scores["length_ok"] is True


def test_score_copy_penalizes_violations():
    platform = PlatformConfig(id="weibo", name="微博", min_chars=1, max_chars=140, min_tags=1, max_tags=3)
    scores = score_copy(platform, "太短没标签", {"sensitive_hits": ["加微信"], "ad_hits": ["最佳"]})
    assert scores["style_score"] < 80
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_reviewer_quality.py -v`
Expected: FAIL，`ModuleNotFoundError: app.reviewer.quality`

- [ ] **Step 3: 最小实现**

```python
# app/reviewer/quality.py
from app.adapter.platforms import PlatformConfig
from app.adapter.rules import validate_text


def score_copy(platform: PlatformConfig, text: str, compliance: dict) -> dict:
    rules = validate_text(platform, text)
    style = 100
    if not rules.length_ok:
        style -= 20
    if not rules.tags_ok:
        style -= 15
    if not rules.emojis_ok:
        style -= 15
    style -= 10 * min(len(compliance["sensitive_hits"]), 3)
    style -= 10 * min(len(compliance["ad_hits"]), 3)
    style = max(0, style)
    return {
        "length": rules.length,
        "length_ok": rules.length_ok,
        "tags": rules.tags,
        "tags_ok": rules.tags_ok,
        "emojis": rules.emojis,
        "emojis_ok": rules.emojis_ok,
        "sensitive_hits": compliance["sensitive_hits"],
        "ad_hits": compliance["ad_hits"],
        "style_score": style,
    }
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_reviewer_quality.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: 提交**

```bash
git add app/reviewer tests/test_reviewer_quality.py
git commit -m "feat: review scoring"
```

---

### Task 8: 适配与审核服务 + 事件接线

**Files:**
- Create: `app/adapter/service.py`、`app/reviewer/service.py`
- Modify: `app/worker.py`
- Test: `tests/test_adapter_service.py`、`tests/test_reviewer_service.py`

**Interfaces:**
- Produces:
  - `AdapterService(settings, redis, provider=None, platforms=None)`：`async adapt_summary(session, summary_id) -> list[int]`
  - `register_adapter_handlers(registry, settings, redis, provider=None)`：注册 `summary.generated`
  - `ReviewerService(settings, redis, platforms=None)`：`async review_copy(session, copy_id) -> Review`
  - `register_reviewer_handlers(registry, settings, redis)`：注册 `copy.adapted`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_adapter_service.py
from sqlalchemy import select

from app.adapter.platforms import PlatformConfig
from app.adapter.service import AdapterService, register_adapter_handlers
from app.storage.models import Article, ArticleStatus, PlatformCopy, Summary, SummaryStatus


class FakeProvider:
    async def chat(self, messages, temperature=0.7):
        return '{"text": "今日分享：#科技# 研究成果发布，非常实用。"}'


def _platforms():
    return {"weibo": PlatformConfig(id="weibo", name="微博", min_chars=1, max_chars=140, min_tags=1, max_tags=3)}


async def test_adapt_summary_creates_copies_and_advances_article(session_factory, redis, settings):
    session = session_factory()
    art = Article(url="https://x/ad1", title="t", text="正文", content_hash="c", simhash_value=1, status=ArticleStatus.SUMMARIZED)
    session.add(art)
    session.flush()
    summary = Summary(
        article_id=art.id,
        summary_text="张三团队发布研究成果。",
        key_points=["要点一", "要点二"],
        short_title="成果",
        scores={},
        status=SummaryStatus.SUMMARIZED,
    )
    session.add(summary)
    session.commit()
    service = AdapterService(settings, redis, provider=FakeProvider(), platforms=_platforms())
    copy_ids = await service.adapt_summary(session, summary.id)
    assert len(copy_ids) == 1
    copy = session.get(PlatformCopy, copy_ids[0])
    assert copy.platform == "weibo"
    assert copy.status == "adapted"
    session.refresh(art)
    assert art.status == ArticleStatus.ADAPTED
    assert await redis.xlen(settings.event_stream) == 1
    # 幂等：再次调用不重复创建
    assert await service.adapt_summary(session, summary.id) == []
    assert len(session.scalars(select(PlatformCopy)).all()) == 1
    session.close()


async def test_register_adapter_handlers(session_factory, redis, settings):
    from app.orchestrator.registry import SkillRegistry

    registry = SkillRegistry()
    register_adapter_handlers(registry, settings, redis, provider=FakeProvider())
    assert registry.has("summary.generated")
```

```python
# tests/test_reviewer_service.py
from sqlalchemy import select

from app.adapter.platforms import PlatformConfig
from app.reviewer.service import ReviewerService, register_reviewer_handlers
from app.storage.models import Article, ArticleStatus, PlatformCopy, Review, Summary, SummaryStatus, Verdict


def _platforms():
    return {"weibo": PlatformConfig(id="weibo", name="微博", min_chars=1, max_chars=140, min_tags=1, max_tags=3)}


async def _seed(session_factory):
    session = session_factory()
    art = Article(url="https://x/rv1", title="t", text="正文", content_hash="c", simhash_value=1, status=ArticleStatus.ADAPTED)
    session.add(art)
    session.flush()
    summary = Summary(
        article_id=art.id,
        summary_text="摘要内容" * 20,
        key_points=["要点一", "要点二"],
        short_title="标题",
        scores={},
        status=SummaryStatus.SUMMARIZED,
    )
    session.add(summary)
    session.flush()
    copy = PlatformCopy(summary_id=summary.id, platform="weibo", text="今日热点：#科技# 核心信息。", status="adapted")
    session.add(copy)
    session.commit()
    return session, art, copy


async def test_review_copy_writes_review_and_advances_article(session_factory, redis, settings):
    session, art, copy = await _seed(session_factory)
    service = ReviewerService(settings, redis, platforms=_platforms())
    review = await service.review_copy(session, copy.id)
    assert review.verdict == Verdict.PENDING
    session.refresh(copy)
    assert copy.status == "reviewed"
    session.refresh(art)
    assert art.status == ArticleStatus.REVIEWED
    assert await redis.xlen(settings.event_stream) == 1
    session.close()


async def test_register_reviewer_handlers(session_factory, redis, settings):
    from app.orchestrator.registry import SkillRegistry

    registry = SkillRegistry()
    register_reviewer_handlers(registry, settings, redis)
    assert registry.has("copy.adapted")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_adapter_service.py tests/test_reviewer_service.py -v`
Expected: FAIL，`ModuleNotFoundError: app.adapter.service`

- [ ] **Step 3: 最小实现**

```python
# app/adapter/service.py
from sqlalchemy import select

from app.adapter.copywriter import generate_copy
from app.adapter.platforms import load_platforms
from app.adapter.wordlists import DEFAULT_AD_WORDS, DEFAULT_SENSITIVE_WORDS, load_wordlist
from app.config import Settings
from app.orchestrator.registry import SkillRegistry
from app.orchestrator.state import transition
from app.storage.models import Article, ArticleStatus, CopyStatus, PlatformCopy, Summary
from app.storage.queue import emit_event


class AdapterService:
    def __init__(self, settings: Settings, redis, provider=None, platforms=None) -> None:
        self.settings = settings
        self.redis = redis
        self.provider = provider
        self.platforms = platforms if platforms is not None else load_platforms(settings.platforms_file)
        self.sensitive_words = DEFAULT_SENSITIVE_WORDS + load_wordlist(settings.sensitive_words_file)
        self.ad_words = DEFAULT_AD_WORDS + load_wordlist(settings.ad_words_file)

    async def adapt_summary(self, session, summary_id: int) -> list[int]:
        summary = session.get(Summary, summary_id)
        if summary is None:
            raise ValueError(f"unknown summary_id: {summary_id}")
        article = session.get(Article, summary.article_id)
        copy_ids: list[int] = []
        for platform_id, platform in self.platforms.items():
            exists = session.scalar(
                select(PlatformCopy.id).where(PlatformCopy.summary_id == summary.id, PlatformCopy.platform == platform_id)
            )
            if exists is not None:
                continue
            result = await generate_copy(self.provider, summary, platform)
            copy = PlatformCopy(summary_id=summary.id, platform=platform_id, text=result.text, status=CopyStatus.ADAPTED)
            session.add(copy)
            session.flush()
            await emit_event(self.redis, session, "copy.adapted", {"copy_id": copy.id}, self.settings.event_stream)
            copy_ids.append(copy.id)
        if ArticleStatus(article.status) == ArticleStatus.SUMMARIZED:
            transition(ArticleStatus(article.status), ArticleStatus.ADAPTED)
            article.status = ArticleStatus.ADAPTED
        session.commit()
        return copy_ids


def register_adapter_handlers(registry: SkillRegistry, settings: Settings, redis, provider=None) -> None:
    service = AdapterService(settings, redis, provider=provider)

    async def on_summary_generated(payload: dict, session) -> None:
        await service.adapt_summary(session, payload["summary_id"])

    registry.register("summary.generated", on_summary_generated)
```

```python
# app/reviewer/service.py
from sqlalchemy import select

from app.adapter.compliance import check_compliance
from app.adapter.platforms import load_platforms
from app.adapter.wordlists import DEFAULT_AD_WORDS, DEFAULT_SENSITIVE_WORDS, load_wordlist
from app.config import Settings
from app.orchestrator.registry import SkillRegistry
from app.orchestrator.state import transition
from app.reviewer.quality import score_copy
from app.storage.models import Article, ArticleStatus, CopyStatus, PlatformCopy, Review, Summary, Verdict
from app.storage.queue import emit_event


class ReviewerService:
    def __init__(self, settings: Settings, redis, platforms=None) -> None:
        self.settings = settings
        self.redis = redis
        self.platforms = platforms if platforms is not None else load_platforms(settings.platforms_file)
        self.sensitive_words = DEFAULT_SENSITIVE_WORDS + load_wordlist(settings.sensitive_words_file)
        self.ad_words = DEFAULT_AD_WORDS + load_wordlist(settings.ad_words_file)

    async def review_copy(self, session, copy_id: int) -> Review:
        copy = session.get(PlatformCopy, copy_id)
        if copy is None:
            raise ValueError(f"unknown copy_id: {copy_id}")
        platform = self.platforms.get(copy.platform)
        if platform is None:
            raise ValueError(f"unknown platform: {copy.platform}")
        compliance = check_compliance(copy.text, self.sensitive_words, self.ad_words)
        scores = score_copy(platform, copy.text, compliance)
        review = Review(copy_id=copy.id, verdict=Verdict.PENDING, scores=scores)
        session.add(review)
        session.flush()
        copy.status = CopyStatus.REVIEWED
        summary = session.get(Summary, copy.summary_id)
        article = session.get(Article, summary.article_id)
        copies = session.scalars(select(PlatformCopy).where(PlatformCopy.summary_id == summary.id)).all()
        if all(c.status == CopyStatus.REVIEWED for c in copies) and ArticleStatus(article.status) in (
            ArticleStatus.ADAPTED,
            ArticleStatus.SUMMARIZED,
        ):
            transition(ArticleStatus(article.status), ArticleStatus.REVIEWED)
            article.status = ArticleStatus.REVIEWED
        await emit_event(
            self.redis,
            session,
            "review.passed",
            {"review_id": review.id, "copy_id": copy.id},
            self.settings.event_stream,
        )
        session.commit()
        return review


def register_reviewer_handlers(registry: SkillRegistry, settings: Settings, redis) -> None:
    service = ReviewerService(settings, redis)

    async def on_copy_adapted(payload: dict, session) -> None:
        await service.review_copy(session, payload["copy_id"])

    registry.register("copy.adapted", on_copy_adapted)
```

```python
# app/worker.py（main 中追加）
from app.adapter.service import register_adapter_handlers
from app.reviewer.service import register_reviewer_handlers

# main() 内：
    register_processor_handlers(registry, settings, redis)
    register_adapter_handlers(registry, settings, redis)
    register_reviewer_handlers(registry, settings, redis)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_adapter_service.py tests/test_reviewer_service.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: 提交**

```bash
git add app/adapter/service.py app/reviewer/service.py app/worker.py tests/test_adapter_service.py tests/test_reviewer_service.py
git commit -m "feat: adapter and reviewer services with event wiring"
```

---

### Task 9: 端到端集成测试

**Files:**
- Create: `tests/test_integration_adapter.py`

**Interfaces:**
- Consumes: `build_registry`、`register_processor_handlers`、`register_adapter_handlers`、`register_reviewer_handlers`、`run_once`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_integration_adapter.py
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_integration_adapter.py -v`
Expected: FAIL（handler 缺失或表缺失）

- [ ] **Step 3: 若前序任务已完成，此步骤无新增代码；如失败则回到对应任务修复**

Expected: 无需实现，直接进入 Step 4。

- [ ] **Step 4: 运行全部测试确认通过**

Run: `.\.venv\Scripts\python.exe -m pytest -v`
Expected: ALL PASS（S1+S2+S3 全部）

Run: `$env:COVERAGE_FILE=Join-Path $env:TEMP 's3.coverage'; .\.venv\Scripts\python.exe -m coverage run --source=app -m pytest -q --ignore=tests/test_entities.py; .\.venv\Scripts\python.exe -m coverage report --omit='app/cli.py,app/worker.py' --fail-under=80`
Expected: app 核心覆盖率 ≥80%

- [ ] **Step 5: 提交**

```bash
git add tests/test_integration_adapter.py
git commit -m "test: adapter and reviewer end-to-end integration"
```

---

### Task 10: 文档与配置收尾

**Files:**
- Modify: `README.md`、`.env.example`

- [ ] **Step 1: 更新文档**

```markdown
# README.md 追加
## S3：内容适配 + 质量审核
- 事件：`summary.generated` → 三平台文案 `platform_copy` → `copy.adapted` → 自动评分 `review`（verdict=pending 待人工）
- 平台规范：微博 1~140 字+1~3 标签；朋友圈 60~200 字+emoji；小红书 100~500 字+2~5 标签+emoji（见 `platforms.yaml`）
- 合规：敏感词/广告法违禁词命中即标记；可配置 `ASSISTANT_SENSITIVE_WORDS_FILE` / `ASSISTANT_AD_WORDS_FILE`
- 审核：`style_score` 0-100（≥80 视为 4/5），所有文案默认进入待人工审核
```

```text
# .env.example（追加）
ASSISTANT_PLATFORMS_FILE=platforms.yaml
# ASSISTANT_SENSITIVE_WORDS_FILE=data/sensitive_words.txt
# ASSISTANT_AD_WORDS_FILE=data/ad_words.txt
```

- [ ] **Step 2: 运行完整验证**

Run: `.\.venv\Scripts\python.exe -m pytest -q`
Expected: ALL PASS

Run: `.\.venv\Scripts\python.exe -m ruff check --no-cache app tests`
Expected: All checks passed

- [ ] **Step 3: 提交**

```bash
git add README.md .env.example
git commit -m "docs: s3 content adaptation usage"
```

---

## Self-Review 结论（计划编写者自查）

1. **Spec 覆盖**：平台规范/规则引擎/词表/LLM 改写/合规/评分/模型/事件链/worker 注册/集成测试均有对应任务；幂等与状态迁移约束在 Task 8 实现并测试。
2. **占位符扫描**：无 TBD/TODO；每个代码步骤含完整可运行代码。
3. **类型一致性**：`PlatformConfig` 字段、`CopyResult(text, source)`、`check_compliance` 返回键、`score_copy` 返回键、`Review`/`PlatformCopy` 字段在跨任务一致；handler 注册函数签名在 Task 8/9 一致。
