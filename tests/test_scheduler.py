from app.collector.sources import SourceConfig
from app.orchestrator.scheduler import build_job_specs, start_scheduler


def test_build_job_specs_only_enabled():
    sources = [
        SourceConfig(id="a", name="A", type="rss", url="https://x/a", frequency_minutes=30),
        SourceConfig(id="b", name="B", type="web", url="https://x/b", frequency_minutes=60, enabled=False),
    ]
    specs = build_job_specs(sources)
    assert [s.source_id for s in specs] == ["a"]
    assert specs[0].interval_minutes == 30


async def test_start_scheduler_registers_jobs(settings, session_factory, redis, tmp_path):
    settings.sources_file = tmp_path / "sources.yaml"
    settings.sources_file.write_text(
        "sources:\n  - id: s1\n    name: S\n    type: rss\n    url: https://x/feed\n    frequency_minutes: 30\n",
        encoding="utf-8",
    )
    scheduler = start_scheduler(settings, redis, session_factory)
    assert [job.id for job in scheduler.get_jobs()] == ["crawl-s1"]
    if scheduler.running:
        scheduler.shutdown(wait=False)
