from app.collector.sources import load_sources
from app.config import Settings


def test_settings_defaults():
    s = Settings()
    assert s.dedup_window_days == 30
    assert s.event_stream == "assistant:events"
    assert s.min_domain_interval_seconds == 1.0


def test_load_sources(tmp_path):
    p = tmp_path / "sources.yaml"
    p.write_text(
        """
sources:
  - id: demo-news
    name: 示例新闻
    type: rss
    url: https://example.com/feed.xml
    frequency_minutes: 60
  - id: demo-blog
    name: 示例博客
    type: web
    url: https://example.com/blog
""",
        encoding="utf-8",
    )
    sources = load_sources(p)
    assert len(sources) == 2
    assert sources[0].type == "rss"
    assert sources[0].enabled is True
    assert sources[1].frequency_minutes == 60


def test_invalid_source_type_rejected(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("sources:\n  - id: x\n    name: x\n    type: ftp\n    url: http://a\n", encoding="utf-8")
    try:
        load_sources(p)
    except Exception as exc:
        assert "type" in str(exc)
    else:
        raise AssertionError("ftp type should be rejected")
