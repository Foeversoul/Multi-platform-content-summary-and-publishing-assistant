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
