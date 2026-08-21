import json

import pytest

from app.collector.opencli_spider import CommandResult, OpenCliError, OpenCliSpider
from app.collector.sources import SourceConfig
from app.collector.web_spider import FetchError


class FakeRunner:
    def __init__(self, result=None, exc=None):
        self.result = result
        self.exc = exc
        self.calls: list[list[str]] = []

    async def run(self, argv, timeout):
        self.calls.append((argv, timeout))
        if self.exc is not None:
            raise self.exc
        return self.result


class FakeSequenceRunner:
    def __init__(self, results):
        self.results = list(results)
        self.calls: list[list[str]] = []

    async def run(self, argv, timeout):
        self.calls.append((argv, timeout))
        return self.results.pop(0)


def _source(**overrides) -> SourceConfig:
    values = {
        "id": "o1",
        "name": "榜单",
        "type": "opencli",
        "site": "bilibili",
        "command": "hot",
        "limit": 5,
    }
    values.update(overrides)
    return SourceConfig(**values)


def _rows_stdout(rows) -> str:
    return json.dumps(rows, ensure_ascii=False)


async def test_build_argv_appends_limit_and_json(settings):
    settings.opencli_profile = ""
    runner = FakeRunner(CommandResult(0, "[]", ""))
    spider = OpenCliSpider(settings, runner=runner)
    source = _source()
    await spider.fetch(source)
    argv, timeout = runner.calls[0]
    assert argv == ["opencli", "bilibili", "hot", "--limit", "5", "-f", "json"]
    assert timeout == settings.opencli_timeout_seconds


async def test_build_argv_uses_args_and_profile(settings):
    runner = FakeRunner(CommandResult(0, "[]", ""))
    spider = OpenCliSpider(settings, runner=runner)
    source = _source(profile="work", args=["--region", "cn"])
    await spider.fetch(source)
    assert runner.calls[0][0] == ["opencli", "--profile", "work", "bilibili", "hot", "--region", "cn", "--limit", "5", "-f", "json"]


async def test_build_argv_falls_back_to_global_profile(settings):
    settings.opencli_profile = "9hrejvdm"
    runner = FakeRunner(CommandResult(0, "[]", ""))
    spider = OpenCliSpider(settings, runner=runner)
    await spider.fetch(_source())
    assert runner.calls[0][0] == ["opencli", "--profile", "9hrejvdm", "bilibili", "hot", "--limit", "5", "-f", "json"]


async def test_build_argv_does_not_duplicate_limit(settings):
    runner = FakeRunner(CommandResult(0, "[]", ""))
    spider = OpenCliSpider(settings, runner=runner)
    source = _source(args=["--limit", "3"])
    await spider.fetch(source)
    assert runner.calls[0][0].count("--limit") == 1


async def test_fetch_parses_row_list(settings):
    payload = [
        {"title": "第一条", "url": "https://x/1", "content": "第一条正文", "published_at": "2026-08-19T08:00:00+00:00"},
        {"title": "第二条", "url": "https://x/2", "summary": "第二条摘要"},
    ]
    runner = FakeRunner(CommandResult(0, _rows_stdout(payload), ""))
    spider = OpenCliSpider(settings, runner=runner)
    candidates = await spider.fetch(_source())
    assert len(candidates) == 2
    assert candidates[0].url == "https://x/1"
    assert candidates[0].title == "第一条"
    assert candidates[0].text == "第一条正文"
    assert candidates[0].publish_time is not None
    assert candidates[1].text == "第二条摘要"


async def test_fetch_video_uses_official_commands_and_parses(settings):
    settings.opencli_profile = "9hrejvdm"
    video_json = _rows_stdout(
        [
            {"field": "bvid", "value": "BV1Rjbe6hEfH"},
            {"field": "title", "value": "芯片对比实测"},
            {"field": "author", "value": "某UP"},
            {"field": "description", "value": "本期实测两款主流芯片的性能与功耗表现。"},
        ]
    )
    summary_json = _rows_stdout(
        [
            {"time": "00:00", "content": "开场"},
            {"time": "01:23", "content": "性能跑分"},
        ]
    )
    runner = FakeSequenceRunner([CommandResult(0, video_json, ""), CommandResult(0, summary_json, "")])
    spider = OpenCliSpider(settings, runner=runner)
    url = "https://www.bilibili.com/video/BV1Rjbe6hEfH"
    candidates = await spider.fetch_video(url)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.title == "芯片对比实测"
    # 元数据（bvid/author/description 等）不应出现在正文中
    assert "BV1Rjbe6hEfH" not in candidate.text
    assert "某UP" not in candidate.text
    assert "本期实测两款主流芯片" not in candidate.text
    assert "官方AI总结" in candidate.text
    assert "00:00 开场" in candidate.text
    assert candidate.url == url
    # 官方命令：opencli --profile <p> bilibili video <url> -f json
    assert runner.calls[0][0] == ["opencli", "--profile", "9hrejvdm", "bilibili", "video", url, "-f", "json"]
    assert runner.calls[1][0][4] == "summary"
    assert runner.calls[1][0][-1] == "json"


