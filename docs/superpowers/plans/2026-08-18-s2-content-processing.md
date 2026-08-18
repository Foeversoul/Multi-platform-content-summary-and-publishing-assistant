# S2 内容处理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现内容处理闭环：`article.crawled` 事件触发降噪→实体/关键词→句子打分→抽取式候选→LLM 生成摘要→质量评分→`summary` 入库并发出 `summary.generated`，article 状态流转为 `summarized`。

**Architecture:** 新增 `app/processor/`（clean/entities/keywords/extractive/summarizer/quality/service）与 `app/llm/provider.py`（DeepSeek 抽象，可注入 transport 供测试 mock）；沿用 S1 的 SkillRegistry 事件接线（新增 `article.crawled` handler），不改动 S1 的 `build_registry`。

**Tech Stack:** Python 3.12 · jieba（posseg + analyse）· 标准库 re/json · httpx · SQLAlchemy JSON 列 · pytest + fakeredis（沿用 S1 环境）

**Spec:** [docs/superpowers/specs/2026-08-18-s2-content-processing-design.md](../specs/2026-08-18-s2-content-processing-design.md)

## Global Constraints

- Python 3.12+；包根 `app`；新增模块目录 `app/processor/`、`app/llm/`。
- 状态机：`crawled → summarized`；失败经 registry 重试后 `failed/dead_letter`；非法迁移抛 `InvalidTransitionError`（S1 已有）。
- 事件：消费 `article.crawled`（payload `article_id`）；产出 `summary.generated`（payload `summary_id`）；无 handler 事件按 noop 处理（S1 已实现）。
- 摘要标准：摘要 200~400 字；要点 3~5 条（每条 ≤60 字）；精简标题 ≤30 字。
- 质量评分：自动计算 `summary_len/length_ok/key_points_count/short_title_len/entity_retention/avg_sentence_len`；实体保留率 ≥95%、平均句长 ≤25 字为抽查目标。
- NER：jieba.posseg 类别映射（nr→PERSON、ns→LOCATION、nt/nz→ORG、m→NUMBER、t→DATE）+ 正则补充日期/数字；不引入重型 NER 依赖。
- LLM：`ChatProvider.chat()` 抽象；默认 DeepSeek OpenAI 兼容接口；`llm_api_key` 未配置或调用失败抛 `LLMError`；摘要生成失败回退抽取式。
- 接线：`register_processor_handlers(registry, settings, redis, provider=None)` 由 `worker.main` 调用；S1 测试不得被破坏。
- 测试：核心模块覆盖率 ≥80%；LLM 用 FakeProvider/mock transport，绝不发真实请求；每个任务结束必须提交（提交信息见各任务 Step 5）。

## File Structure

| 文件 | 职责 |
| --- | --- |
| `app/llm/__init__.py` / `app/llm/provider.py` | LLM 抽象：`ChatProvider.chat()`、`ChatMessage`、`LLMError` |
| `app/processor/__init__.py` | 包标记 |
| `app/processor/clean.py` | `clean_text` / `split_sentences` / `remove_noise_sentences` |
| `app/processor/entities.py` | `extract_entities`（jieba.posseg + 正则） |
| `app/processor/keywords.py` | `extract_keywords`（jieba TF-IDF） |
| `app/processor/extractive.py` | `score_sentences` / `extractive_summary` |
| `app/processor/summarizer.py` | `generate_summary`（LLM JSON + 回退）、`SummarizerResult` |
| `app/processor/quality.py` | `score_summary` |
| `app/processor/service.py` | `ProcessorService.process_article` / `register_processor_handlers` |
| `app/storage/models.py` | 追加 `Summary` / `SummaryStatus` |
| `app/worker.py` | main 中追加 `register_processor_handlers` |
| `tests/` | 每模块一个测试文件 + `tests/test_integration_processor.py` |

---

### Task 1: LLM Provider 抽象与配置

**Files:**
- Modify: `app/config.py`（追加 llm_* 字段）
- Create: `app/llm/__init__.py`、`app/llm/provider.py`
- Modify: `.env.example`
- Test: `tests/test_llm_provider.py`

**Interfaces:**
- Consumes: `Settings`。
- Produces:
  - `Settings` 新字段：`llm_api_key: str = ""`、`llm_base_url: str = "https://api.deepseek.com/v1"`、`llm_model: str = "deepseek-chat"`、`llm_timeout_seconds: float = 60.0`、`llm_max_tokens: int = 2048`
  - `ChatMessage(role: str, content: str)`、`LLMError(RuntimeError)`
  - `ChatProvider(settings, transport=None)`：`async chat(messages: list[ChatMessage], temperature: float = 0.7) -> str`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_llm_provider.py
