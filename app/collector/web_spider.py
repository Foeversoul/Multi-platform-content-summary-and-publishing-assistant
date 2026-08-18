import asyncio
import random
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from readability import Document

from app.collector.base import Candidate
from app.collector.politeness import DomainPauseRegistry, RateLimiter
from app.collector.robots import RobotsPolicy, fetch_robots_text
from app.collector.sources import SourceConfig
from app.config import Settings


class FetchError(RuntimeError):
    pass


def normalize_text(text: str) -> str:
    return " ".join(text.split())


class WebSpider:
    source_type = "web"

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.settings = settings
        self.transport = transport
        self.limiter = RateLimiter(
            settings.min_domain_interval_seconds,
            settings.random_delay_min_seconds,
            settings.random_delay_max_seconds,
        )
        self.pauses = DomainPauseRegistry(settings.domain_pause_minutes)

    def _ua(self) -> str:
        return random.choice(self.settings.user_agents)

    async def _get_with_retry(self, client: httpx.AsyncClient, url: str) -> str:
        attempts = max(1, self.settings.crawl_retries + 1)  # 1 次初试 + 3 次重试
        for attempt in range(attempts):
            try:
                resp = await client.get(url, timeout=self.settings.request_timeout_seconds)
                if resp.status_code in (403, 429):
                    self.pauses.pause(urlparse(url).netloc)
                    raise FetchError(f"blocked by server ({resp.status_code}): {url}")
                if 400 <= resp.status_code < 500:
                    raise FetchError(f"http {resp.status_code}: {url}")
                resp.raise_for_status()
                return resp.text
            except httpx.HTTPError as exc:
                if attempt == attempts - 1:
                    raise FetchError(f"fetch failed after {attempts} attempts: {url} ({exc!r})") from exc
                await asyncio.sleep(self.settings.retry_base_seconds * (2**attempt))
        raise FetchError(f"unreachable: {url}")

    async def fetch(self, source: SourceConfig) -> list[Candidate]:
        if source.render:
            raise FetchError(f"source {source.id}: render=true 暂不支持（S1 仅静态抓取）")
        domain = urlparse(source.url).netloc
        if self.pauses.is_paused(domain):
            raise FetchError(f"domain paused: {domain}")
        ua = self._ua()
        headers = {"User-Agent": ua}
        async with httpx.AsyncClient(headers=headers, transport=self.transport, follow_redirects=True) as client:
            await self.limiter.wait(source.url)
            robots_text = await fetch_robots_text(client, source.url, ua)
            if robots_text is not None and not RobotsPolicy.from_text(robots_text).can_fetch(ua, source.url):
                raise FetchError(f"robots.txt disallows: {source.url}")
            html = await self._get_with_retry(client, source.url)
        doc = Document(html)
        title = normalize_text(doc.title() or source.url)
        summary_html = doc.summary(html_partial=True)
        soup = BeautifulSoup(summary_html, "html.parser")
        for tag in soup(["nav", "footer", "aside", "form"]):
            tag.decompose()
        text = normalize_text(soup.get_text(" ", strip=True))
        if not text:
            return []
        return [Candidate(url=source.url, title=title[:500], text=text)]
