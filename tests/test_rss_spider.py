import asyncio
from pathlib import Path

import pytest

from app.collector.errors import FetchError
from app.collector.rss_spider import RssSpider
from app.collector.sources import SourceConfig


async def test_rss_spider_parses_entries(settings, tmp_path: Path):
    feed = tmp_path / "feed.xml"
    feed.write_text("""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>示例频道</title>
  <item>
    <title>第一条新闻</title>
    <link>https://example.com/1</link>
    <pubDate>Mon, 17 Aug 2026 08:00:00 GMT</pubDate>
    <description>第一条的&lt;b&gt;摘要&lt;/b&gt;内容</description>
  </item>
  <item>
    <title>第二条新闻</title>
    <link>https://example.com/2</link>
    <description></description>
  </item>
</channel></rss>""", encoding="utf-8")
    source = SourceConfig(id="r1", name="RSS", type="rss", url=str(feed))
    spider = RssSpider(settings)
    candidates = await spider.fetch(source)
    assert len(candidates) == 1  # 第二条无文本被跳过
    assert candidates[0].url == "https://example.com/1"
    assert candidates[0].title == "第一条新闻"
    assert "摘要" in candidates[0].text
    assert candidates[0].publish_time is not None


async def test_rss_spider_content_and_no_link(settings, tmp_path: Path):
    feed = tmp_path / "feed-content.xml"
    feed.write_text("""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel>
  <item>
    <title>带正文条目</title>
    <link>https://example.com/full</link>
    <content:encoded><![CDATA[<p>完整正文内容。</p>]]></content:encoded>
  </item>
  <item>
    <title>无链接条目</title>
    <description>无链接摘要内容</description>
  </item>
</channel></rss>""", encoding="utf-8")
    source = SourceConfig(id="r2", name="RSS", type="rss", url=str(feed))
    spider = RssSpider(settings)
    candidates = await spider.fetch(source)
    assert len(candidates) == 2
    assert "完整正文内容" in candidates[0].text
    assert candidates[0].url == "https://example.com/full"
    assert candidates[1].url == str(feed)  # 无链接时回退到源 URL


async def test_rss_spider_surfaces_network_failure(settings, monkeypatch):
    def boom(*_args, **_kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr("app.collector.rss_spider.feedparser.parse", boom)
    source = SourceConfig(id="r-x", name="RSS", type="rss", url="https://example.com/feed.xml")
    spider = RssSpider(settings)
    with pytest.raises(FetchError, match="RSS 拉取失败"):
        await spider.fetch(source)


async def test_rss_spider_reports_timeout(settings, monkeypatch):
    settings.request_timeout_seconds = 0.05

    async def hanging_thread(_func, *_args, **_kwargs):
        await asyncio.sleep(1)

    monkeypatch.setattr("app.collector.rss_spider.asyncio.to_thread", hanging_thread)
    source = SourceConfig(id="r-y", name="RSS", type="rss", url="https://example.com/slow.xml")
    spider = RssSpider(settings)
    with pytest.raises(FetchError, match="RSS 拉取超时"):
        await spider.fetch(source)
