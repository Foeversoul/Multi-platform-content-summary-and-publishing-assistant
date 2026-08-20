import logging
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collector.base import Candidate
from app.collector.dedup import DedupService, hash_content, simhash, to_signed
from app.collector.opencli_spider import OpenCliSpider
from app.collector.rss_spider import RssSpider
from app.collector.sources import SourceConfig
from app.collector.web_spider import WebSpider
from app.config import Settings
from app.events import EVENT_ARTICLE_CRAWLED, EVENT_CRAWL_REQUESTED
from app.orchestrator.registry import SkillRegistry
from app.orchestrator.state import transition
from app.storage.models import Article, ArticleStatus, Source
from app.storage.queue import emit_event

logger = logging.getLogger(__name__)

SPIDERS = {"web": WebSpider, "rss": RssSpider, "opencli": OpenCliSpider}


def upsert_sources(session: Session, sources: list[SourceConfig]) -> None:
    for cfg in sources:
        row = session.scalar(select(Source).where(Source.external_id == cfg.id))
        if row is None:
            session.add(
                Source(
                    external_id=cfg.id,
                    name=cfg.name,
                    type=cfg.type,
                    url=cfg.url,
                    frequency_minutes=cfg.frequency_minutes,
                    enabled=cfg.enabled,
                    site=cfg.site,
                    command=cfg.command,
                    limit=cfg.limit,
                    args=cfg.args,
                    profile=cfg.profile,
                    opencli_bin=cfg.opencli_bin,
                )
            )
        else:
            row.name = cfg.name
            row.type = cfg.type
            row.url = cfg.url
            row.frequency_minutes = cfg.frequency_minutes
            row.enabled = cfg.enabled
            row.site = cfg.site
            row.command = cfg.command
            row.limit = cfg.limit
            row.args = cfg.args
            row.profile = cfg.profile
            row.opencli_bin = cfg.opencli_bin
    session.commit()


class CollectorService:
    def __init__(self, settings: Settings, redis, spiders: dict | None = None, dedup: DedupService | None = None) -> None:
        self.settings = settings
        self.redis = redis
        self.spiders = spiders or {key: cls(settings) for key, cls in SPIDERS.items()}
        self.dedup = dedup or DedupService(settings.dedup_window_days, settings.simhash_threshold)

    async def crawl_source(self, session: Session, source: SourceConfig) -> list[int]:
        spider = self.spiders.get(source.type)
        if spider is None:
            raise ValueError(f"unknown source type: {source.type}")
        candidates = await spider.fetch(source)
        src_row = session.scalar(select(Source).where(Source.external_id == source.id))
        source_db_id = src_row.id if src_row is not None else None
        raw_dir = self.settings.data_dir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        new_ids: list[int] = []
        for cand in candidates:
            try:
                article_id = await self._save_candidate(session, source_db_id, cand, raw_dir)
                if article_id is not None:
                    new_ids.append(article_id)
            except Exception:
                # E4：单条目异常（如 simhash/落库失败）不中断整源，记录后继续
                session.rollback()
                logger.exception("candidate skipped", extra={"url": cand.url})
        session.commit()
        return new_ids

    async def _save_candidate(self, session: Session, source_db_id: int | None, cand: Candidate, raw_dir: Path) -> int | None:
        """保存单条采集结果：去重 → 落库 → 记录原文 → 发布事件。返回文章 ID，去重跳过时返回 None。"""
        if not cand.text:
            return None
        ch = hash_content(cand.text)
        sh = simhash(cand.text)
        if self.dedup.is_duplicate(session, cand.url, ch, sh):
            logger.info("duplicate skipped", extra={"url": cand.url})
            return None
        article = Article(
            source_id=source_db_id if source_db_id is not None else cand.source_id,
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
        raw_path = raw_dir / f"{article.id}.txt"
        raw_path.write_text(cand.text, encoding="utf-8")
        article.raw_path = str(raw_path)
        await emit_event(self.redis, session, EVENT_ARTICLE_CRAWLED, {"article_id": article.id}, self.settings.event_stream)
        return article.id

    async def crawl_by_id(self, session: Session, source_id: str) -> list[int]:
        row = session.scalar(select(Source).where(Source.external_id == source_id))
        if row is None:
            raise ValueError(f"unknown source_id: {source_id}")
        if not row.enabled:
            return []
        cfg = SourceConfig(
            id=row.external_id,
            name=row.name,
            type=row.type,
            url=row.url,
            frequency_minutes=row.frequency_minutes,
            enabled=row.enabled,
            site=row.site or "",
            command=row.command or "hot",
            limit=row.limit or 0,
            args=row.args or [],
            profile=row.profile or "",
            opencli_bin=row.opencli_bin or "",
        )
        return await self.crawl_source(session, cfg)

    async def crawl_url(self, session: Session, url: str) -> list[int]:
        """手动 URL 爬取（CLI --url / HTML 失败重试入口），先做 SSRF 防护（S3/SEC-09）。"""
        await self._guard_manual_url(url)
        cfg = SourceConfig(id=f"manual-{uuid.uuid4().hex[:8]}", name="manual", type="web", url=url)
        return await self.crawl_source(session, cfg)

    async def _guard_manual_url(self, url: str) -> None:
        """格式 + 静态 IP + DNS 解析后二次校验，与 scrape 模块的 SSRF 防护能力对齐。"""
        from app.scrape.validator import UrlValidator

        validator = UrlValidator(settings=self.settings)
        error = await validator.check_resolved_ssrf(url)
        if error:
            raise ValueError(f"URL 校验失败：{error}")


def build_registry(settings: Settings, redis) -> SkillRegistry:
    service = CollectorService(settings, redis)
    registry = SkillRegistry()

    async def handle_crawl_requested(payload: dict, session: Session) -> None:
        url = payload.get("url")
        source_id = payload.get("source_id")
        if url:
            await service.crawl_url(session, url)
        elif source_id:
            await service.crawl_by_id(session, source_id)
        else:
            raise ValueError("crawl.requested requires 'url' or 'source_id'")

    registry.register(EVENT_CRAWL_REQUESTED, handle_crawl_requested)
    return registry
