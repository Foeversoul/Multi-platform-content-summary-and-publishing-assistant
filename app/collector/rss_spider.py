import asyncio
from datetime import UTC, datetime

import feedparser
from bs4 import BeautifulSoup

from app.collector.base import Candidate
from app.collector.sources import SourceConfig
from app.config import Settings


def html_to_text(html: str) -> str:
    return " ".join(BeautifulSoup(html or "", "html.parser").get_text(" ", strip=True).split())


class RssSpider:
    source_type = "rss"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def fetch(self, source: SourceConfig) -> list[Candidate]:
        feed = await asyncio.to_thread(feedparser.parse, source.url)
        candidates: list[Candidate] = []
        for entry in feed.entries[: self.settings.max_rss_entries]:
            text = ""
            if entry.get("content"):
                text = html_to_text(entry.content[0].get("value", ""))
            elif entry.get("summary"):
                text = html_to_text(entry.summary)
            if not text:
                continue
            title = html_to_text(entry.get("title", "")) or source.url
            parsed = entry.get("published_parsed") or entry.get("updated_parsed")
            publish_time = datetime(*parsed[:6], tzinfo=UTC) if parsed else None
            candidates.append(
                Candidate(
                    url=entry.get("link") or source.url,
                    title=title[:500],
                    text=text,
                    publish_time=publish_time,
                )
            )
        return candidates
