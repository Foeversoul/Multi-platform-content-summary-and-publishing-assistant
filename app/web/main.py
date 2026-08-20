from pathlib import Path
from time import monotonic
from urllib.parse import urlparse

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from app.config import Settings, get_settings
from app.events import EVENT_CRAWL_REQUESTED
from app.llm.provider import ChatProvider
from app.orchestrator.state import transition
from app.scrape.service import ScrapeService
from app.storage.models import (
    Article,
    ArticleStatus,
    EventLog,
    PlatformCopy,
    Publish,
    Review,
    Source,
    Summary,
    Verdict,
)
from app.storage.queue import EventDeliveryError, emit_event
from app.web.actions import (
    InvalidCommentError,
    ReviewNotFoundError,
)
from app.web.actions import (
    publish_copy as do_publish_copy,
)
from app.web.actions import (
    reject_copy as do_reject_copy,
)
from app.web.api import ApiError, build_api_router

TEMPLATES = Jinja2Templates(directory=Path(__file__).parent / "templates")


class _SlidingLimiter:
    """S5 内存滑动窗口限流：单窗口内每 IP 最大请求数；写操作窗口更小。"""

    def __init__(self, limit: int, window_seconds: float = 60.0, write_divisor: int = 3) -> None:
        self._limit = limit
        self._window = window_seconds
        self._write_limit = max(1, limit // write_divisor)
        self._hits: dict[str, list[float]] = {}

    def allow(self, key: str, write: bool = False) -> bool:
        limit = self._write_limit if write else self._limit
        now = monotonic()
        hits = [t for t in self._hits.get(key, []) if now - t < self._window]
        if len(hits) >= limit:
            self._hits[key] = hits
            return False
        hits.append(now)
        if hits:
            self._hits[key] = hits
        return True


def create_app(
    session_factory,
    redis,
    event_stream: str = "assistant:events",
    settings: Settings | None = None,
    scrape_service: ScrapeService | None = None,
):
    app = FastAPI(title="content-assistant")
    settings = settings or get_settings()
    app.state.session_factory = session_factory
    app.state.redis = redis
    app.state.event_stream = event_stream
    limiter = _SlidingLimiter(settings.rate_limit_per_minute)

    @app.middleware("http")
    async def enforce_rate_limit(request: Request, call_next):
        """S5 速率限制：/api 按 IP 滑动窗口限流（读 60/min、写 20/min），超限返回 429。"""
        limit = settings.rate_limit_per_minute
        if limit > 0 and request.url.path.startswith("/api"):
            client_ip = request.client.host if request.client else "unknown"
            if not limiter.allow(client_ip, write=request.method in ("POST", "PUT", "PATCH", "DELETE")):
                return JSONResponse(status_code=429, content={"code": 429, "message": "请求过于频繁，请稍后重试", "data": None})
        return await call_next(request)

    @app.middleware("http")
    async def enforce_api_token(request: Request, call_next):
        """SEC-01 基线鉴权：配置 ASSISTANT_API_TOKEN 后，/api 请求须携带 X-API-Token（健康检查除外）。"""
        if (
            settings.api_token
            and request.url.path.startswith("/api")
            and not request.url.path.startswith("/api/health")
            and request.headers.get("x-api-token", "") != settings.api_token
        ):
            return JSONResponse(status_code=401, content={"code": 401, "message": "未授权", "data": None})
        return await call_next(request)

    @app.middleware("http")
    async def csrf_same_origin(request: Request, call_next):
        """S2 轻量 CSRF 防护：HTML 表单写操作校验 Origin/Referer 同源（无 Cookie 机制下有效阻断跨站提交）。"""
        if (
            request.method in ("POST", "PUT", "PATCH", "DELETE")
            and not request.url.path.startswith("/api")
            and (source := request.headers.get("origin") or request.headers.get("referer"))
        ):
            host = request.headers.get("host", "")
            try:
                netloc = urlparse(source).netloc
            except ValueError:
                netloc = ""
            if netloc and netloc != host:
                return JSONResponse(status_code=403, content={"code": 403, "message": "跨站请求已被拒绝", "data": None})
        return await call_next(request)

    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError):
        return JSONResponse(status_code=exc.status_code, content={"code": exc.code, "message": exc.message, "data": None})

    @app.exception_handler(HTTPException)
    async def handle_http_error(request: Request, exc: HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"code": exc.status_code, "message": str(exc.detail), "data": None})

    app.include_router(build_api_router(session_factory, redis, settings, scrape_service, provider=ChatProvider(settings)))

    def _session():
        return app.state.session_factory()

    @app.get("/")
    def list_pending(request: Request, page: int = Query(1, ge=1)):
        # P4：HTML 列表分页，与 REST 接口对齐（默认 20 条/页）
        page_size = 20
        with _session() as session:
            base = (
                select(Review, PlatformCopy, Summary, Article)
                .join(PlatformCopy, PlatformCopy.id == Review.copy_id)
                .join(Summary, Summary.id == PlatformCopy.summary_id)
                .join(Article, Article.id == Summary.article_id)
                .where(Review.verdict == Verdict.PENDING)
            )
            total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
            rows = session.execute(
                base.order_by(Review.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
        pages = max(1, (total + page_size - 1) // page_size)
        return TEMPLATES.TemplateResponse(request, "list.html", {"rows": rows, "page": page, "pages": pages, "total": total})

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
            try:
                do_publish_copy(session, copy_id)
            except ReviewNotFoundError:
                raise HTTPException(status_code=404, detail="copy not found")
        return RedirectResponse(f"/copy/{copy_id}", status_code=303)

    @app.post("/copy/{copy_id}/reject")
    def reject_copy(copy_id: int, comment: str = Form("")):
        with _session() as session:
            try:
                do_reject_copy(session, copy_id, comment)
            except InvalidCommentError:
                # AC-IF-02：驳回理由必填，返回详情页提示
                return RedirectResponse(f"/copy/{copy_id}?error=comment_required", status_code=303)
            except ReviewNotFoundError:
                raise HTTPException(status_code=404, detail="copy not found")
        return RedirectResponse(f"/copy/{copy_id}", status_code=303)

    @app.get("/status")
    async def status_page(request: Request):
        with _session() as session:
            article_counts = dict(session.execute(select(Article.status, func.count()).group_by(Article.status)).all())
            copy_counts = dict(session.execute(select(PlatformCopy.status, func.count()).group_by(PlatformCopy.status)).all())
            event_counts = dict(session.execute(select(EventLog.status, func.count()).group_by(EventLog.status)).all())
            pending_reviews = session.scalar(select(func.count()).select_from(Review).where(Review.verdict == Verdict.PENDING)) or 0
            recent_events = session.scalars(select(EventLog).order_by(EventLog.created_at.desc()).limit(10)).all()
        queue_len = 0
        try:
            queue_len = await app.state.redis.xlen(app.state.event_stream)
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

    @app.get("/failed")
    def failed_list(request: Request):
        with _session() as session:
            rows = session.execute(
                select(Article, Source)
                .join(Source, Source.id == Article.source_id, isouter=True)
                .where(Article.status.in_([ArticleStatus.FAILED, ArticleStatus.DEAD_LETTER]))
                .order_by(Article.updated_at.desc())
            ).all()
        return TEMPLATES.TemplateResponse(request, "failed.html", {"rows": rows})

    @app.post("/failed/{article_id}/retry")
    async def retry_article(article_id: int):
        with _session() as session:
            article = session.get(Article, article_id)
            if article is None:
                raise HTTPException(status_code=404, detail="article not found")
            transition(ArticleStatus(article.status), ArticleStatus.PENDING)
            article.status = ArticleStatus.PENDING
            source = session.get(Source, article.source_id) if article.source_id else None
            payload = {"source_id": source.external_id} if source else {"url": article.url}
            try:
                await emit_event(app.state.redis, session, EVENT_CRAWL_REQUESTED, payload, app.state.event_stream)
            except EventDeliveryError as exc:
                raise HTTPException(status_code=503, detail="事件投递暂时失败，请稍后重试") from exc
            session.commit()
        return RedirectResponse("/failed", status_code=303)

    @app.post("/failed/{article_id}/discard")
    def discard_article(article_id: int):
        with _session() as session:
            article = session.get(Article, article_id)
            if article is None:
                raise HTTPException(status_code=404, detail="article not found")
            transition(ArticleStatus(article.status), ArticleStatus.REJECTED)
            article.status = ArticleStatus.REJECTED
            session.commit()
        return RedirectResponse("/failed", status_code=303)

    return app
