import asyncio
import random
import re
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from readability import Document

from app.collector.base import Candidate
from app.collector.errors import FetchError
from app.collector.opencli_spider import OpenCliError, SubprocessRunner
from app.collector.politeness import DomainPauseRegistry, RateLimiter
from app.collector.robots import RobotsPolicy, fetch_robots_text
from app.collector.sources import SourceConfig
from app.config import Settings


def normalize_text(text: str) -> str:
    return " ".join(text.split())


def _first_markdown_heading(value: str) -> str:
    for raw in value.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return ""


def _markdown_to_text(value: str) -> str:
    """把 opencli web read 导出的 Markdown 规整成可入库的纯文本。"""
    cleaned: list[str] = []
    for raw in value.splitlines():
        line = raw.strip()
        if not line:
            continue
        # 忽略 opencli 可能输出的 YAML 汇总行
        if re.match(r"^(title|author|publish_time|status|size|saved)\s*:", line, flags=re.IGNORECASE):
            continue
        # 去掉图片标记，再把 [文本](链接) 转成 "文本 (链接)"
        line = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", line).strip()
        line = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", line).strip()
        if line:
            cleaned.append(line)
    return normalize_text("\n".join(cleaned))


class WebSpider:
    source_type = "web"

    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
        runner=None,
    ) -> None:
        self.settings = settings
        self.transport = transport
        self.runner = runner or SubprocessRunner()
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
            # 序接的 JS 渲染采集：不再抛 RENDER_UNSUPPORTED，改用 OpenCLI 浏览器兜底
            return [await self._fetch_rendered(source)]

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

        # 静态抓不到正文（典型的 JS 渲染页面）时，自动用 OpenCLI 浏览器渲染兜底
        if not text and self.settings.opencli_render_fallback:
            return [await self._fetch_rendered(source)]
        if not text:
            return []
        return [Candidate(url=source.url, title=title[:500], text=text)]

    async def _fetch_rendered(self, source: SourceConfig) -> Candidate:
        """通过 opencli web read 用已登录浏览器渲染页面并导出 Markdown。"""
        argv = [self.settings.opencli_bin]
        if self.settings.opencli_profile:
            argv += ["--profile", self.settings.opencli_profile]
        argv += ["web", "read", "--url", source.url, "--stdout", "true"]
        try:
            result = await self.runner.run(argv, self.settings.opencli_render_timeout_seconds)
        except OpenCliError as exc:
            raise FetchError(str(exc)) from exc
        except FileNotFoundError as exc:
            raise FetchError(f"未找到 opencli 命令：{argv[0]}，无法渲染 JS 页面") from exc
        if result.returncode not in (0,):
            detail = (result.stderr or result.stdout or "").strip()
            raise FetchError(f"opencli 渲染失败（exit {result.returncode}）：{detail[:300]}")
        text = _markdown_to_text(result.stdout)
        if not text:
            raise FetchError(f"opencli 渲染后仍无内容: {source.url}")
        title = _first_markdown_heading(result.stdout) or source.url
        return Candidate(url=source.url, title=title[:500], text=text)