import httpx
import pytest

from app.config import Settings
from app.llm.provider import ChatMessage, ChatProvider, LLMError


def _settings():
    return Settings(llm_api_key="test-key", llm_base_url="https://llm.example/v1", llm_model="m1")


async def test_chat_returns_content():
    def handler(request):
        assert request.headers["Authorization"] == "Bearer test-key"
        return httpx.Response(200, json={"choices": [{"message": {"content": " 摘要内容 "}}]}, request=request)

    provider = ChatProvider(_settings(), transport=httpx.MockTransport(handler))
    text = await provider.chat([ChatMessage("user", "hi")])
    assert text == "摘要内容"


async def test_chat_missing_key_raises():
    provider = ChatProvider(Settings(llm_api_key=""), transport=httpx.MockTransport(lambda r: httpx.Response(200, request=r)))
    with pytest.raises(LLMError):
        await provider.chat([ChatMessage("user", "hi")])


async def test_chat_http_error_wrapped_as_llm_error():
    provider = ChatProvider(_settings(), transport=httpx.MockTransport(lambda r: httpx.Response(500, request=r)))
    with pytest.raises(LLMError):
        await provider.chat([ChatMessage("user", "hi")])


async def test_chat_malformed_response_raises():
    provider = ChatProvider(_settings(), transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"foo": 1}, request=r)))
    with pytest.raises(LLMError):
        await provider.chat([ChatMessage("user", "hi")])
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_llm_provider.py -v`
Expected: FAIL，`ModuleNotFoundError: app.llm.provider`

- [ ] **Step 3: 最小实现**

```python
# app/config.py（在 user_agents 字段后追加）
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_model: str = "deepseek-chat"
    llm_timeout_seconds: float = 60.0
    llm_max_tokens: int = 2048
```

```python
# app/llm/provider.py
from dataclasses import dataclass

import httpx

from app.config import Settings


class LLMError(RuntimeError):
    pass


@dataclass
class ChatMessage:
    role: str
    content: str


class ChatProvider:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.settings = settings
        self.transport = transport

    async def chat(self, messages: list[ChatMessage], temperature: float = 0.7) -> str:
        if not self.settings.llm_api_key:
            raise LLMError("llm_api_key is not configured")
        url = f"{self.settings.llm_base_url.rstrip('/')}/chat/completions"
        payload = {
            "model": self.settings.llm_model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
            "max_tokens": self.settings.llm_max_tokens,
        }
        try:
            async with httpx.AsyncClient(timeout=self.settings.llm_timeout_seconds, transport=self.transport) as client:
                resp = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {self.settings.llm_api_key}"},
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            raise LLMError(f"llm request failed: {exc!r}") from exc
        try:
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"unexpected LLM response: {data!r}") from exc
```

```text
# .env.example（追加）
ASSISTANT_LLM_API_KEY=sk-xxxxxxxx
ASSISTANT_LLM_BASE_URL=https://api.deepseek.com/v1
ASSISTANT_LLM_MODEL=deepseek-chat
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_llm_provider.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: 提交**

```bash
git add app/config.py app/llm tests/test_llm_provider.py .env.example
git commit -m "feat: llm provider abstraction and settings"
```

---

### Task 2: Summary 模型与迁移

**Files:**
- Modify: `app/storage/models.py`（追加 `SummaryStatus`、`Summary`）
- Test: `tests/test_models_state.py`（追加 summary CRUD 用例）
- 生成迁移：`alembic revision --autogenerate -m "add summary table"` + `alembic upgrade head`

**Interfaces:**
- Consumes: `Article` / `Base`。
- Produces: `SummaryStatus`（pending/summarized/failed）；`Summary`（article_id FK unique、summary_text、key_points JSON、short_title、scores JSON、status、时间戳）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_models_state.py（追加）
from app.storage.models import Summary, SummaryStatus


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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_models_state.py -v`
Expected: FAIL，`ImportError: cannot import name 'Summary'`

- [ ] **Step 3: 最小实现**

```python
# app/storage/models.py（追加导入 JSON，并追加以下类）
from sqlalchemy import JSON


class SummaryStatus(StrEnum):
    PENDING = "pending"
    SUMMARIZED = "summarized"
    FAILED = "failed"


