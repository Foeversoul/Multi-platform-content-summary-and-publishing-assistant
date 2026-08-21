from app.collector.video import (
    build_video_candidate,
    clean_video_noise,
    resolve_video_platform,
)


def test_resolve_video_platform_detects_bilibili_and_youtube():
    assert resolve_video_platform("https://www.bilibili.com/video/BV1xxxx") == "bilibili"
    assert resolve_video_platform("https://b23.tv/abc123") == "bilibili"
    assert resolve_video_platform("https://www.youtube.com/watch?v=abc") == "youtube"
    assert resolve_video_platform("https://youtu.be/abc") == "youtube"
    assert resolve_video_platform("https://example.com/article/1") is None


def test_clean_video_noise_removes_irrelevant_info():
    text = (
        "镜头一：产品拆解。\n"
        "关注主播不迷路\n"
        "相关推荐：另一个视频\n"
        "https://example.com/x\n"
        "#科技#\n"
        "镜头二：实测对比。"
    )
    cleaned = clean_video_noise(text)
    assert "镜头一：产品拆解。" in cleaned
    assert "镜头二：实测对比。" in cleaned
    assert "关注主播" not in cleaned
    assert "相关推荐" not in cleaned
    assert "http" not in cleaned
    assert "#科技#" not in cleaned


def test_build_video_candidate_extracts_summary_and_chapters():
    html = """
    <html><head>
      <meta property="og:title" content="芯片对比实测">
      <meta name="description" content="本期实测两款主流芯片的性能与功耗表现。">
    </head><body>
      芯片对比实测
      00:00 开场
      01:23 性能跑分
      03:45 功耗测试
      关注主播
      相关推荐：其他视频
      #科技#
    </body></html>
    """
    candidate = build_video_candidate(html, "https://www.bilibili.com/video/BV1x")
    assert candidate is not None
    assert candidate.title == "芯片对比实测"
    assert "本期实测两款主流芯片的性能与功耗表现。" in candidate.text
    assert "时间戳大纲：" in candidate.text
    assert "00:00 开场" in candidate.text
    assert "01:23 性能跑分" in candidate.text
    assert "关注主播" not in candidate.text


def test_build_video_candidate_returns_none_when_empty():
    html = "<html><head><title>空页面</title></head><body></body></html>"
    assert build_video_candidate(html, "https://www.bilibili.com/video/BV1x") is None
