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
