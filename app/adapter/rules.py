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
