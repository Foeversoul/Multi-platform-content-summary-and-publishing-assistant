def _bigrams(text: str) -> set[str]:
    compact = text.replace(" ", "")
    return {compact[i : i + 2] for i in range(max(len(compact) - 1, 0))}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


def score_sentences(sentences: list[str], title: str, entities: dict[str, set[str]]) -> list[float]:
    all_entities = {e for values in entities.values() for e in values}
    title_bigrams = _bigrams(title or "")
    scores: list[float] = []
    for index, sentence in enumerate(sentences):
        position = 1.0 / (index + 1)
        entity_count = sum(1 for e in all_entities if e in sentence)
        entity_score = min(1.0, entity_count / 5.0)
        title_score = _jaccard(_bigrams(sentence), title_bigrams)
        scores.append(0.3 * position + 0.4 * entity_score + 0.3 * title_score)
    return scores


def extractive_summary(sentences: list[str], scores: list[float], min_chars: int = 200, max_chars: int = 400) -> str:
    order = sorted(range(len(sentences)), key=lambda i: scores[i], reverse=True)
    selected: list[int] = []
    total = 0
    for i in order:
        length = len(sentences[i])
        if total + length > max_chars and selected:
            break
        selected.append(i)
        total += length
        if total >= min_chars:
            break
    selected.sort()
    return "".join(sentences[i] for i in selected)