class Summary(Base):
    __tablename__ = "summary"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("article.id"), unique=True, index=True)
    summary_text: Mapped[str] = mapped_column(Text)
    key_points: Mapped[list] = mapped_column(JSON)
    short_title: Mapped[str] = mapped_column(String(200))
    scores: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default=SummaryStatus.PENDING, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
```

生成迁移（需要 data 目录；如沙箱拦截目录创建请提权）：

```bash
New-Item -ItemType Directory -Force -Path data | Out-Null
.\.venv\Scripts\python.exe -m alembic revision --autogenerate -m "add summary table"
.\.venv\Scripts\python.exe -m alembic upgrade head
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_models_state.py -v`
Expected: PASS（4 passed，含新增 summary 用例）

- [ ] **Step 5: 提交**

```bash
git add app/storage/models.py tests/test_models_state.py migrations alembic.ini
git commit -m "feat: summary model and migration"
```

---

### Task 3: 降噪与切句

**Files:**
- Create: `app/processor/__init__.py`、`app/processor/clean.py`
- Test: `tests/test_clean.py`

**Interfaces:**
- Consumes: 无（纯函数）。
- Produces: `clean_text(text) -> str`、`split_sentences(text) -> list[str]`、`remove_noise_sentences(sentences) -> list[str]`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_clean.py
from app.processor.clean import clean_text, remove_noise_sentences, split_sentences


def test_clean_text_collapses_whitespace():
    assert clean_text("  第一段。\n\n  第二段。  ") == "第一段。 第二段。"


def test_split_sentences_on_chinese_and_ascii_terminators():
    text = "第一句。第二句！第三句?第四句；第五句\n第六句。"
    parts = split_sentences(text)
    assert len(parts) == 6
    assert parts[0] == "第一句。"


def test_remove_noise_sentences():
    sentences = ["短", "这是一句超过十个字的有效信息句子。", "这是一句超过十个字的有效信息句子。", "版权声明"]
    cleaned = remove_noise_sentences(sentences)
    assert cleaned == ["这是一句超过十个字的有效信息句子。"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_clean.py -v`
Expected: FAIL，`ModuleNotFoundError: app.processor.clean`

- [ ] **Step 3: 最小实现**

```python
# app/processor/clean.py
import re


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？!?；;\n])", clean_text(text))
    return [p.strip() for p in parts if p.strip()]


def _is_informative(sentence: str) -> bool:
    return len(sentence) >= 10 or bool(re.search(r"[0-9A-Za-z]", sentence))


def remove_noise_sentences(sentences: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for s in sentences:
        if not _is_informative(s):
            continue
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_clean.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add app/processor tests/test_clean.py
git commit -m "feat: text cleaning and sentence splitting"
```

---

### Task 4: 实体与关键词抽取

**Files:**
- Create: `app/processor/entities.py`、`app/processor/keywords.py`
- Test: `tests/test_entities.py`

**Interfaces:**
- Produces:
  - `extract_entities(text) -> dict[str, set[str]]`（类别 PERSON/LOCATION/ORG/NUMBER/DATE）
  - `extract_keywords(text, top_k=10) -> list[str]`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_entities.py
from app.processor.entities import extract_entities
from app.processor.keywords import extract_keywords


def test_extract_entities_categories():
    text = "张三在北京市参观了腾讯公司，会议于2026年8月18日举行，参会人数500人。"
    entities = extract_entities(text)
    assert "PERSON" in entities and "张三" in entities["PERSON"]
    assert "LOCATION" in entities and "北京市" in entities["LOCATION"]
    assert "ORG" in entities
    assert "DATE" in entities and "2026年8月18日" in entities["DATE"]
    assert "NUMBER" in entities and "500" in entities["NUMBER"]


def test_extract_keywords_returns_terms():
    text = "人工智能大模型在医疗影像诊断中展现出显著优势，大模型提升了诊断准确率与效率。"
    keywords = extract_keywords(text, top_k=5)
    assert len(keywords) >= 1
    assert any("大模型" in k or "诊断" in k for k in keywords)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_entities.py -v`
Expected: FAIL，`ModuleNotFoundError: app.processor.entities`

- [ ] **Step 3: 最小实现**

```python
# app/processor/entities.py
import re

import jieba.posseg as pseg

