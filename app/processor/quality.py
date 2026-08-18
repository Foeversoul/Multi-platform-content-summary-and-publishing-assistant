from app.processor.clean import split_sentences
from app.processor.entities import extract_entities


def score_summary(article_text: str, summary_text: str, key_points: list[str], short_title: str) -> dict:
    article_entities = {e for values in extract_entities(article_text).values() for e in values}
    summary_entities = {e for values in extract_entities(summary_text).values() for e in values}
    retained = len(summary_entities & article_entities) / max(len(article_entities), 1)
    sentences = split_sentences(summary_text)
    avg_len = len(summary_text) / max(len(sentences), 1)
    return {
        "summary_len": len(summary_text),
        "length_ok": 200 <= len(summary_text) <= 400,
        "key_points_count": len(key_points),
        "short_title_len": len(short_title or ""),
        "entity_retention": round(retained, 4),
        "avg_sentence_len": round(avg_len, 2),
    }
