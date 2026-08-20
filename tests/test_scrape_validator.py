"""URL 验证器测试（PRD FR-21 / SEC-09）。"""

import ssl

import httpx
import pytest

from app.scrape.errors import (
    CONNECTION_REFUSED,
    DNS_FAILED,
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
from app.scrape.validator import UrlValidator, check_static_ssrf, validate_url_format


def test_format_rejects_invalid():
    assert validate_url_format("not a url") == INVALID_URL_FORMAT
    assert validate_url_format("") == INVALID_URL_FORMAT
    assert validate_url_format("ftp://example.com/a") == UNSUPPORTED_PROTOCOL
    assert validate_url_format("javascript:alert(1)") == UNSUPPORTED_PROTOCOL
    assert validate_url_format("https://") == INVALID_URL_FORMAT
    assert validate_url_format("https://exa mple.com/x") == INVALID_URL_FORMAT


def test_format_accepts_valid():
    assert validate_url_format("https://example.com/article/1") is None
    assert validate_url_format("http://example.com") is None


def test_static_ssrf_blocks_private_ips():
    assert check_static_ssrf("http://127.0.0.1/x") == SSRF_BLOCKED
    assert check_static_ssrf("http://10.0.0.5/x") == SSRF_BLOCKED
    assert check_static_ssrf("http://192.168.1.1/x") == SSRF_BLOCKED
    assert check_static_ssrf("http://169.254.169.254/latest/meta-data") == SSRF_BLOCKED
    assert check_static_ssrf("http://0.0.0.0/x") == SSRF_BLOCKED


def test_static_ssrf_allows_public_ip_and_domain():
    assert check_static_ssrf("http://93.184.216.34/x") is None
    assert check_static_ssrf("http://example.com/x") is None  # 域名解析后二次校验


async def test_resolve_ssrf_blocks_localhost(settings):
    validator = UrlValidator(settings)
    assert await validator._resolve_check_ssrf("localhost") == SSRF_BLOCKED


async def test_resolve_ssrf_dns_failure(settings, monkeypatch):
    class FakeLoop:
        async def getaddrinfo(self, *args, **kwargs):
            raise OSError("name resolution failed")

    validator = UrlValidator(settings)
    monkeypatch.setattr("app.scrape.validator.asyncio.get_running_loop", lambda: FakeLoop())
    assert await validator._resolve_check_ssrf("no-such-host.invalid") == DNS_FAILED


async def test_probe_http_success(settings):
    def handler(request):
        return httpx.Response(200, text="<html>ok</html>")

    validator = UrlValidator(settings, transport=httpx.MockTransport(handler))
    result = await validator._probe_http("http://93.184.216.34/x")
    assert result.ok


@pytest.mark.parametrize(
    "status,expected",
    [
        (403, HTTP_403),
        (404, HTTP_404),
        (429, HTTP_429),
        (400, HTTP_OTHER),
        (401, HTTP_OTHER),
        (405, HTTP_OTHER),
        (500, HTTP_5XX),
        (502, HTTP_5XX),
    ],
)
async def test_probe_http_status_classification(settings, status, expected):
    def handler(request):
        return httpx.Response(status)

    validator = UrlValidator(settings, transport=httpx.MockTransport(handler))
    result = await validator._probe_http("http://93.184.216.34/x")
    assert not result.ok
    assert result.error_code == expected


async def test_probe_http_robots_blocked(settings):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private/")
        return httpx.Response(200, text="ok")

    validator = UrlValidator(settings, transport=httpx.MockTransport(handler))
    result = await validator._probe_http("http://93.184.216.34/private/x")
    assert not result.ok
    assert result.error_code == ROBOTS_BLOCKED


async def test_probe_http_timeout(settings):
    def handler(request):
        raise httpx.ConnectTimeout("too slow")

    validator = UrlValidator(settings, transport=httpx.MockTransport(handler))
    result = await validator._probe_http("http://93.184.216.34/x")
    assert result.error_code == TIMEOUT


async def test_probe_http_ssl_error(settings):
    def handler(request):
        # 模拟真实网络栈：httpx 将底层 ssl.SSLError 包装为 ConnectError，__cause__ 保留原始异常
        err = httpx.ConnectError("bad cert", request=request)
        err.__cause__ = ssl.SSLError("bad cert")
        raise err

    validator = UrlValidator(settings, transport=httpx.MockTransport(handler))
    result = await validator._probe_http("http://93.184.216.34/x")
    assert result.error_code == SSL_ERROR


async def test_probe_http_connect_refused(settings):
    def handler(request):
        raise httpx.ConnectError("connection refused")

    validator = UrlValidator(settings, transport=httpx.MockTransport(handler))
    result = await validator._probe_http("http://93.184.216.34/x")
    assert result.error_code == CONNECTION_REFUSED


async def test_validate_blocks_localhost_full_flow(settings):
    validator = UrlValidator(settings)
    result = await validator.validate("http://localhost/x")
    assert not result.ok
    assert result.error_code == SSRF_BLOCKED


async def test_validate_format_error(settings):
    validator = UrlValidator(settings)
    result = await validator.validate("ftp://example.com/x")
    assert result.error_code == UNSUPPORTED_PROTOCOL
    result = await validator.validate("bad url")
    assert result.error_code == INVALID_URL_FORMAT
