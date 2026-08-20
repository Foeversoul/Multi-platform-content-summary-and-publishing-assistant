from dataclasses import dataclass

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.collector.service import upsert_sources
from app.collector.sources import SourceConfig, load_sources
from app.events import EVENT_CRAWL_REQUESTED
from app.storage.queue import emit_event


@dataclass
class JobSpec:
    source_id: str
    interval_minutes: int


def build_job_specs(sources: list[SourceConfig]) -> list[JobSpec]:
    return [JobSpec(s.id, s.frequency_minutes) for s in sources if s.enabled]


def start_scheduler(settings, redis, session_factory) -> AsyncIOScheduler:
    sources = load_sources(settings.sources_file)
    with session_factory() as session:
        upsert_sources(session, sources)
    scheduler = AsyncIOScheduler()

    async def trigger_crawl(source_id: str) -> None:
        with session_factory() as session:
            await emit_event(redis, session, EVENT_CRAWL_REQUESTED, {"source_id": source_id}, settings.event_stream)

    for spec in build_job_specs(sources):
        scheduler.add_job(
            trigger_crawl,
            "interval",
            minutes=spec.interval_minutes,
            args=[spec.source_id],
            id=f"crawl-{spec.source_id}",
            replace_existing=True,
        )
    return scheduler
