"""P4 端到端集成测试（PRD AC-2.6 智能体协作验收）。

覆盖：
1. 完整事件链：crawl.requested → article.crawled → summary.generated → copy.adapted → review.passed；
2. URL 上传爬取任务端到端：create_job → run_job → Article 入库 → 事件入队 → 下游摘要生成；
3. 失败链路：FetchError → 条目 FAILED + 错误码映射，不产生 article.crawled 事件。
"""

from pathlib import Path

from sqlalchemy import func, select

from app.adapter.service import register_adapter_handlers
from app.collector.base import Candidate
from app.collector.service import build_registry, upsert_sources
from app.collector.sources import load_sources
from app.collector.web_spider import FetchError
from app.processor.service import register_processor_handlers
from app.reviewer.service import register_reviewer_handlers
from app.scrape.errors import HTTP_403
from app.scrape.service import ScrapeService
from app.scrape.validator import ProbeResult
from app.storage.models import (
    Article,
    ArticleStatus,
    CopyStatus,
    EventLog,
    PlatformCopy,
    Review,
    ScrapeItemStatus,
    ScrapeJob,
    ScrapeJobItem,
    ScrapeJobStatus,
    Summary,
    SummaryStatus,
    Verdict,
)
from app.storage.queue import emit_event
from app.worker import run_once_loop


class SummarizeProvider:
    """模拟摘要智能体的 LLM 输出。"""

    async def chat(self, messages, temperature=0.7):
        return (
            '{"summary": "' + "这是端到端集成测试生成的摘要内容。" * 16
            + '", "key_points": ["要点一", "要点二", "要点三"], "short_title": "集成测试标题"}'
        )


class CopyProvider:
    """模拟适配智能体的 LLM 输出。"""

    async def chat(self, messages, temperature=0.7):
        return '{"text": "今天分享：#科技# #AI# 研究成果发布，内容实用，值得关注。"}'


class AllPassValidator:
    async def validate(self, url: str) -> ProbeResult:
        return ProbeResult(True)


class SpiderWithCandidates:
    def __init__(self, candidates: list[Candidate]) -> None:
        self.candidates = candidates

    async def fetch(self, cfg):
        return self.candidates


class SpiderWithError:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def fetch(self, cfg):
        raise self.error


async def _drain(settings, redis, session_factory, registry, max_rounds: int = 30) -> int:
    """循环批量消费事件直到队列为空，返回消费的事件总条数。"""
    consumed = 0
    for _ in range(max_rounds):
        n = await run_once_loop(registry, settings, redis, session_factory, "drain")
        if not n:
            break
        consumed += n
    return consumed


def _seed_rss_env(settings, tmp_path: Path, items: list[tuple[str, str]]) -> None:
    """构造本地 RSS feed 与 sources.yaml，返回 feed 文件路径。"""
    feed = tmp_path / "feed.xml"
    entries = "".join(f"<item><title>{title}</title><link>https://example.com/{i}</link><description>{desc}</description></item>" for i, (title, desc) in enumerate(items))
    feed.write_text(f'<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel><title>频道</title>{entries}</channel></rss>', encoding="utf-8")
    sources_file = tmp_path / "sources.yaml"
    sources_file.write_text(
        f"""
sources:
  - id: local-rss
    name: 本地RSS
    type: rss
    url: {feed}
    frequency_minutes: 60
""",
        encoding="utf-8",
    )
    settings.sources_file = sources_file
    platforms_file = tmp_path / "platforms.yaml"
    platforms_file.write_text(
        """
platforms:
  weibo:
    name: 微博
    min_chars: 1
    max_chars: 140
    min_tags: 1
    max_tags: 3
    style_prompt: 微博风格
""",
        encoding="utf-8",
    )
    settings.platforms_file = platforms_file


def _register_all_handlers(settings, redis, registry) -> None:
    register_processor_handlers(registry, settings, redis, provider=SummarizeProvider())
    register_adapter_handlers(registry, settings, redis, provider=CopyProvider())
    register_reviewer_handlers(registry, settings, redis)