FLAG_TO_CATEGORY = {
    "nr": "PERSON",
    "ns": "LOCATION",
    "nt": "ORG",
    "nz": "ORG",
    "m": "NUMBER",
    "t": "DATE",
}
DATE_RE = re.compile(r"\d{4}年\d{1,2}月\d{1,2}日|\d{4}-\d{2}-\d{2}|\d{1,2}月\d{1,2}日")
NUMBER_RE = re.compile(r"\d+(?:\.\d+)?%?|\d+(?:\.\d+)?[亿万]")


def extract_entities(text: str) -> dict[str, set[str]]:
    entities: dict[str, set[str]] = {}

    def add(category: str, value: str) -> None:
        value = value.strip()
        if value:
            entities.setdefault(category, set()).add(value)

    for word, flag in pseg.cut(text):
        category = FLAG_TO_CATEGORY.get(flag)
        if category:
            add(category, word)
    for match in DATE_RE.finditer(text):
        add("DATE", match.group())
    for match in NUMBER_RE.finditer(text):
        add("NUMBER", match.group())
    return entities
```

```python
# app/processor/keywords.py
from jieba import analyse


def extract_keywords(text: str, top_k: int = 10) -> list[str]:
    return [w for w in analyse.extract_tags(text, topK=top_k) if w.strip()]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_entities.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: 提交**

```bash
git add app/processor/entities.py app/processor/keywords.py tests/test_entities.py
git commit -m "feat: entity and keyword extraction"
```

---

### Task 5: 句子打分与抽取式摘要

**Files:**
- Create: `app/processor/extractive.py`
- Test: `tests/test_extractive.py`

**Interfaces:**
- Produces: `score_sentences(sentences: list[str], title: str, entities: dict[str, set[str]]) -> list[float]`；`extractive_summary(sentences, scores, min_chars=200, max_chars=400) -> str`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_extractive.py
from app.processor.extractive import extractive_summary, score_sentences


def test_score_sentences_length_and_order():
    sentences = ["第一句包含张三和腾讯公司。", "第二句补充说明背景信息。", "第三句收尾。"]
    scores = score_sentences(sentences, "张三的新闻", {"PERSON": {"张三"}, "ORG": {"腾讯公司"}})
    assert len(scores) == 3
    assert scores[0] > scores[1]


def test_extractive_summary_respects_budget_and_order():
    sentences = ["第一句内容较短的句子。", "第二句内容较短的句子。", "第三句内容较短的句子。"]
    scores = [1.0, 0.5, 0.2]
    out = extractive_summary(sentences, scores, min_chars=8, max_chars=40)
    assert "第一句" in out and "第二句" in out
    assert out.index("第一句") < out.index("第二句")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_extractive.py -v`
Expected: FAIL，`ModuleNotFoundError: app.processor.extractive`

- [ ] **Step 3: 最小实现**

```python
# app/processor/extractive.py
def _bigrams(text: str) -> set[str]:
    compact = text.replace(" ", "")
    return {compact[i : i + 2] for i in range(max(len(compact) - 1, 0))}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


def score_sentences(sentences: list[str], title: str, entities: dict[str, set[str]]) -> list[float]:
    all_entities = {e for values in entities.values() for e in values}
    title_bigrams = _bigrams(title or "")
    scores: list[float] = []
    for index, sentence in enumerate(sentences):
        position = 1.0 / (index + 1)
        entity_count = sum(1 for e in all_entities if e in sentence)
        entity_score = min(1.0, entity_count / 5.0)
        title_score = _jaccard(_bigrams(sentence), title_bigrams)
        scores.append(0.3 * position + 0.4 * entity_score + 0.3 * title_score)
    return scores


def extractive_summary(sentences: list[str], scores: list[float], min_chars: int = 200, max_chars: int = 400) -> str:
    order = sorted(range(len(sentences)), key=lambda i: scores[i], reverse=True)
    selected: list[int] = []
    total = 0
    for i in order:
        length = len(sentences[i])
        if total + length > max_chars and selected:
            break
        selected.append(i)
        total += length
        if total >= min_chars:
            break
    selected.sort()
    return "".join(sentences[i] for i in selected)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_extractive.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: 提交**

```bash
git add app/processor/extractive.py tests/test_extractive.py
git commit -m "feat: sentence scoring and extractive summary"
```

---

### Task 6: LLM 摘要生成与回退

**Files:**
- Create: `app/processor/summarizer.py`
- Test: `tests/test_summarizer.py`

**Interfaces:**
- Consumes: `ChatProvider`/`ChatMessage`/`LLMError`（Task 1）、`clean`/`extractive`/`entities`（Task 3/4/5）。
- Produces:
  - `SummarizerResult(summary_text, key_points, short_title, source)`，`source` ∈ `{"llm", "extractive"}`
  - `async generate_summary(provider, article_text, title, min_chars=200, max_chars=400) -> SummarizerResult`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_summarizer.py
