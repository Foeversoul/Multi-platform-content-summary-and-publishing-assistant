from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel


class SourceConfig(BaseModel):
    id: str
    name: str
    type: Literal["rss", "web", "opencli"]
    url: str = ""
    frequency_minutes: int = 60
    enabled: bool = True
    render: bool = False
    # OpenCLI 适配字段（type=opencli 时使用）
    site: str = ""
    command: str = "hot"
    limit: int = 0
    args: list[str] = []
    profile: str = ""
    opencli_bin: str = ""


class SourcesFile(BaseModel):
    sources: list[SourceConfig]


def load_sources(path: Path) -> list[SourceConfig]:
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return SourcesFile.model_validate(raw).sources
