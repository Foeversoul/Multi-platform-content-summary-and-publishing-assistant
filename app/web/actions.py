"""审核业务服务层（F3/Q4）：HTML 路由与 REST API 共用，消除双实现行为漂移。

包含发布幂等（E3）与驳回理由必填校验（AC-IF-02）。
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.storage.models import Publish, PublishStatus, Review, Verdict


class ReviewNotFoundError(Exception):
    """文案审核记录不存在。"""

    def __init__(self, copy_id: int) -> None:
        self.copy_id = copy_id
        super().__init__(f"review for copy #{copy_id} not found")


class InvalidCommentError(ValueError):
    """驳回理由为空（AC-IF-02）。"""


def publish_copy(session: Session, copy_id: int) -> Publish:
    """发布文案：幂等 upsert；并发双击由唯一约束兜底后重查（E3）。"""
    review = session.scalar(select(Review).where(Review.copy_id == copy_id))
    if review is None:
        raise ReviewNotFoundError(copy_id)
    try:
        publish = _upsert_publish(session, copy_id)
        review.verdict = Verdict.PASS
        session.commit()
    except IntegrityError:
        # 并发竞态：另一请求已插入 Publish，回滚后重查重写
        session.rollback()
        review = session.scalar(select(Review).where(Review.copy_id == copy_id))
        if review is None:
            raise ReviewNotFoundError(copy_id)
        publish = _upsert_publish(session, copy_id)
        review.verdict = Verdict.PASS
        session.commit()
    return publish


def publish_pending_all(session: Session) -> int:
    """一键通过：仅处理 verdict=pending 的文案，发布并标记通过，返回处理条数。"""
    rows = session.execute(
        select(Review.copy_id).where(Review.verdict == Verdict.PENDING).order_by(Review.copy_id)
    ).all()
    published = 0
    for (copy_id,) in rows:
        publish_copy(session, copy_id)
        published += 1
    return published


def _upsert_publish(session: Session, copy_id: int) -> Publish:
    publish = session.scalar(select(Publish).where(Publish.copy_id == copy_id))
    now = datetime.now(UTC)
    if publish is None:
        publish = Publish(copy_id=copy_id, status=PublishStatus.PUBLISHED, published_at=now)
        session.add(publish)
    else:
        publish.status = PublishStatus.PUBLISHED
        publish.published_at = now
    return publish


def reject_copy(session: Session, copy_id: int, comment: str) -> Review:
    """驳回文案：驳回理由必填（AC-IF-02），超长截断至 500 字符。"""
    comment = (comment or "").strip()
    if not comment:
        raise InvalidCommentError("驳回理由 comment 必填")
    review = session.scalar(select(Review).where(Review.copy_id == copy_id))
    if review is None:
        raise ReviewNotFoundError(copy_id)
    review.verdict = Verdict.REJECT
    review.comment = comment[:500]
    session.commit()
    return review
