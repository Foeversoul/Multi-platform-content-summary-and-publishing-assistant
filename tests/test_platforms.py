from pathlib import Path

from app.adapter.platforms import PlatformConfig, load_platforms


def test_default_platforms_normalized():
    """锁定各平台默认规范：微博有明确长度段并带话题，朋友圈不加话题，小红书话题+emoji 双要求。"""
    path = Path(__file__).resolve().parents[1] / "platforms.yaml"
    platforms = load_platforms(path)
    assert {"weibo", "moments", "xhs"} <= set(platforms)

    weibo = platforms["weibo"]
    assert weibo.min_chars >= 50
    assert weibo.max_chars >= 140
    assert weibo.min_tags >= 1
    assert weibo.min_emojis == 0

    moments = platforms["moments"]
    assert moments.min_emojis == 0
    assert moments.max_tags == 0  # 朋友圈不使用话题标签

    xhs = platforms["xhs"]
    assert xhs.min_tags >= 2
    assert xhs.min_emojis >= 1


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
