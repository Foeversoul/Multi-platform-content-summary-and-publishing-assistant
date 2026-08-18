import httpx
import pytest

from app.collector.sources import SourceConfig
from app.collector.web_spider import FetchError, WebSpider

HTML = """<html><head><title>测试文章</title></head>
<body>
  <nav>导航链接</nav>
  <article>
    <h1>测试文章</h1>
    <p>第一段正文内容，包含关键信息。</p>
    <p>第二段正文内容，继续说明。</p>
  </article>
  <footer>版权信息</footer>
</body></html>"""


def _spider(settings):
    def handler(request):
        return httpx.Response(200, text=HTML, request=request)

    return WebSpider(settings, transport=httpx.MockTransport(handler))


async def test_web_spider_extracts_text(settings):
    spider = _spider(settings)
    source = SourceConfig(id="w1", name="网页", type="web", url="https://example.com/a")
    candidates = await spider.fetch(source)
    assert len(candidates) == 1
    assert candidates[0].title == "测试文章"
    assert "第一段正文内容" in candidates[0].text
    assert "导航链接" not in candidates[0].text


async def test_web_spider_4xx_fails_fast(settings):
    def handler(request):
        return httpx.Response(404, text="not found", request=request)

    spider = WebSpider(settings, transport=httpx.MockTransport(handler))
    source = SourceConfig(id="w2", name="网页", type="web", url="https://example.com/missing")
    with pytest.raises(FetchError):
        await spider.fetch(source)


async def test_web_spider_retries_5xx(settings):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503, text="busy", request=request)
        return httpx.Response(200, text=HTML, request=request)

    spider = WebSpider(settings, transport=httpx.MockTransport(handler))
    source = SourceConfig(id="w3", name="网页", type="web", url="https://example.com/retry")
    candidates = await spider.fetch(source)
    assert len(candidates) == 1
    assert calls["n"] == 3


async def test_web_spider_robots_disallowed(settings):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /\n", request=request)
        return httpx.Response(200, text=HTML, request=request)

    spider = WebSpider(settings, transport=httpx.MockTransport(handler))
    source = SourceConfig(id="w4", name="网页", type="web", url="https://example.com/blocked")
    with pytest.raises(FetchError):
        await spider.fetch(source)


async def test_web_spider_domain_paused(settings):
    spider = WebSpider(settings, transport=httpx.MockTransport(lambda r: httpx.Response(200, text=HTML, request=r)))
    spider.pauses.pause("example.com")
    source = SourceConfig(id="w5", name="网页", type="web", url="https://example.com/a")
    with pytest.raises(FetchError):
        await spider.fetch(source)


async def test_web_spider_render_unsupported(settings):
    spider = WebSpider(settings, transport=httpx.MockTransport(lambda r: httpx.Response(200, text=HTML, request=r)))
    source = SourceConfig(id="w6", name="网页", type="web", url="https://example.com/a", render=True)
    with pytest.raises(FetchError):
        await spider.fetch(source)


async def test_web_spider_network_error_fails_after_retries(settings):
    def handler(request):
        raise httpx.ConnectError("boom", request=request)

    spider = WebSpider(settings, transport=httpx.MockTransport(handler))
    source = SourceConfig(id="w7", name="网页", type="web", url="https://example.com/net")
    with pytest.raises(FetchError):
        await spider.fetch(source)


async def test_web_spider_empty_text_returns_no_candidates(settings):
    empty_html = "<html><head><title>空页</title></head><body><nav>导航</nav></body></html>"
    spider = WebSpider(settings, transport=httpx.MockTransport(lambda r: httpx.Response(200, text=empty_html, request=r)))
    source = SourceConfig(id="w8", name="网页", type="web", url="https://example.com/empty")
    assert await spider.fetch(source) == []
