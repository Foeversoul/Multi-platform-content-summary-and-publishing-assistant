from app.processor.clean import clean_text, remove_noise_sentences, split_sentences


def test_clean_text_collapses_whitespace():
    assert clean_text("  第一段。\n\n  第二段。  ") == "第一段。 第二段。"


def test_split_sentences_on_chinese_and_ascii_terminators():
    text = "第一句。第二句！第三句?第四句；第五句\n第六句。"
    parts = split_sentences(text)
    assert len(parts) == 6
    assert parts[0] == "第一句。"


def test_remove_noise_sentences():
    sentences = ["短", "这是一句超过十个字的有效信息句子。", "这是一句超过十个字的有效信息句子。", "版权声明"]
    cleaned = remove_noise_sentences(sentences)
    assert cleaned == ["这是一句超过十个字的有效信息句子。"]