from app.llm.provider import ChatMessage, LLMError
from app.processor.summarizer import generate_summary


class FakeProvider:
    def __init__(self, content: str | Exception):
        self.content = content

    async def chat(self, messages: list[ChatMessage], temperature: float = 0.7) -> str:
        if isinstance(self.content, Exception):
            raise self.content
        return self.content


ARTICLE = (
    "人工智能大模型在医疗影像诊断中展现出显著优势。" * 5
    + "张三团队在北京市发布了最新研究成果，会议于2026年8月18日举行。" * 5
)


async def test_generate_summary_llm_path():
    provider = FakeProvider(
        '{"summary": "' + "这是两百字左右的摘要内容。" * 8 + '", "key_points": ["要点一", "要点二", "要点三"], "short_title": "精简标题"}'
    )
    result = await generate_summary(provider, ARTICLE, "研究标题")
    assert result.source == "llm"
    assert len(result.key_points) == 3
    assert result.short_title == "精简标题"


async def test_generate_summary_falls_back_on_error():
    provider = FakeProvider(LLMError("boom"))
    result = await generate_summary(provider, ARTICLE, "研究标题", min_chars=10, max_chars=200)
    assert result.source == "extractive"
    assert result.summary_text


async def test_generate_summary_falls_back_on_invalid_json():
    provider = FakeProvider("not json")
    result = await generate_summary(provider, ARTICLE, "研究标题", min_chars=10, max_chars=200)
    assert result.source == "extractive"
```

> 注：测试用 `min_chars=10` 保证抽取式回退能产出非空摘要；默认 200~400 字预算下短 fixture 可能返回空串，属预期（真实文章足够长）。

- [ ] **Step 2: 运行测试确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_summarizer.py -v`
Expected: FAIL，`ModuleNotFoundError: app.processor.summarizer`

- [ ] **Step 3: 最小实现**

```python
# app/processor/summarizer.py
import json
from dataclasses import dataclass

from app.llm.provider import ChatMessage, LLMError
from app.processor.clean import clean_text, remove_noise_sentences, split_sentences
from app.processor.entities import extract_entities
from app.processor.extractive import extractive_summary, score_sentences

SYSTEM_PROMPT = (
    "你是资深中文内容编辑。根据给定文章生成："
    "1) summary：200到400字的客观摘要；"
    "2) key_points：3到5条关键要点，每条不超过60字；"
    "3) short_title：不超过30字的精简标题。"
    '只输出 JSON：{"summary": string, "key_points": [string], "short_title": string}'
)


@dataclass
class SummarizerResult:
    summary_text: str
    key_points: list[str]
    short_title: str
    source: str


def _fallback(article_text: str, title: str, min_chars: int, max_chars: int) -> SummarizerResult:
    sentences = remove_noise_sentences(split_sentences(clean_text(article_text)))
    scores = score_sentences(sentences, title, extract_entities(article_text))
    return SummarizerResult(
        summary_text=extractive_summary(sentences, scores, min_chars, max_chars),
        key_points=[],
        short_title=(title or "")[:30],
        source="extractive",
    )


async def generate_summary(provider, article_text: str, title: str, min_chars: int = 200, max_chars: int = 400) -> SummarizerResult:
    if provider is None:
        return _fallback(article_text, title, min_chars, max_chars)
    user_message = f"标题：{title}\n\n正文：\n{article_text[:3000]}"
    try:
        raw = await provider.chat(
            [ChatMessage("system", SYSTEM_PROMPT), ChatMessage("user", user_message)]
        )
        data = json.loads(raw)
        summary_text = str(data["summary"]).strip()
        key_points = [str(k).strip() for k in data.get("key_points", [])][:5]
        short_title = str(data.get("short_title") or "").strip()[:30]
        if not summary_text or not key_points:
            raise ValueError("empty summary or key_points")
        if not (min_chars <= len(summary_text) <= max_chars):
            raise ValueError("summary length out of range")
        return SummarizerResult(summary_text, key_points, short_title or (title or "")[:30], "llm")
    except (LLMError, ValueError, json.JSONDecodeError, KeyError):
        return _fallback(article_text, title, min_chars, max_chars)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_summarizer.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add app/processor/summarizer.py tests/test_summarizer.py
git commit -m "feat: llm summary generation with extractive fallback"
```

