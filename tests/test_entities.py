from app.processor.entities import extract_entities
from app.processor.keywords import extract_keywords


def test_extract_entities_categories():
    text = "张三在北京市参观了腾讯公司，会议于2026年8月18日举行，参会人数500人。"
    entities = extract_entities(text)
    assert "PERSON" in entities and "张三" in entities["PERSON"]
    assert "LOCATION" in entities and "北京市" in entities["LOCATION"]
    assert "ORG" in entities
    assert "DATE" in entities and "2026年8月18日" in entities["DATE"]
    assert "NUMBER" in entities and "500" in entities["NUMBER"]


def test_extract_keywords_returns_terms():
    text = "人工智能大模型在医疗影像诊断中展现出显著优势，大模型提升了诊断准确率与效率。"
    keywords = extract_keywords(text, top_k=5)
    assert len(keywords) >= 1
    assert any("大模型" in k or "诊断" in k for k in keywords)
