from app.adapter.platforms import PlatformConfig
from app.reviewer.quality import score_copy


def test_score_copy_clean_text():
    platform = PlatformConfig(id="weibo", name="微博", min_chars=1, max_chars=140, min_tags=1, max_tags=3)
    scores = score_copy(platform, "今日热点：#科技# 核心信息。", {"sensitive_hits": [], "ad_hits": []})
    assert scores["style_score"] == 100
    assert scores["length_ok"] is True


def test_score_copy_penalizes_violations():
    platform = PlatformConfig(id="weibo", name="微博", min_chars=1, max_chars=140, min_tags=1, max_tags=3)
    scores = score_copy(platform, "太短没标签", {"sensitive_hits": ["加微信"], "ad_hits": ["最佳"]})
    assert scores["style_score"] < 80
