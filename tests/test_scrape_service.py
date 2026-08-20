"""URL 上传爬取服务测试（PRD FR-20~24）。"""

import pytest
from sqlalchemy import func, select

from app.collector.base import Candidate
from app.collector.web_spider import FetchError
from app.scrape.errors import (
    DUPLICATE,
    EMPTY_CONTENT,
    HTTP_403,
    HTTP_OTHER,
    INVALID_URL_FORMAT,
    QuotaExceededError,
    ScrapeError,
    map_fetch_error,
)
from app.scrape.service import ScrapeService
from app.scrape.validator import ProbeResult
from app.storage.models import Article, ScrapeItemStatus, ScrapeJob, ScrapeJobItem, ScrapeJobStatus


class FakeValidator:
    def __init__(self, results: dict[str, ProbeResult] | None = None) -> None:
        self.results = results or {}

    async def validate(self, url: str) -> ProbeResult:
        return self.results.get(url, ProbeResult(True))


class FakeSpider:
    def __init__(self, candidates: list[Candidate] | None = None, error: Exception | None = None) -> None:
        self.candidates = candidates or []
        self.error = error

    async def fetch(self, cfg):
        if self.error is not None:
            raise self.error
        return self.candidates


def _service(settings, validator=None, spider=None, redis=None) -> ScrapeService:
    return ScrapeService(settings, validator=validator or FakeValidator(), spider=spider or FakeSpider(), redis=redis)


# ---------- create_job（FR-20 / SEC-10） ----------

def test_create_job_empty_raises(settings, session_factory):
    service = _service(settings)
    with session_factory() as session:
        with pytest.raises(ScrapeError) as exc_info:
            service.create_job(session, ["", "  "])
        assert exc_info.value.code == INVALID_URL_FORMAT


def test_create_job_dedups_urls(settings, session_factory):
    service = _service(settings)
    with session_factory() as session:
        job, dedup = service.create_job(session, ["https://a.com/1", " https://a.com/1 ", "https://a.com/2"])
        assert job.url_count == 2
        assert dedup == 1
        assert session.scalar(select(func.count()).select_from(ScrapeJob)) == 1
        assert session.scalar(select(func.count()).select_from(ScrapeJobItem)) == 2


def test_create_job_batch_limit(settings, session_factory):
    service = _service(settings)
    too_many = [f"https://a.com/{i}" for i in range(settings.scrape_max_batch + 1)]
    with session_factory() as session, pytest.raises(ScrapeError):
        service.create_job(session, too_many)


def test_create_job_quota_exceeded(settings, session_factory):
    service = _service(settings)
    with session_factory() as session:
        for _ in range(settings.scrape_max_jobs_inflight):
            service.create_job(session, ["https://a.com/x"])
        with pytest.raises(QuotaExceededError):
            service.create_job(session, ["https://a.com/y"])


# ---------- run_job（FR-22 状态机） ----------

async def test_run_job_all_succeed(settings, session_factory, redis):
    service = _service(
        settings,
        spider=FakeSpider(candidates=[Candidate(url="https://a.com/1", title="t", text="正文内容" * 5)]),
        redis=redis,
    )
    with session_factory() as session:
        job_id = service.create_job(session, ["https://a.com/1"])[0].id
    await service.run_job(session_factory, job_id)
    with session_factory() as session:
        job = session.get(ScrapeJob, job_id)
        assert job.status == ScrapeJobStatus.SUCCEEDED
        assert job.succeeded_count == 1
        assert job.finished_at is not None
        item = session.scalar(select(ScrapeJobItem).where(ScrapeJobItem.job_id == job_id))
        assert item.status == ScrapeItemStatus.SUCCEEDED
        assert item.article_id is not None
        article = session.get(Article, item.article_id)
        assert article.status == "crawled"


async def test_run_job_partial(settings, session_factory, redis):
    validator = FakeValidator({"https://a.com/bad": ProbeResult(False, HTTP_403, "禁止抓取")})
    service = _service(
        settings,
        validator=validator,
        spider=FakeSpider(candidates=[Candidate(url="https://a.com/ok", title="t", text="正文内容" * 5)]),
        redis=redis,
    )
    with session_factory() as session:
        job_id = service.create_job(session, ["https://a.com/ok", "https://a.com/bad"])[0].id
    await service.run_job(session_factory, job_id)
    with session_factory() as session:
        job = session.get(ScrapeJob, job_id)
        assert job.status == ScrapeJobStatus.PARTIAL
        assert job.succeeded_count == 1
        assert job.failed_count == 1
        items = session.scalars(select(ScrapeJobItem).where(ScrapeJobItem.job_id == job_id).order_by(ScrapeJobItem.id)).all()
        bad = next(it for it in items if it.url.endswith("bad"))
        assert bad.status == ScrapeItemStatus.FAILED
        assert bad.error_code == HTTP_403


