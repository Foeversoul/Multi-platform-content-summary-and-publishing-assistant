from pathlib import Path

from app.adapter.platforms import PlatformConfig, load_platforms


def test_load_platforms(tmp_path: Path):
    p = tmp_path / "platforms.yaml"
    p.write_text(
        """
platforms:
  weibo:
    name: 微博
    min_chars: 1
    max_chars: 140
    min_tags: 1
    max_tags: 3
    style_prompt: 口语化
""",
        encoding="utf-8",
    )
    platforms = load_platforms(p)
    assert platforms["weibo"].max_chars == 140
    assert platforms["weibo"].min_tags == 1


def test_platform_config_defaults():
    cfg = PlatformConfig(id="x", name="X", min_chars=1, max_chars=10)
    assert cfg.min_tags == 0
    assert cfg.max_emojis == 0
