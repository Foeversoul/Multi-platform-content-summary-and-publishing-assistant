from pathlib import Path

DEFAULT_SENSITIVE_WORDS = ["代购", "刷单", "加微信", "私聊", "返利"]
DEFAULT_AD_WORDS = ["国家级", "最高级", "最佳", "第一品牌", "顶级", "全网最低"]


def load_wordlist(path: Path | None) -> list[str]:
    if path is None or not path.exists():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def find_hits(text: str, words: list[str]) -> list[str]:
    return [w for w in words if w in text]
