from app.adapter.wordlists import DEFAULT_AD_WORDS, DEFAULT_SENSITIVE_WORDS, find_hits, load_wordlist


def test_default_wordlists_nonempty():
    assert DEFAULT_SENSITIVE_WORDS
    assert DEFAULT_AD_WORDS


def test_find_hits():
    hits = find_hits("全网最低价，加微信详聊", DEFAULT_SENSITIVE_WORDS + DEFAULT_AD_WORDS)
    assert "加微信" in hits


def test_load_wordlist(tmp_path):
    p = tmp_path / "words.txt"
    p.write_text("词一\n词二\n\n词三\n", encoding="utf-8")
    assert load_wordlist(p) == ["词一", "词二", "词三"]
    assert load_wordlist(None) == []
