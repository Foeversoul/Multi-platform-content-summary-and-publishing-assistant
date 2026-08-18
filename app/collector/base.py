from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.collector.sources import SourceConfig


@dataclass
class Candidate:
    url: str
    title: str
    text: str
    publish_time: datetime | None = None
    source_id: int | None = None


class Spider(Protocol):
    source_type: str

    async def fetch(self, source: SourceConfig) -> list[Candidate]: ...
