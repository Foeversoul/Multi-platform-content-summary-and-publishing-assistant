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
