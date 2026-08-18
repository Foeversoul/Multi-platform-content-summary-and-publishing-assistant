import re


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？!?；;])\s*|\n+", text or "")
    return [re.sub(r"\s+", " ", p).strip() for p in parts if p.strip()]


def _is_informative(sentence: str) -> bool:
    return len(sentence) >= 10 or bool(re.search(r"[0-9A-Za-z]", sentence))


def remove_noise_sentences(sentences: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for s in sentences:
        if not _is_informative(s):
            continue
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out
