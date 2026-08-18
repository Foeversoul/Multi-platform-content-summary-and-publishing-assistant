from app.adapter.platforms import PlatformConfig
from app.adapter.rules import count_emojis, count_tags, validate_text


def test_count_tags_and_emojis():
    assert count_tags("今天分享#科技#和#AI#") == 2
    assert count_emojis("好棒😀还有🚀") == 2


def test_validate_text_weibo():
    platform = PlatformConfig(id="weibo", name="微博", min_chars=1, max_chars=140, min_tags=1, max_tags=3)
    result = validate_text(platform, "今日热点：#科技# 核心信息。")
    assert result.length_ok is True
    assert result.tags_ok is True
    assert result.ok is True


def test_validate_text_rejects_long_moments():
    platform = PlatformConfig(id="moments", name="朋友圈", min_chars=60, max_chars=200, min_emojis=1, max_emojis=3)
    result = validate_text(platform, "太短")
    assert result.length_ok is False
    assert result.ok is False
