from app.processor.extractive import extractive_summary, score_sentences


def test_score_sentences_length_and_order():
    sentences = ["第一句包含张三和腾讯公司。", "第二句补充说明背景信息。", "第三句收尾。"]
    scores = score_sentences(sentences, "张三的新闻", {"PERSON": {"张三"}, "ORG": {"腾讯公司"}})
    assert len(scores) == 3
    assert scores[0] > scores[1]


def test_extractive_summary_respects_budget_and_order():
    sentences = ["第一句内容较短的句子。", "第二句内容较短的句子。", "第三句内容较短的句子。"]
    scores = [1.0, 0.5, 0.2]
    out = extractive_summary(sentences, scores, min_chars=20, max_chars=40)
    assert "第一句" in out and "第二句" in out
    assert out.index("第一句") < out.index("第二句")