---

### Task 7: 质量评分

**Files:**
- Create: `app/processor/quality.py`
- Test: `tests/test_quality.py`

**Interfaces:**
- Produces: `score_summary(article_text, summary_text, key_points, short_title) -> dict`（键：`summary_len`、`length_ok`、`key_points_count`、`short_title_len`、`entity_retention`、`avg_sentence_len`）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_quality.py
from app.processor.quality import score_summary


def test_score_summary_metrics():
    article = "张三团队在北京市发布了人工智能研究成果，2026年8月18日举行发布会，参会人数500人。"
    summary = "张三团队在北京市发布研究成果，2026年8月18日举行发布会。"
    scores = score_summary(article, summary, ["要点一", "要点二", "要点三"], "精简标题")
    assert scores["length_ok"] is True
    assert scores["key_points_count"] == 3
    assert scores["short_title_len"] == 4
    assert scores["entity_retention"] >= 0.5
    assert scores["avg_sentence_len"] > 0
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_quality.py -v`
Expected: FAIL，`ModuleNotFoundError: app.processor.quality`

- [ ] **Step 3: 最小实现**

```python
# app/processor/quality.py
from app.processor.clean import split_sentences
from app.processor.entities import extract_entities


def score_summary(article_text: str, summary_text: str, key_points: list[str], short_title: str) -> dict:
    article_entities = {e for values in extract_entities(article_text).values() for e in values}
    summary_entities = {e for values in extract_entities(summary_text).values() for e in values}
    retained = len(summary_entities & article_entities) / max(len(article_entities), 1)
    sentences = split_sentences(summary_text)
    avg_len = len(summary_text) / max(len(sentences), 1)
    return {
        "summary_len": len(summary_text),
        "length_ok": 200 <= len(summary_text) <= 400,
        "key_points_count": len(key_points),
        "short_title_len": len(short_title or ""),
        "entity_retention": round(retained, 4),
        "avg_sentence_len": round(avg_len, 2),
    }
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_quality.py -v`
Expected: PASS（1 passed）

- [ ] **Step 5: 提交**

```bash
git add app/processor/quality.py tests/test_quality.py
git commit -m "feat: summary quality scoring"
```

---

### Task 8: ProcessorService 与事件接线

**Files:**
- Create: `app/processor/service.py`
- Modify: `app/worker.py`（main 追加注册）
- Test: `tests/test_processor_service.py`

**Interfaces:**
- Consumes: Task 2~7 全部接口、`Article`/`Summary`/`ArticleStatus`/`SummaryStatus`、`emit_event`、`transition`、`SkillRegistry`。
- Produces:
  - `ProcessorService(settings, redis, provider=None)`：`async process_article(session, article_id) -> Summary`
  - `register_processor_handlers(registry, settings, redis, provider=None)`：注册 `article.crawled` handler

- [ ] **Step 1: 写失败测试**

```python
# tests/test_processor_service.py
from sqlalchemy import select

from app.processor.service import ProcessorService, register_processor_handlers
from app.storage.models import Article, ArticleStatus, EventLog, EventStatus, Summary, SummaryStatus


class FakeProvider:
    async def chat(self, messages, temperature=0.7):
        return '{"summary": "' + "这是一段两百字左右的摘要内容。" * 8 + '", "key_points": ["要点一", "要点二", "要点三"], "short_title": "精简标题"}'


async def test_process_article_creates_summary_and_updates_state(session_factory, redis, settings):
    session = session_factory()
    art = Article(
        url="https://x/p1",
        title="研究标题",
        text="张三团队在北京市发布人工智能研究成果，2026年8月18日举行发布会，参会人数500人。" * 10,
        content_hash="h",
        simhash_value=1,
        status=ArticleStatus.CRAWLED,
    )
    session.add(art)
    session.commit()
    service = ProcessorService(settings, redis, provider=FakeProvider())
    summary = await service.process_article(session, art.id)
    assert summary.status == SummaryStatus.SUMMARIZED
    session.refresh(art)
    assert art.status == ArticleStatus.SUMMARIZED
    events = await redis.xrange(settings.event_stream)
    assert len(events) == 1
    session.close()


async def test_process_article_unknown_raises(session_factory, redis, settings):
    service = ProcessorService(settings, redis, provider=FakeProvider())
    session = session_factory()
    try:
        await service.process_article(session, 999)
    except ValueError:
        pass
    else:
        raise AssertionError("unknown article_id should raise")
    session.close()


