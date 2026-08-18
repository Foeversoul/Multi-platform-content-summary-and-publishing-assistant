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
