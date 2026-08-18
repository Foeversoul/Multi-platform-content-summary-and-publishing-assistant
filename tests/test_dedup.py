from app.collector.dedup import DedupService, hash_content, hamming, simhash
from app.storage.models import Article, ArticleStatus, utcnow


def test_hash_and_simhash_stable():
    text = "这是一篇关于人工智能的测试文章，讨论大模型的应用。"
    assert hash_content(text) == hash_content(text)
    assert hamming(simhash(text), simhash(text)) == 0


def test_near_duplicate_simhash_close():
    a = "今天股市大涨，科技板块领涨，投资者情绪乐观，多家机构发布研报看好后市，成交量显著放大，北向资金持续流入。"
    b = "今天股市大涨，科技板块领涨，投资者情绪非常乐观，多家机构发布研报看好后市，成交量显著放大，北向资金持续流入。"
    assert hamming(simhash(a), simhash(b)) <= 3


def test_dedup_service_exact_hash(session_factory):
    session = session_factory()
    text = "唯一正文内容用于去重测试。"
    ch = hash_content(text)
    session.add(
        Article(
            url="https://x/1",
            title="t",
            text=text,
            content_hash=ch,
            simhash_value=simhash(text),
            status=ArticleStatus.CRAWLED,
            created_at=utcnow(),
        )
    )
    session.commit()
    svc = DedupService(window_days=30, threshold=3)
    assert svc.is_duplicate(session, "https://x/1", ch, simhash(text)) is True  # URL 重复
    assert svc.is_duplicate(session, "https://x/2", ch, simhash(text)) is True  # 哈希重复
    assert svc.is_duplicate(session, "https://x/3", hash_content("完全不同的正文内容。"), simhash("完全不同的正文内容。")) is False
    session.close()