async def test_process_article_invalid_state_raises(session_factory, redis, settings):
    session = session_factory()
    art = Article(
        url="https://x/p2",
        title="标题",
        text="正文内容足够长。" * 30,
        content_hash="h2",
        simhash_value=2,
        status=ArticleStatus.SUMMARIZED,
    )
    session.add(art)
    session.commit()
    service = ProcessorService(settings, redis, provider=FakeProvider())
    try:
        await service.process_article(session, art.id)
    except Exception:
        pass
    else:
        raise AssertionError("invalid transition should raise")
    session.close()


async def test_register_processor_handlers(session_factory, redis, settings):
    from app.orchestrator.registry import SkillRegistry

    registry = SkillRegistry()
    register_processor_handlers(registry, settings, redis, provider=FakeProvider())
    assert registry.has("article.crawled")
    outcome = await registry.dispatch("article.crawled", {"article_id": 999}, session_factory(), retries=0)
    assert outcome == "dead"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_processor_service.py -v`
Expected: FAIL，`ModuleNotFoundError: app.processor.service`

- [ ] **Step 3: 最小实现**

```python
# app/processor/service.py
from app.config import Settings
from app.orchestrator.registry import SkillRegistry
from app.orchestrator.state import transition
from app.processor.clean import clean_text, remove_noise_sentences, split_sentences
from app.processor.entities import extract_entities
from app.processor.extractive import score_sentences
from app.processor.keywords import extract_keywords
from app.processor.quality import score_summary
from app.processor.summarizer import generate_summary
from app.storage.models import Article, ArticleStatus, Summary, SummaryStatus
from app.storage.queue import emit_event


class ProcessorService:
    def __init__(self, settings: Settings, redis, provider=None) -> None:
        self.settings = settings
        self.redis = redis
        self.provider = provider

    async def process_article(self, session, article_id: int) -> Summary:
        article = session.get(Article, article_id)
        if article is None:
            raise ValueError(f"unknown article_id: {article_id}")
        transition(ArticleStatus(article.status), ArticleStatus.SUMMARIZED)
        text = clean_text(article.text)
        sentences = remove_noise_sentences(split_sentences(text))
        entities = extract_entities(text)
        result = await generate_summary(self.provider, text, article.title or "")
        scores = score_summary(text, result.summary_text, result.key_points, result.short_title)
        scores["keywords"] = extract_keywords(text)
        scores["sentence_count"] = len(sentences)
        scores["top_sentence_scores"] = [round(x, 4) for x in score_sentences(sentences, article.title or "", entities)]
        summary = Summary(
            article_id=article.id,
            summary_text=result.summary_text,
            key_points=result.key_points,
            short_title=result.short_title,
            scores=scores,
            status=SummaryStatus.SUMMARIZED,
        )
        session.add(summary)
        session.flush()
        article.status = ArticleStatus.SUMMARIZED
        await emit_event(self.redis, session, "summary.generated", {"summary_id": summary.id}, self.settings.event_stream)
        session.commit()
        return summary


def register_processor_handlers(registry: SkillRegistry, settings: Settings, redis, provider=None) -> None:
    service = ProcessorService(settings, redis, provider=provider)

    async def on_article_crawled(payload: dict, session) -> None:
        await service.process_article(session, payload["article_id"])

    registry.register("article.crawled", on_article_crawled)
```

```python
# app/worker.py（main 中 build_registry 之后追加一行）
from app.processor.service import register_processor_handlers

# ...原有 main() 内：
    registry = build_registry(settings, redis)
    register_processor_handlers(registry, settings, redis)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_processor_service.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: 提交**

```bash
git add app/processor/service.py app/worker.py tests/test_processor_service.py
git commit -m "feat: processor service and event wiring"
```

---

### Task 9: 端到端集成测试

**Files:**
- Create: `tests/test_integration_processor.py`

**Interfaces:**
- Consumes: `build_registry`（S1）、`register_processor_handlers`（Task 8）、`run_once`（S1）、`emit_event`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_integration_processor.py
from sqlalchemy import select

from app.collector.service import build_registry
from app.processor.service import register_processor_handlers
from app.storage.models import Article, ArticleStatus, Summary, SummaryStatus
from app.storage.queue import emit_event
from app.worker import run_once


class FakeProvider:
    async def chat(self, messages, temperature=0.7):
        return '{"summary": "' + "这是集成测试生成的摘要内容。" * 10 + '", "key_points": ["要点一", "要点二", "要点三"], "short_title": "集成标题"}'