async def test_fetch_video_survives_summary_failure(settings):
    video_json = _rows_stdout([{"field": "title", "value": "标题X"}, {"field": "description", "value": "简介内容"}])
    runner = FakeSequenceRunner([CommandResult(0, video_json, ""), CommandResult(70, "", "summary unavailable")])
    spider = OpenCliSpider(settings, runner=runner)
    candidates = await spider.fetch_video("https://www.bilibili.com/video/BV1Rjbe6hEfH")
    assert len(candidates) == 1
    assert "标题X" in candidates[0].title
    assert "官方AI总结" not in candidates[0].text
    # AI 总结失败时用 description 兜底，不包含元数据
    assert candidates[0].text == "简介内容"


async def test_fetch_handles_wrapper_and_numeric_time(settings):
    payload = {"data": [{"name": "标题", "link": "https://x/a", "heat": 123, "created": 1755000000}]}
    runner = FakeRunner(CommandResult(0, _rows_stdout(payload), ""))
    spider = OpenCliSpider(settings, runner=runner)
    candidates = await spider.fetch(_source())
    assert len(candidates) == 1
    assert candidates[0].url == "https://x/a"
    assert candidates[0].text == "heat: 123"
    assert candidates[0].publish_time is not None


async def test_fetch_builds_synthetic_url_from_id(settings):
    payload = [{"id": "BV1abc", "title": "视频", "description": "简介内容"}]
    runner = FakeRunner(CommandResult(0, _rows_stdout(payload), ""))
    spider = OpenCliSpider(settings, runner=runner)
    candidates = await spider.fetch(_source())
    assert len(candidates) == 1
    assert candidates[0].url == "opencli://bilibili/hot/BV1abc"


async def test_fetch_skips_row_without_text(settings):
    payload = [{"id": "x", "title": "无正文", "url": "https://x/1"}]
    runner = FakeRunner(CommandResult(0, _rows_stdout(payload), ""))
    spider = OpenCliSpider(settings, runner=runner)
    assert await spider.fetch(_source()) == []


async def test_fetch_empty_result_exit(settings):
    runner = FakeRunner(CommandResult(66, "[]", ""))
    spider = OpenCliSpider(settings, runner=runner)
    assert await spider.fetch(_source()) == []


async def test_fetch_bridge_down_raises(settings):
    runner = FakeRunner(CommandResult(69, "", "daemon not responding"))
    spider = OpenCliSpider(settings, runner=runner)
    with pytest.raises(FetchError) as exc:
        await spider.fetch(_source())
    assert "Browser Bridge" in str(exc.value)


async def test_fetch_runner_error_raises(settings):
    runner = FakeRunner(exc=OpenCliError("opencli 执行超时"))
    spider = OpenCliSpider(settings, runner=runner)
    with pytest.raises(FetchError):
        await spider.fetch(_source())


async def test_fetch_invalid_json_returns_empty(settings):
    runner = FakeRunner(CommandResult(0, "not json", ""))
    spider = OpenCliSpider(settings, runner=runner)
    assert await spider.fetch(_source()) == []


async def test_crawl_by_id_roundtrips_opencli_config(session_factory, redis, settings):
    from app.collector.base import Candidate
    from app.collector.service import CollectorService, upsert_sources

    seen = {}

    class FakeSpider:
        source_type = "opencli"

        async def fetch(self, source):
            seen["site"] = source.site
            seen["command"] = source.command
            seen["limit"] = source.limit
            seen["args"] = source.args
            return [Candidate(url="https://x/1", title="正文", text="正文内容")]

    service = CollectorService(settings, redis, spiders={"opencli": FakeSpider()})
    session = session_factory()
    upsert_sources(
        session,
        [
            SourceConfig(
                id="b1",
                name="B站",
                type="opencli",
                site="bilibili",
                command="hot",
                limit=5,
                args=["--region", "cn"],
            )
        ],
    )
    ids = await service.crawl_by_id(session, "b1")
    assert len(ids) == 1
    assert seen == {"site": "bilibili", "command": "hot", "limit": 5, "args": ["--region", "cn"]}
    session.close()
