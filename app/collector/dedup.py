import hashlib
import re
from datetime import UTC, datetime, timedelta

import jieba
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.storage.models import Article

SIMHASH_BITS = 64


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def hash_content(text: str) -> str:
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def tokenize(text: str) -> list[str]:
    return [t for t in jieba.cut(normalize_text(text)) if t.strip()]


def simhash(text: str, bits: int = SIMHASH_BITS) -> int:
    v = [0] * bits
    for token in tokenize(text):
        h = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16)
        for i in range(bits):
            v[i] += 1 if (h >> i) & 1 else -1
    out = 0
    for i in range(bits):
        if v[i] > 0:
            out |= 1 << i
    return out


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def to_signed(value: int) -> int:
    """将无符号 simhash 转为有符号 64 位，兼容 SQLite/PostgreSQL 的 BIGINT。"""
    return value - (1 << SIMHASH_BITS) if value >= (1 << (SIMHASH_BITS - 1)) else value


def from_signed(value: int) -> int:
    """将库中存储的有符号值还原为无符号 simhash。"""
    return value + (1 << SIMHASH_BITS) if value < 0 else value


class DedupService:
    def __init__(self, window_days: int = 30, threshold: int = 3) -> None:
        self.window_days = window_days
        self.threshold = threshold

    def is_duplicate(self, session: Session, url: str, content_hash: str, simhash_value: int) -> bool:
        if session.scalar(select(Article.id).where(Article.url == url)) is not None:
            return True
        since = datetime.now(UTC) - timedelta(days=self.window_days)
        rows = session.execute(
            select(Article.content_hash, Article.simhash_value).where(Article.created_at >= since)
        ).all()
        for existing_hash, existing_simhash in rows:
            if existing_hash == content_hash:
                return True
            if hamming(simhash_value, from_signed(existing_simhash)) <= self.threshold:
                return True
        return False
