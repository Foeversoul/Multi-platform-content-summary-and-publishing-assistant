import asyncio
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from app.storage.models import (
    Article,
    EventLog,
    PlatformCopy,
    Publish,
    PublishStatus,
    Review,
    Summary,
    Verdict,
)

TEMPLATES = Jinja2Templates(directory=Path(__file__).parent / "templates")


def create_app(session_factory, redis, event_stream: str = "assistant:events"):
    app = FastAPI(title="content-assistant")
    app.state.session_factory = session_factory
    app.state.redis = redis
    app.state.event_stream = event_stream

    def _session():
        return app.state.session_factory()

    @app.get("/")
    def list_pending(request: Request):
        with _session() as session:
            rows = session.execute(
                select(Review, PlatformCopy, Summary, Article)
                .join(PlatformCopy, PlatformCopy.id == Review.copy_id)
                .join(Summary, Summary.id == PlatformCopy.summary_id)
                .join(Article, Article.id == Summary.article_id)
                .where(Review.verdict == Verdict.PENDING)
                .order_by(Review.created_at.desc())
            ).all()
        return TEMPLATES.TemplateResponse(request, "list.html", {"rows": rows})

    @app.get("/copy/{copy_id}")
    def copy_detail(request: Request, copy_id: int):
        with _session() as session:
            row = session.execute(
                select(Review, PlatformCopy, Summary, Article)
                .join(PlatformCopy, PlatformCopy.id == Review.copy_id)
                .join(Summary, Summary.id == PlatformCopy.summary_id)
                .join(Article, Article.id == Summary.article_id)
                .where(PlatformCopy.id == copy_id)
            ).one_or_none()
            if row is None:
                raise HTTPException(status_code=404, detail="copy not found")
            review, copy, summary, article = row
            publish = session.scalar(select(Publish).where(Publish.copy_id == copy_id))
        return TEMPLATES.TemplateResponse(
            request,
            "detail.html",
            {"review": review, "copy": copy, "summary": summary, "article": article, "publish": publish},
        )

    @app.post("/copy/{copy_id}/publish")
    def publish_copy(copy_id: int):
        with _session() as session:
            review = session.scalar(select(Review).where(Review.copy_id == copy_id))
            if review is None:
                raise HTTPException(status_code=404, detail="copy not found")
            publish = session.scalar(select(Publish).where(Publish.copy_id == copy_id))
            if publish is None:
                publish = Publish(copy_id=copy_id, status=PublishStatus.PUBLISHED, published_at=datetime.now(UTC))
                session.add(publish)
            else:
                publish.status = PublishStatus.PUBLISHED
                publish.published_at = datetime.now(UTC)
            review.verdict = Verdict.PASS
            session.commit()
        return RedirectResponse(f"/copy/{copy_id}", status_code=303)

    @app.post("/copy/{copy_id}/reject")
    def reject_copy(copy_id: int, comment: str = Form("")):
        with _session() as session:
            review = session.scalar(select(Review).where(Review.copy_id == copy_id))
            if review is None:
                raise HTTPException(status_code=404, detail="copy not found")
            review.verdict = Verdict.REJECT
            review.comment = comment[:500]
            session.commit()
        return RedirectResponse(f"/copy/{copy_id}", status_code=303)

    @app.get("/status")
    def status_page(request: Request):
        with _session() as session:
            article_counts = dict(session.execute(select(Article.status, func.count()).group_by(Article.status)).all())
            copy_counts = dict(session.execute(select(PlatformCopy.status, func.count()).group_by(PlatformCopy.status)).all())
            event_counts = dict(session.execute(select(EventLog.status, func.count()).group_by(EventLog.status)).all())
            pending_reviews = session.scalar(select(func.count()).select_from(Review).where(Review.verdict == Verdict.PENDING)) or 0
            recent_events = session.scalars(select(EventLog).order_by(EventLog.created_at.desc()).limit(10)).all()
        queue_len = 0
        try:
            queue_len = asyncio.run(app.state.redis.xlen(app.state.event_stream))
        except Exception:  # noqa: BLE001 — Redis 不可达时状态页容错显示 0
            queue_len = 0
        return TEMPLATES.TemplateResponse(
            request,
            "status.html",
            {
                "article_counts": article_counts,
                "copy_counts": copy_counts,
                "event_counts": event_counts,
                "pending_reviews": pending_reviews,
                "queue_len": queue_len,
                "recent_events": recent_events,
            },
        )

    return app