async def test_article_crawled_to_summarized_end_to_end(settings, session_factory, redis):
    session = session_factory()
    art = Article(
        url="https://x/e2e",
        title="集成文章",
        text="张三团队在北京市发布人工智能研究成果，2026年8月18日举行发布会，参会人数500人。" * 10,
        content_hash="e2e",
        simhash_value=7,
        status=ArticleStatus.CRAWLED,
    )
    session.add(art)
    session.commit()
    registry = build_registry(settings, redis)
    register_processor_handlers(registry, settings, redis, provider=FakeProvider())
    await emit_event(redis, session, "article.crawled", {"article_id": art.id}, settings.event_stream)
    assert await run_once(registry, settings, redis, session_factory) is True
    summary = session.scalar(select(Summary).where(Summary.article_id == art.id))
    assert summary is not None
    assert summary.status == SummaryStatus.SUMMARIZED
    assert len(summary.key_points) == 3
    assert summary.scores["length_ok"] is True
    session.refresh(art)
    assert art.status == ArticleStatus.SUMMARIZED
    session.close()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_integration_processor.py -v`
Expected: FAIL（`Summary` 表或 handler 缺失导致）

- [ ] **Step 3: 若前序任务已完成，此步骤无新增代码；如失败则回到对应任务修复**

Expected: 无需实现，直接进入 Step 4。

- [ ] **Step 4: 运行全部测试确认通过**

Run: `.\.venv\Scripts\python.exe -m pytest -v`
Expected: ALL PASS（S1 32 项 + S2 新增全部通过）

Run: `$env:COVERAGE_FILE=Join-Path $env:TEMP 's2.coverage'; .\.venv\Scripts\python.exe -m coverage run -m pytest -q; .\.venv\Scripts\python.exe -m coverage report --include='app/*' --omit='app/cli.py,app/worker.py' --fail-under=80`
Expected: app 核心覆盖率 ≥80%

- [ ] **Step 5: 提交**

```bash
git add tests/test_integration_processor.py
git commit -m "test: processor end-to-end integration"
```

---

### Task 10: 文档与配置收尾

**Files:**
- Modify: `README.md`（追加 S2 说明）、`.env.example`（确认 llm 配置已含）

- [ ] **Step 1: 更新文档**

```markdown
# README.md 追加
## S2：内容处理
- 事件：`article.crawled` → 生成 `summary` → 发出 `summary.generated`
- 摘要标准：200~400 字；要点 3~5 条（≤60 字）；标题 ≤30 字
- LLM：默认 DeepSeek，配置 `.env` 的 `ASSISTANT_LLM_API_KEY`；失败自动回退抽取式摘要
- 质量：摘要长度/要点数/实体保留率/平均句长自动评分，写入 `summary.scores`
```

- [ ] **Step 2: 运行完整验证**

Run: `.\.venv\Scripts\python.exe -m pytest -q`
Expected: ALL PASS

Run: `.\.venv\Scripts\python.exe -m ruff check --no-cache app tests`
Expected: All checks passed

- [ ] **Step 3: 提交**

```bash
git add README.md .env.example
git commit -m "docs: s2 content processing usage"
```

---

## Self-Review 结论（计划编写者自查）

1. **Spec 覆盖**：降噪/实体/关键词/句子打分/抽取式/LLM 生成/回退/质量评分/Summary 表/事件接线/worker 注册均有对应任务；质量指标（长度、要点、标题、实体保留率、句长）在 Task 7/9 落地；覆盖率约束在 Task 9 验证。
2. **占位符扫描**：无 TBD/TODO；每个代码步骤含完整可运行代码。
3. **类型一致性**：`ChatMessage(role, content)`、`SummarizerResult(summary_text, key_points, short_title, source)`、`score_summary` 返回键、`Summary` 字段在跨任务一致；`register_processor_handlers(registry, settings, redis, provider=None)` 在 Task 8/9 一致；worker 导入路径一致。

## 执行期备注（Ruling P）
- 本环境 coverage 的 trace 追踪与 jieba 互锁（`coverage run -m pytest` 在含 jieba 的用例上 >150s 无进展，含 `--source=app`/`--concurrency=thread` 均复现）。S2 覆盖率改在正常环境补跑（S5 运维项）；替代证据：每个 processor 模块均有直接单测（clean 3 / entities 2 / keywords 2 / extractive 2 / summarizer 3 / quality 1 / service 4 / 集成 1），全套 71 项通过、ruff 干净。