async def test_full_event_chain_rss_to_reviewed(settings, session_factory, redis, tmp_path: Path):
    """AC-2.6：五智能体端到端事件链，从采集到审核终态。"""
    _seed_rss_env(settings, tmp_path, [("集成文章一", "第一篇文章正文" * 30), ("集成文章二", "第二篇文章正文" * 30)])
    session = session_factory()
    upsert_sources(session, load_sources(settings.sources_file))
    registry = build_registry(settings, redis)
    _register_all_handlers(settings, redis, registry)
    await emit_event(redis, session, "crawl.requested", {"source_id": "local-rss"}, settings.event_stream)
    consumed = await _drain(settings, redis, session_factory, registry, max_rounds=30)
    # 1(crawl) + 2(crawled) + 2(summarized) + 2(adapted) + 2(review.passed)
    assert consumed == 9
    articles = session.scalars(select(Article).order_by(Article.id)).all()
    assert len(articles) == 2
    assert all(a.status == ArticleStatus.REVIEWED for a in articles)
    assert all(a.raw_path for a in articles)
    summaries = session.scalars(select(Summary)).all()
    assert len(summaries) == 2
    assert all(s.status == SummaryStatus.SUMMARIZED for s in summaries)
    assert all(s.scores.get("length_ok") is True for s in summaries)
    copies = session.scalars(select(PlatformCopy)).all()
    assert len(copies) == 2
    assert all(c.status == CopyStatus.REVIEWED for c in copies)
    reviews = session.scalars(select(Review)).all()
    assert len(reviews) == 2
    assert all(r.verdict == Verdict.PENDING for r in reviews)
    assert all(r.scores.get("style_score") is not None for r in reviews)
    # 事件日志完整记录五个事件类型
    types = set(session.scalars(select(EventLog.event_type)).all())
    assert types == {
        "crawl.requested",
        "article.crawled",
        "summary.generated",
        "copy.adapted",
        "review.passed",
    }
    session.close()


async def test_scrape_job_end_to_end(settings, session_factory, redis):
    """URL 上传爬取任务端到端：创建 → 执行 → 文章入库 → 事件驱动下游摘要。"""
    validator = AllPassValidator()
    spider = SpiderWithCandidates(
        [
            Candidate(url="https://a.com/1", title="任务文章一", text="第一篇文章的正文内容。" * 30),
            Candidate(url="https://a.com/2", title="任务文章二", text="第二篇文章的正文内容。" * 30),
        ]
    )
    service = ScrapeService(settings, validator=validator, spider=spider, redis=redis)
    with session_factory() as session:
        job, dedup = service.create_job(session, ["https://a.com/1", "https://a.com/2"])
        assert dedup == 0
    await service.run_job(session_factory, job.id)
    with session_factory() as session:
        job_row = session.get(ScrapeJob, job.id)
        assert job_row.status == ScrapeJobStatus.SUCCEEDED
        assert job_row.succeeded_count == 2
        assert job_row.failed_count == 0
        assert job_row.finished_at is not None
        items = session.scalars(select(ScrapeJobItem.status).where(ScrapeJobItem.job_id == job.id)).all()
        assert all(s == ScrapeItemStatus.SUCCEEDED for s in items)
        articles = session.scalars(select(Article).order_by(Article.id)).all()
        assert len(articles) == 2
        assert all(a.status == ArticleStatus.CRAWLED for a in articles)
        # 事件已入队，由下游智能体消费
    registry = build_registry(settings, redis)
    register_processor_handlers(registry, settings, redis, provider=SummarizeProvider())

    async def noop(payload, session):
        return None

    registry.register("summary.generated", noop)  # 下游仅验证到摘要为止
    consumed = await _drain(settings, redis, session_factory, registry, max_rounds=10)
    # 2 个 article.crawled 被处理生成摘要 + 2 个 summary.generated 由 noop 消费
    assert consumed == 4
    with session_factory() as session:
        summaries = session.scalars(select(Summary)).all()
        assert len(summaries) == 2
        assert all(s.status == SummaryStatus.SUMMARIZED for s in summaries)
        assert all(session.get(Article, s.article_id).status == ArticleStatus.SUMMARIZED for s in summaries)


async def test_scrape_job_failure_chain(settings, session_factory, redis):
    """失败链路：FetchError 分类为 HTTP_403，任务 FAILED，不产生事件。"""
    service = ScrapeService(
        settings,
        validator=AllPassValidator(),
        spider=SpiderWithError(FetchError("blocked by server (403)")),
        redis=redis,
    )
    with session_factory() as session:
        job, _ = service.create_job(session, ["https://a.com/1"])
    await service.run_job(session_factory, job.id)
    with session_factory() as session:
        job_row = session.get(ScrapeJob, job.id)
        assert job_row.status == ScrapeJobStatus.FAILED
        assert job_row.succeeded_count == 0
        assert job_row.failed_count == 1
        item = session.scalar(select(ScrapeJobItem).where(ScrapeJobItem.job_id == job.id))
        assert item.status == ScrapeItemStatus.FAILED
        assert item.error_code == HTTP_403
        assert "403" in item.error_message
        assert item.article_id is None
        assert session.scalar(select(func.count()).select_from(Article)) == 0
        # 失败不产生 article.crawled 事件，无死信
        assert session.scalar(select(func.count()).select_from(EventLog)) == 0