async def test_run_job_all_fail(settings, session_factory, redis):
    validator = FakeValidator({"https://a.com/x": ProbeResult(False, HTTP_403, "禁止抓取")})
    service = _service(settings, validator=validator, spider=FakeSpider(), redis=redis)
    with session_factory() as session:
        job_id = service.create_job(session, ["https://a.com/x"])[0].id
    await service.run_job(session_factory, job_id)
    with session_factory() as session:
        job = session.get(ScrapeJob, job_id)
        assert job.status == ScrapeJobStatus.FAILED
        assert job.succeeded_count == 0
        assert job.failed_count == 1


async def test_run_job_crawl_fetch_error_maps_code(settings, session_factory, redis):
    service = _service(
        settings,
        spider=FakeSpider(error=FetchError("blocked by server (403): https://a.com/x")),
        redis=redis,
    )
    with session_factory() as session:
        job_id = service.create_job(session, ["https://a.com/x"])[0].id
    await service.run_job(session_factory, job_id)
    with session_factory() as session:
        item = session.scalar(select(ScrapeJobItem).where(ScrapeJobItem.job_id == job_id))
        assert item.status == ScrapeItemStatus.FAILED
        assert item.error_code == HTTP_403


async def test_run_job_empty_content(settings, session_factory, redis):
    service = _service(settings, spider=FakeSpider(candidates=[]), redis=redis)
    with session_factory() as session:
        job_id = service.create_job(session, ["https://a.com/empty"])[0].id
    await service.run_job(session_factory, job_id)
    with session_factory() as session:
        item = session.scalar(select(ScrapeJobItem).where(ScrapeJobItem.job_id == job_id))
        assert item.error_code == EMPTY_CONTENT


async def test_run_job_duplicate_content(settings, session_factory, redis):
    # 先入库一篇，再以相同内容爬取 → DUPLICATE（BR-24-02）
    service = _service(
        settings,
        spider=FakeSpider(candidates=[Candidate(url="https://a.com/dup", title="t", text="重复内容" * 5)]),
        redis=redis,
    )
    with session_factory() as session:
        job_id = service.create_job(session, ["https://a.com/dup"])[0].id
    await service.run_job(session_factory, job_id)
    with session_factory() as session:
        item = session.scalar(select(ScrapeJobItem).where(ScrapeJobItem.job_id == job_id))
        assert item.status == ScrapeItemStatus.SUCCEEDED
        assert item.article_id is not None
    # 第二次相同内容 → 去重命中（候选 URL 与目标一致，仅内容重复）
    with session_factory() as session:
        job2_id = service.create_job(session, ["https://a.com/dup2"])[0].id
    service.spider = FakeSpider(
        candidates=[Candidate(url="https://a.com/dup2", title="t", text="重复内容" * 5)]
    )
    await service.run_job(session_factory, job2_id)
    with session_factory() as session:
        item2 = session.scalar(select(ScrapeJobItem).where(ScrapeJobItem.job_id == job2_id))
        assert item2.error_code == DUPLICATE
        assert item2.article_id is None


async def test_run_job_url_mismatch_returns_empty(settings, session_factory, redis):
    """Issue1：候选 URL 与目标不一致时过滤为空，报 EMPTY_CONTENT，而非误用其他 URL 的内容。"""
    service = _service(
        settings,
        spider=FakeSpider(
            candidates=[Candidate(url="https://other.com/1", title="t", text="不应被采用的正文" * 5)]
        ),
        redis=redis,
    )
    with session_factory() as session:
        job_id = service.create_job(session, ["https://a.com/1"])[0].id
    await service.run_job(session_factory, job_id)
    with session_factory() as session:
        item = session.scalar(select(ScrapeJobItem).where(ScrapeJobItem.job_id == job_id))
        assert item.status == ScrapeItemStatus.FAILED
        assert item.error_code == EMPTY_CONTENT
        assert item.article_id is None


def test_map_fetch_error_other_4xx():
    """Issue2：未分类的 4xx（400/401/405 等）映射为 HTTP_OTHER，不再误导为 HTTP_403。"""
    for status in ("400", "401", "405"):
        code, _ = map_fetch_error(FetchError(f"http {status}: https://a.com/x"), "https://a.com/x")
        assert code == HTTP_OTHER


def test_create_retry_job(settings, session_factory):
    service = _service(settings)
    with session_factory() as session:
        job = service.create_job(session, ["https://a.com/x"])[0]
        item_id = session.scalar(select(ScrapeJobItem.id).where(ScrapeJobItem.job_id == job.id))
        new_job = service.create_retry_job(session, item_id)
        assert new_job.url_count == 1
        assert new_job.id != job.id
