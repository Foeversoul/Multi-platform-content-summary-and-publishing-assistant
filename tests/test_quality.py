from app.processor.quality import score_summary


def test_score_summary_metrics():
    article = "张三团队在北京市发布了人工智能研究成果，2026年8月18日举行发布会，参会人数500人。"
    summary = "张三团队在北京市发布研究成果，2026年8月18日举行发布会。"
    scores = score_summary(article, summary, ["要点一", "要点二", "要点三"], "精简标题")
    assert scores["length_ok"] is False  # 短摘要低于 200 字下限；True 分支由集成测试覆盖
    assert scores["key_points_count"] == 3
    assert scores["short_title_len"] == 4
    assert scores["entity_retention"] >= 0.5
    assert scores["avg_sentence_len"] > 0
