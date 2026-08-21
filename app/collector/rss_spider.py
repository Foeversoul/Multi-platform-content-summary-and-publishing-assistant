import asyncio
from datetime import UTC, datetime

import feedparser
from bs4 import BeautifulSoup

from app.collector.base import Candidate
from app.collector.errors import FetchError
from app.collector.sources import SourceConfig
from app.config import Settings

_FULLTEXT_MARKERS = ("查看全文", "阅读全文", "展开阅读全文", "查看原文", "阅读原文", "全文阅读")
_MIN_FULLTEXT_LEN = 120


def html_to_text(html: str) -> str:
    return " ".join(BeautifulSoup(html or "", "html.parser").get_text(" ", strip=True).split())


def _strip_fulltext_markers(text: str) -> str:
    """移除 feed 摘要自带的“查看全文/阅读全文”等截断提示。"""
    for marker in _FULLTEXT_MARKERS:
        text = text.replace(marker, "")
    return " ".join(text.split()).strip()


def _looks_truncated(text: str) -> bool:
    """判定 feed 摘要是否可能被截断：带截断提示，或正文短到可疑。"""
    return any(marker in text for marker in _FULLTEXT_MARKERS) or len(_strip_fulltext_markers(text)) < _MIN_FULLTEXT_LEN


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

    def __init__(self, settings: Settings, web_spider=None) -> None:
        self.settings = settings
        self.web_spider = web_spider

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
            link = entry.get("link") or source.url
            if self.web_spider is not None and link != source.url and _looks_truncated(text):
                full_text = await self._fetch_full_text(link)
                if full_text:
                    text = full_text
            text = _strip_fulltext_markers(text)
            if not text:
                continue
            candidates.append(
                Candidate(
                    url=link,
                    title=title[:500],
                    text=text,
                    publish_time=_entry_published_at(entry),
                )
            )
        return candidates

    async def _fetch_full_text(self, url: str) -> str:
        """回源抓取 RSS 条目的完整正文；失败或仍被截断时返回空串。"""
        cfg = SourceConfig(id="rss-fulltext", name="rss", type="web", url=url)
        try:
            candidates = await self.web_spider.fetch(cfg)
        except FetchError:
            return ""
        for cand in candidates or []:
            if cand.url == url and cand.text and not _looks_truncated(cand.text):
                return cand.text
        return ""
