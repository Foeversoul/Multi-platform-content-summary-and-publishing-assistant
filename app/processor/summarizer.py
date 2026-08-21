import asyncio
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
_SHORT_SOURCE_PROMPT = (
    "你是资深中文内容编辑。下面的素材信息很少（通常是视频简介或时间戳大纲）。"
    "请基于标题和素材，补写一段完整、连贯、详实的中文完整描述，字数不少于 {min_chars} 字。"
    "不要编造素材中不存在的事实，可以围绕主题合理展开并保持客观。"
    '只输出 JSON：{"summary": string}'
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


async def _short_source_description(provider, article_text: str, title: str, min_chars: int) -> SummarizerResult:
    """原文很短（视频链接常见）时，让 AI 补写一大段完整文字描述。"""
    clean = clean_text(article_text)

    async def _fallback() -> SummarizerResult:
        body = clean or (title or "")
        return SummarizerResult(
            summary_text=body,
            key_points=[],
            short_title=(title or "")[:30],
            source="extractive",
        )

    if provider is None:
        return await _fallback()
    system_prompt = _SHORT_SOURCE_PROMPT.replace("{min_chars}", str(min_chars))
    user_message = f"标题：{title}\n\n素材：\n{clean[:3000]}"
    try:
        raw = await provider.chat([ChatMessage("system", system_prompt), ChatMessage("user", user_message)])
        data = json.loads(raw)
        summary_text = str(data["summary"]).strip()
        if len(summary_text) < min_chars:
            raise ValueError("description too short")
        return SummarizerResult(summary_text, [], (title or "")[:30], "llm")
    except (LLMError, ValueError, json.JSONDecodeError, KeyError):
        return await _fallback()


async def generate_summary(provider, article_text: str, title: str, min_chars: int = 200, max_chars: int = 400) -> SummarizerResult:
    if len(clean_text(article_text)) < min_chars:
        return await _short_source_description(provider, article_text, title, min_chars)
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


async def generate_summary_quick(
    provider,
    article_text: str,
    title: str,
    min_chars: int = 200,
    max_chars: int = 400,
    timeout_seconds: float = 3.0,
) -> SummarizerResult:
    """限时摘要生成：LLM 超时（或未配置）自动回退到抽取式摘要，保证调用方响应 ≤ timeout_seconds。"""
    try:
        return await asyncio.wait_for(generate_summary(provider, article_text, title, min_chars, max_chars), timeout=timeout_seconds)
    except TimeoutError:
        return _fallback(article_text, title, min_chars, max_chars)
