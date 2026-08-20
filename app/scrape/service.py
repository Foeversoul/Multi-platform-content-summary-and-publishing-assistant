"""URL 上传爬取：任务执行器（PRD FR-20~24）。

任务状态机：pending → validating → crawling → succeeded / failed / partial
条目状态机：pending → validated → crawling → succeeded / failed
"""

import asyncio
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.collector.dedup import DedupService, hash_content, simhash, to_signed
from app.collector.snapshot import SnapshotCollector
from app.collector.sources import SourceConfig
from app.collector.web_spider import FetchError, WebSpider
from app.config import Settings
from app.events import EVENT_ARTICLE_CRAWLED
from app.orchestrator.state import transition
from app.scrape.errors import (
    DUPLICATE,
    EMPTY_CONTENT,
    INTERNAL_ERROR,
    INVALID_URL_FORMAT,
    ScrapeError,
    map_fetch_error,
)
from app.scrape.validator import UrlValidator
from app.storage.models import (
    Article,
    ArticleStatus,
    ScrapeItemStatus,
    ScrapeJob,
    ScrapeJobItem,
    ScrapeJobStatus,
)
from app.storage.queue import emit_event


def _now() -> datetime:
    return datetime.now(UTC)


class ScrapeService:
    def __init__(
        self,
        settings: Settings,
        validator: UrlValidator | None = None,
        spider: WebSpider | None = None,
        dedup: DedupService | None = None,
        snapshot: SnapshotCollector | None = None,
        redis=None,
    ) -> None:
        self.settings = settings
        self.validator = validator or UrlValidator(settings)
        self.spider = spider or WebSpider(settings)
        self.dedup = dedup or DedupService(settings.dedup_window_days, settings.simhash_threshold)
        self.redis = redis
        self.snapshot = snapshot or SnapshotCollector(settings)

    # ---------- 任务创建（FR-20 / SEC-10） ----------

    def create_job(self, session: Session, urls: list[str]) -> tuple[ScrapeJob, int]:
        """创建任务，返回 (job, 去重数量)。配额与批量上限在此校验。"""
        cleaned = [u.strip() for u in urls if u and u.strip()]
        if not cleaned:
            raise ScrapeError(INVALID_URL_FORMAT, "请至少输入一个 URL")
        if len(cleaned) > self.settings.scrape_max_batch:
            raise ScrapeError(INVALID_URL_FORMAT, f"单批 URL 数量不能超过 {self.settings.scrape_max_batch} 条")
        inflight = (
            session.scalar(
                select(func.count())
                .select_from(ScrapeJob)
                .where(ScrapeJob.status.in_([ScrapeJobStatus.PENDING, ScrapeJobStatus.VALIDATING, ScrapeJobStatus.CRAWLING]))
            )
            or 0
        )
        if inflight >= self.settings.scrape_max_jobs_inflight:
            from app.scrape.errors import QuotaExceededError

            raise QuotaExceededError()
        # BR-20-02：同批重复 URL 去重
        seen: set[str] = set()
        unique: list[str] = []
        for u in cleaned:
            if u in seen:
                continue
            seen.add(u)
            unique.append(u)
        job = ScrapeJob(status=ScrapeJobStatus.PENDING, url_count=len(unique))
        session.add(job)
        session.flush()
        for u in unique:
            session.add(ScrapeJobItem(job_id=job.id, url=u, status=ScrapeItemStatus.PENDING))
        session.commit()
        return job, len(cleaned) - len(unique)

    def create_retry_job(self, session: Session, item_id: int) -> ScrapeJob:
        """FR-23：失败条目单独重新提交（生成新任务）。"""
        item = session.get(ScrapeJobItem, item_id)
        if item is None:
            raise ScrapeError(INVALID_URL_FORMAT, "爬取条目不存在")
        job, _ = self.create_job(session, [item.url])
        return job

    # ---------- 查询（FR-23） ----------

    def get_job(self, session: Session, job_id: int) -> ScrapeJob | None:
        return session.get(ScrapeJob, job_id)

    def get_items(
        self, session: Session, job_id: int, page: int = 1, page_size: int = 20, status: str | None = None
    ) -> tuple[list[ScrapeJobItem], int]:
        stmt = select(ScrapeJobItem).where(ScrapeJobItem.job_id == job_id)
        if status:
            stmt = stmt.where(ScrapeJobItem.status == status)
        total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = session.scalars(stmt.order_by(ScrapeJobItem.id).offset((page - 1) * page_size).limit(page_size)).all()
        return list(rows), total

    def get_item(self, session: Session, item_id: int) -> ScrapeJobItem | None:
        return session.get(ScrapeJobItem, item_id)

    # ---------- 任务执行（FR-22） ----------

    async def run_job(self, session_factory, job_id: int) -> None:
        """异步执行任务：验证 → 爬取 → 汇总。每个条目使用独立会话。"""
        with session_factory() as session:
            job = session.get(ScrapeJob, job_id)
            if job is None or job.status != ScrapeJobStatus.PENDING:
                return  # 任务不存在或已执行，防止重复运行
            job.status = ScrapeJobStatus.VALIDATING
            items = session.scalars(select(ScrapeJobItem).where(ScrapeJobItem.job_id == job_id).order_by(ScrapeJobItem.id)).all()
            session.commit()

        # 阶段 1：验证（FR-21）
        validated_ids: list[tuple[int, str]] = []
        for item in items:
            with session_factory() as session:
                row = session.get(ScrapeJobItem, item.id)
                result = await self.validator.validate(row.url)
                if result.ok:
                    row.status = ScrapeItemStatus.VALIDATED
                    validated_ids.append((row.id, row.url))
                else:
                    row.status = ScrapeItemStatus.FAILED
                    row.error_code = result.error_code
                    row.error_message = result.error_message
                    row.finished_at = _now()
                session.commit()

        # 阶段 2：爬取（BR-22-03 并发 ≤5）
        with session_factory() as session:
            job = session.get(ScrapeJob, job_id)
            job.status = ScrapeJobStatus.CRAWLING
            session.commit()

        semaphore = asyncio.Semaphore(self.settings.scrape_concurrency)

        async def crawl_item(item_id: int, url: str) -> None:
            async with semaphore:
                with session_factory() as session:
                    row = session.get(ScrapeJobItem, item_id)
                    row.status = ScrapeItemStatus.CRAWLING
                    session.commit()
                    article_id, err = await self._crawl_one(session, url)
                    if err is not None:
                        row.status = ScrapeItemStatus.FAILED
                        row.error_code = err.code
                        row.error_message = err.message
                    else:
                        row.status = ScrapeItemStatus.SUCCEEDED
                        row.article_id = article_id
                    row.finished_at = _now()
                    session.commit()

        await asyncio.gather(*(crawl_item(item_id, url) for item_id, url in validated_ids))

        # 汇总（BR-22-01）
        with session_factory() as session:
            job = session.get(ScrapeJob, job_id)
            job.succeeded_count = (
                session.scalar(
                    select(func.count()).select_from(ScrapeJobItem).where(
                        ScrapeJobItem.job_id == job_id, ScrapeJobItem.status == ScrapeItemStatus.SUCCEEDED
                    )
                )
                or 0
            )
            job.failed_count = job.url_count - job.succeeded_count
            if job.succeeded_count == job.url_count:
                job.status = ScrapeJobStatus.SUCCEEDED
            elif job.succeeded_count == 0:
                job.status = ScrapeJobStatus.FAILED
            else:
                job.status = ScrapeJobStatus.PARTIAL
            job.finished_at = _now()
            session.commit()

    # ---------- 单条目爬取（FR-24） ----------

    async def _crawl_one(self, session: Session, url: str) -> tuple[int | None, ScrapeError | None]:
        """爬取单个 URL 并结构化入库，返回 (article_id, error)；成功时 error 为 None。"""
        try:
            cfg = SourceConfig(id=f"scrape-{uuid.uuid4().hex[:8]}", name="manual", type="web", url=url)
            try:
                candidates = await self.spider.fetch(cfg)
                # 只保留与目标 URL 一致的候选，避免跨 URL 误取内容导致错文章/误判重（FR-24）
                candidates = [c for c in candidates if c.url == url]
            except FetchError as fetch_exc:
                # 常规爬取异常，尝试快照兜底（Playwright 渲染 + OCR）
                if self.settings.snapshot_fallback:
                    cand = await self.snapshot.capture(url)
                    if cand and cand.text:
                        candidates = [cand]
                    else:
                        session.rollback()
                        code, message = map_fetch_error(fetch_exc, url)
                        return None, ScrapeError(code, message)
                else:
                    raise
            if not candidates or not candidates[0].text:
                # 常规爬取为空，尝试快照兜底（Playwright 渲染 + OCR）
                if self.settings.snapshot_fallback:
                    cand = await self.snapshot.capture(url)
                    if cand and cand.text:
                        candidates = [cand]
                if not candidates or not candidates[0].text:
                    return None, ScrapeError(EMPTY_CONTENT)
            cand = candidates[0]
            ch = hash_content(cand.text)
            sh = simhash(cand.text)
            if self.dedup.is_duplicate(session, cand.url, ch, sh):
                return None, ScrapeError(DUPLICATE)
            article = Article(
                source_id=None,
                url=cand.url,
                title=cand.title[:500],
                publish_time=cand.publish_time,
                text=cand.text,
                content_hash=ch,
                simhash_value=to_signed(sh),
                status=ArticleStatus.PENDING,
            )
            session.add(article)
            session.flush()
            transition(ArticleStatus.PENDING, ArticleStatus.CRAWLED)
            article.status = ArticleStatus.CRAWLED
            raw_dir = self.settings.data_dir / "raw"
            raw_dir.mkdir(parents=True, exist_ok=True)
            raw_path = raw_dir / f"{article.id}.txt"
            raw_path.write_text(cand.text, encoding="utf-8")
            article.raw_path = str(raw_path)
            await emit_event(self.redis, session, EVENT_ARTICLE_CRAWLED, {"article_id": article.id}, self.settings.event_stream)
            session.commit()
            return article.id, None
        except FetchError as exc:
            session.rollback()
            code, message = map_fetch_error(exc, url)
            return None, ScrapeError(code, message)
        except Exception as exc:  # noqa: BLE001 — 兜底错误分类
            session.rollback()
            return None, ScrapeError(INTERNAL_ERROR, str(exc)[:200])
