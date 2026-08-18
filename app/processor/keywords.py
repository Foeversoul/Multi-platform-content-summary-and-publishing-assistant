from jieba import analyse


def extract_keywords(text: str, top_k: int = 10) -> list[str]:
    return [w for w in analyse.extract_tags(text, topK=top_k) if w.strip()]
