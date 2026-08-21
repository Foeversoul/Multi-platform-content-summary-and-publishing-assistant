"""URL 上传爬取：格式校验、SSRF 防护与可访问性探测（PRD FR-21 / SEC-09）。

探测流程：格式校验 → SSRF 静态检查 → DNS 解析并二次校验 → robots 检查 → HTTP 状态分类。
"""

import asyncio
import ipaddress
import random
import socket
import ssl
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from app.collector.robots import RobotsPolicy, fetch_robots_text
from app.config import Settings
from app.scrape.errors import (
    CONNECTION_REFUSED,
    DNS_FAILED,
    ERROR_MESSAGES,
    HTTP_5XX,
    HTTP_403,
    HTTP_404,
    HTTP_429,
    HTTP_OTHER,
    INVALID_URL_FORMAT,
    ROBOTS_BLOCKED,
    SSL_ERROR,
    SSRF_BLOCKED,
    TIMEOUT,
    UNSUPPORTED_PROTOCOL,
)


@dataclass
class ProbeResult:
    ok: bool
    error_code: str | None = None
    error_message: str | None = None


def _ip_is_blocked(ip_str: str) -> bool:
    """SSRF 拦截：内网/回环/链路本地/保留/组播地址（SEC-09）。"""
    ip = ipaddress.ip_address(ip_str)
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def validate_url_format(url: str) -> str | None:
    """格式校验，返回错误码或 None（合法）。"""
    try:
        parsed = urlparse(url)
    except ValueError:
        return INVALID_URL_FORMAT
    if parsed.scheme not in ("http", "https"):
        return UNSUPPORTED_PROTOCOL if parsed.scheme else INVALID_URL_FORMAT
    if not parsed.netloc or not parsed.hostname:
        return INVALID_URL_FORMAT
    if " " in url or "\n" in url or "\t" in url:
        return INVALID_URL_FORMAT
    return None


def check_static_ssrf(url: str) -> str | None:
    """对 IP 字面量主机做静态 SSRF 检查（域名在解析后二次校验）。"""
    host = urlparse(url).hostname
    if host is None:
        return INVALID_URL_FORMAT
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return None  # 域名，交由解析后校验
    return SSRF_BLOCKED if _ip_is_blocked(str(ip)) else None


class UrlValidator:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.settings = settings
        self.transport = transport

    async def _resolve_check_ssrf(self, host: str) -> str | None:
        """解析域名并校验所有解析 IP，返回 SSRF_BLOCKED / DNS_FAILED / None。"""
        loop = asyncio.get_running_loop()
        try:
            infos = await loop.getaddrinfo(host, None, type=0)
        except OSError:
            return DNS_FAILED
        if not infos:
            return DNS_FAILED
        for info in infos:
            ip = info[4][0]
            if _ip_is_blocked(ip):
                return SSRF_BLOCKED
        return None

    async def check_resolved_ssrf(self, url: str) -> str | None:
        """域名级 SSRF 校验（供非 scrape 入口复用，如手动 URL 爬取）：格式 + 静态 + 解析后二次校验。"""
        err = validate_url_format(url)
        if err:
            return err
        err = check_static_ssrf(url)
        if err:
            return err
        host = urlparse(url).hostname
        if host is None:
            return INVALID_URL_FORMAT
        return await self._resolve_check_ssrf(host)

    async def validate(self, url: str) -> ProbeResult:
        """完整验证流程，返回探测结果。"""
        err = validate_url_format(url)
        if err:
            return ProbeResult(False, err, ERROR_MESSAGES[err])
        err = check_static_ssrf(url)
        if err:
            return ProbeResult(False, err, ERROR_MESSAGES[err])
        host = urlparse(url).hostname
        err = await self._resolve_check_ssrf(host)
        if err:
            return ProbeResult(False, err, ERROR_MESSAGES[err])
        return await self._probe_http(url)

    async def validate_without_probe(self, url: str) -> ProbeResult:
        """Format + SSRF check only, skip HTTP probe.

        Used for platforms fetched via Browser Bridge (opencli), whose real
        fetch does not depend on this process directly connecting the target,
        so an HTTP probe is both meaningless and misclassified as unreachable
        in environments without direct outbound network.
        """
        err = validate_url_format(url)
        if err:
            return ProbeResult(False, err, ERROR_MESSAGES[err])
        err = check_static_ssrf(url)
        if err:
            return ProbeResult(False, err, ERROR_MESSAGES[err])
        host = urlparse(url).hostname
        err = await self._resolve_check_ssrf(host)
        if err:
            return ProbeResult(False, err, ERROR_MESSAGES[err])
        return ProbeResult(True)

    async def _probe_http(self, url: str) -> ProbeResult:
        ua = random.choice(self.settings.user_agents)
        headers = {"User-Agent": ua}
        timeout = httpx.Timeout(self.settings.scrape_probe_timeout_seconds)
        try:
            async with httpx.AsyncClient(
                headers=headers, timeout=timeout, follow_redirects=True, transport=self.transport
            ) as client:
                robots_text = await fetch_robots_text(client, url, ua)
                if robots_text is not None and not RobotsPolicy.from_text(robots_text).can_fetch(ua, url):
                    return ProbeResult(False, ROBOTS_BLOCKED, ERROR_MESSAGES[ROBOTS_BLOCKED])
                resp = await client.get(url)
        except httpx.TimeoutException:
            return ProbeResult(False, TIMEOUT, ERROR_MESSAGES[TIMEOUT])
        except httpx.ConnectError as exc:
            code, message = self._classify_connect(exc)
            return ProbeResult(False, code, message)
        except httpx.HTTPError:
            return ProbeResult(False, CONNECTION_REFUSED, ERROR_MESSAGES[CONNECTION_REFUSED])
        if resp.status_code == 403:
            return ProbeResult(False, HTTP_403, ERROR_MESSAGES[HTTP_403])
        if resp.status_code == 404:
            return ProbeResult(False, HTTP_404, ERROR_MESSAGES[HTTP_404])
        if resp.status_code == 429:
            return ProbeResult(False, HTTP_429, ERROR_MESSAGES[HTTP_429])
        if 500 <= resp.status_code < 600:
            return ProbeResult(False, HTTP_5XX, ERROR_MESSAGES[HTTP_5XX])
        if 400 <= resp.status_code < 500:
            # 未单独分类的 4xx（400/401/405 等）不归入 HTTP_403，避免误导错误原因
            return ProbeResult(False, HTTP_OTHER, ERROR_MESSAGES[HTTP_OTHER])
        return ProbeResult(True)

    def _classify_connect(self, exc: httpx.ConnectError) -> tuple[str, str]:
        cause = exc.__cause__ if exc.__cause__ is not None else exc
        if isinstance(cause, asyncio.TimeoutError):
            return TIMEOUT, ERROR_MESSAGES[TIMEOUT]
        if isinstance(cause, ssl.SSLError):
            return SSL_ERROR, ERROR_MESSAGES[SSL_ERROR]
        if isinstance(cause, OSError):
            if isinstance(cause, socket.gaierror):
                return DNS_FAILED, ERROR_MESSAGES[DNS_FAILED]
            if isinstance(cause, ConnectionRefusedError):
                return CONNECTION_REFUSED, ERROR_MESSAGES[CONNECTION_REFUSED]
        return CONNECTION_REFUSED, ERROR_MESSAGES[CONNECTION_REFUSED]
