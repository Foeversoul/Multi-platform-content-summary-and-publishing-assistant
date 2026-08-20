import asyncio
import json
import re
from dataclasses import dataclass

from app.adapter.platforms import PlatformConfig
from app.llm.provider import ChatMessage, LLMError
from app.storage.models import Summary


@dataclass
class CopyResult:
    text: str
    source: str


_NUM_VARIANTS = 4


def _detect_variant(text: str) -> int:
    """从文案文本检测当前使用的变体号。"""
    if text.startswith("速览｜"):
        return 1
    if text.startswith("关于「"):
        return 2
    if text.startswith("【"):
        return 3
    return 0


def next_fallback_variant(current_text: str) -> int:
    """计算下一次回退扩写使用的变体号，保证与当前文案不同的改写。"""
    return (_detect_variant(current_text) + 1) % _NUM_VARIANTS


def _enforce_max(text: str, max_chars: int) -> str:
    return text[:max_chars]


def _strip_hashtags(text: str) -> str:
    """移除已有 #话题# 标记，避免改写后重复堆叠。"""
    return re.sub(r"#[^#]+#", "", text).strip()


def _split_sentences(text: str) -> list[str]:
    """按句号/换行拆分为短句，用作伪要点。"""
    parts = re.split(r"[。\n]", text)
    return [p.strip() for p in parts if p.strip() and len(p.strip()) > 3]


def _clean_for_regen(text: str) -> str:
    """移除话题标记和所有变体前缀标记，提取干净内容用于改写。"""
    text = re.sub(r"#[^#]+#", "", text)
    # 移除所有变体前缀标记（不论位置，只去掉前缀，保留后面的内容）
    text = re.sub(r"速览｜[^，。\n、]*", "", text)
    text = re.sub(r"关于「[^」]*」，这是我看到的一些要点：", "", text)
    text = re.sub(r"【[^】]*】", "", text)
    text = re.sub(r"^\s*·\s*", "", text, flags=re.MULTILINE)
    # 移除行首的 短标题： 前缀（变体0格式），标题不含句末标点
    text = re.sub(r"^[^：，。！？\n]{1,15}：", "", text)
    # 清理残留的标点和空白
    text = re.sub(r"[、，]{2,}", "、", text)
    text = re.sub(r"^[、，。\s]+", "", text)
    text = re.sub(r"[、，。\s]+$", "", text)
    return text.strip()


def _fallback(
    summary: Summary,
    platform: PlatformConfig,
    variant: int = 0,
    *,
    current_text: str = "",
) -> CopyResult:
    title = summary.short_title or ""
    body = summary.summary_text or ""
    points = [p for p in (summary.key_points or []) if p]
    # 摘要为空但已有文案时，用已有文案作为改写素材
    if not body and not points and current_text:
        body = _clean_for_regen(current_text)
        points = _split_sentences(body)
    if variant == 1:
        lead = f"速览｜{title}\n" + ("、".join(points[:4]) or body[:60])
    elif variant == 2:
        lead = f"关于「{title}」，这是我看到的一些要点：\n" + "\n".join(f"· {p}" for p in points[:4] or [body[:60]])
    elif variant == 3:
        lead = f"【{title}】\n{body}"
    else:
        lead = f"{title}：{body}"
    if platform.max_tags and points:
        tags = " ".join(f"#{k}#" for k in points[: platform.max_tags])
        text = lead + "\n" + tags
    else:
        text = lead
    return CopyResult(_enforce_max(text, platform.max_chars), "fallback")


async def generate_copy(
    provider,
    summary: Summary,
    platform: PlatformConfig,
    variant: int = 0,
    current_text: str = "",
) -> CopyResult:
    if provider is None:
        return _fallback(summary, platform, variant, current_text=current_text)
    prompt = (
        f"{platform.style_prompt}\n"
        f"标题：{summary.short_title}\n摘要：{summary.summary_text}\n要点：{'；'.join(summary.key_points or []) or '无'}\n"
        f"字数要求：{platform.min_chars}-{platform.max_chars}字。"
        f"只输出 JSON：{{\"text\": string}}"
    )
    if current_text:
        prompt += f"\n这是已有文案，请用不同的表达方式、不同的开头和结构重新扩写：\n{current_text}"
        prompt += "\n改写要求：换一种切入角度，避免重复已有文案的句式和措辞。"
    try:
        raw = await provider.chat([ChatMessage("user", prompt)])
        data = json.loads(raw)
        text = str(data["text"]).strip()
        if not text:
            raise ValueError("empty text")
        return CopyResult(_enforce_max(text, platform.max_chars), "llm")
    except (LLMError, ValueError, json.JSONDecodeError, KeyError):
        return _fallback(summary, platform, variant, current_text=current_text)


async def generate_copy_quick(
    provider,
    summary: Summary,
    platform: PlatformConfig,
    timeout_seconds: float = 3.0,
    variant: int = 0,
    current_text: str = "",
) -> CopyResult:
    """限时平台文案生成：LLM 超时（或未配置）自动回退到模板扩写，保证调用方响应 ≤ timeout_seconds。"""
    try:
        return await asyncio.wait_for(
            generate_copy(provider, summary, platform, variant=variant, current_text=current_text),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        return _fallback(summary, platform, variant, current_text=current_text)
