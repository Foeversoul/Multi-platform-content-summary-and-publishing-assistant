import asyncio
from datetime import UTC, datetime

import feedparser
from bs4 import BeautifulSoup

from app.collector.base import Candidate
from app.collector.errors import FetchError
from app.collector.sources import SourceConfig
from app.config import Settings


def html_to_text(html: str) -> str:
    return " ".join(BeautifulSoup(html or "", "html.parser").get_text(" ", strip=True).split())


def _entry_published_at(entry) -> datetime | None:
    """安全解析条目发布时间；字段缺失或格式异常时返回 None 而非中断整源。"""
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    try:
        return datetime(*parsed[:6], tzinfo=UTC)
    except (ValueError, TypeError, OverflowError):
        return None


class RssSpider:
    source_type = "rss"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def fetch(self, source: SourceConfig) -> list[Candidate]:
        try:
            feed = await asyncio.wait_for(
                asyncio.to_thread(feedparser.parse, source.url),
                timeout=self.settings.request_timeout_seconds,
            )
        except TimeoutError:
            raise FetchError(
                f"RSS 拉取超时（>{self.settings.request_timeout_seconds:.0f}s）：{source.url}"
            ) from None
        except Exception as exc:
            raise FetchError(f"RSS 拉取失败：{source.url} ({exc!r})") from exc

        candidates: list[Candidate] = []
        for entry in (feed.entries or [])[: self.settings.max_rss_entries]:
            text = ""
            if entry.get("content"):
                text = html_to_text(entry.content[0].get("value", ""))
            elif entry.get("summary"):
                text = html_to_text(entry.summary)
            if not text:
                continue
            title = html_to_text(entry.get("title", "")) or source.url
            candidates.append(
                Candidate(
                    url=entry.get("link") or source.url,
                    title=title[:500],
                    text=text,
                    publish_time=_entry_published_at(entry),
                )
            )
        return candidates
