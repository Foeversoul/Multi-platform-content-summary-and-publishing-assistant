from app.adapter.wordlists import find_hits


def check_compliance(text: str, sensitive_words: list[str], ad_words: list[str]) -> dict:
    return {"sensitive_hits": find_hits(text, sensitive_words), "ad_hits": find_hits(text, ad_words)}
