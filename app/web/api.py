"""REST JSON API（PRD IF-01~13）。

统一响应包：{"code": 0, "message": "ok", "data": ...}；业务失败 code != 0。
与现有 HTML 路由并存，统一前缀 /api。
"""

import json
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Body, File, Query, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import delete, func, select

from app.config import Settings
from app.scrape.errors import QuotaExceededError, ScrapeError
from app.scrape.service import ScrapeService
from app.storage.models import (
    Article,
    ChatMessage,
    EventLog,
    EventStatus,
    PlatformCopy,
    Publish,
    Review,
    ScrapeJob,
    Summary,
    Verdict,
)
from app.storage.queue import EventDeliveryError, emit_event
from app.web.actions import (
    InvalidCommentError,
    ReviewNotFoundError,
    publish_copy,
    publish_pending_all,
    reject_copy,
)
from app.web.chat_assistant import chat_assistant
from app.web.content_ops import ContentOpsService, CopyNotFoundError, PublishedCopyError, extract_docx_text


class ApiError(Exception):
    """业务错误，由 create_app 统一转换为 JSON 响应包。"""

    def __init__(self, status_code: int, code: int, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)


def ok(data):
    return {"code": 0, "message": "ok", "data": data}


def build_api_router(session_factory, redis, settings: Settings, scrape_service: ScrapeService | None = None, provider=None) -> APIRouter:
    router = APIRouter(prefix="/api")
    service = scrape_service or ScrapeService(settings, redis=redis)
    content_ops = ContentOpsService(settings, provider=provider)

    def _dt(value) -> str | None:
        return value.isoformat() if value is not None else None

    # ---------- IF-01~04 审核 ----------

    @router.get("/reviews")
    def list_reviews(
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        platform: str | None = None,
        verdict: str | None = None,
        keyword: str | None = None,
    ):
        with session_factory() as session:
            stmt = (
                select(Review, PlatformCopy, Summary, Article)
                .join(PlatformCopy, PlatformCopy.id == Review.copy_id)
                .join(Summary, Summary.id == PlatformCopy.summary_id)
                .join(Article, Article.id == Summary.article_id)
                .where(PlatformCopy.deleted_at.is_(None))
            )
            if platform:
                stmt = stmt.where(PlatformCopy.platform == platform)
            if verdict:
                stmt = stmt.where(Review.verdict == verdict)
            if keyword and len(keyword.strip()) >= 2:
                # P3：最小关键词长度 2，避免单字符 LIKE 全表扫描
                stmt = stmt.where(Article.title.contains(keyword) | PlatformCopy.text.contains(keyword))
            # 按文章聚合：一篇内容只返回一行，各平台副本放 platforms 供切换
            rows = session.execute(stmt.order_by(Review.created_at.desc())).all()
            groups: dict[int, dict] = {}
            for review, copy, _summary, article in rows:
                group = groups.setdefault(
                    article.id,
                    {
                        "article_id": article.id,
                        "article_title": article.title,
                        "platforms": [],
                        "created_at": _dt(review.created_at),
                    },
                )
                group["platforms"].append(
                    {
                        "copy_id": copy.id,
                        "review_id": review.id,
                        "platform": copy.platform,
                        "verdict": review.verdict.value,
                        "scores": review.scores or {},
                    }
                )
                if review.verdict == Verdict.PENDING and group.get("_default") is None:
                    group["_default"] = {
                        "copy_id": copy.id,
                        "review_id": review.id,
                        "scores": review.scores or {},
                    }
            items: list[dict] = []
            for group in groups.values():
                default = group.get("_default") or {
                    "copy_id": group["platforms"][0]["copy_id"],
                    "review_id": group["platforms"][0]["review_id"],
                    "scores": group["platforms"][0]["scores"],
                }
                group.pop("_default", None)
                group["copy_id"] = default["copy_id"]
                group["review_id"] = default["review_id"]
                group["scores"] = default["scores"]
                group["platform"] = next(
                    (p["platform"] for p in group["platforms"] if p["copy_id"] == default["copy_id"]),
                    group["platforms"][0]["platform"],
                )
                group["platform_count"] = len(group["platforms"])
                group["verdict"] = (
                    Verdict.PENDING.value
                    if any(p["verdict"] == Verdict.PENDING.value for p in group["platforms"])
                    else group["platforms"][0]["verdict"]
                )
                items.append(group)
            items.sort(key=lambda it: it["created_at"] or "", reverse=True)
            total = len(items)
            page_items = items[(page - 1) * page_size : page * page_size]
        return ok({"items": page_items, "total": total})

    @router.get("/reviews/{copy_id}")
    def review_detail(copy_id: int):
        with session_factory() as session:
            row = session.execute(
                select(Review, PlatformCopy, Summary, Article)
                .join(PlatformCopy, PlatformCopy.id == Review.copy_id)
                .join(Summary, Summary.id == PlatformCopy.summary_id)
                .join(Article, Article.id == Summary.article_id)
                .where(PlatformCopy.id == copy_id)
            ).one_or_none()
            if row is None or row[1].deleted_at is not None:
                raise ApiError(404, 404, "文案不存在")
            review, copy, summary, article = row
            publish = session.scalar(select(Publish).where(Publish.copy_id == copy_id))
            # 同摘要的其他平台文案（U2 多平台聚合视图）
            sibling_rows = session.execute(
                select(PlatformCopy, Review)
                .join(Review, Review.copy_id == PlatformCopy.id)
                .where(PlatformCopy.summary_id == summary.id, PlatformCopy.id != copy_id)
                .order_by(PlatformCopy.platform)
            ).all()
            siblings = [
                {
                    "copy_id": scopy.id,
                    "platform": scopy.platform,
                    "text": scopy.text,
                    "verdict": sreview.verdict,
                }
                for scopy, sreview in sibling_rows
            ]
        return ok(
            {
                "review": {"id": review.id, "verdict": review.verdict, "scores": review.scores or {}, "comment": review.comment, "created_at": _dt(review.created_at)},
                "copy": {"id": copy.id, "platform": copy.platform, "text": copy.text, "status": copy.status},
                "summary": {"id": summary.id, "summary_text": summary.summary_text, "key_points": summary.key_points, "short_title": summary.short_title},
                "article": {"id": article.id, "url": article.url, "title": article.title, "publish_time": _dt(article.publish_time), "text": article.text},
                "publish": {"status": publish.status, "published_at": _dt(publish.published_at)} if publish else None,
                "siblings": siblings,
            }
        )

    @router.post("/reviews/{copy_id}/publish")
    def publish_review(copy_id: int):
        with session_factory() as session:
            copy = session.get(PlatformCopy, copy_id)
            if copy is None or copy.deleted_at is not None:
                raise ApiError(404, 404, "文案不存在")
            try:
                publish = publish_copy(session, copy_id)
            except ReviewNotFoundError as exc:
                raise ApiError(404, 404, "文案不存在") from exc
        return ok({"copy_id": copy_id, "verdict": "pass", "published_at": _dt(publish.published_at)})

    @router.post("/reviews/{copy_id}/reject")
    def reject_review(copy_id: int, body: dict = Body(...)):  # noqa: B008 — FastAPI 依赖注入惯用法
        comment = (body.get("comment") or "").strip()
        with session_factory() as session:
            copy = session.get(PlatformCopy, copy_id)
            if copy is None or copy.deleted_at is not None:
                raise ApiError(404, 404, "文案不存在")
            try:
                review = reject_copy(session, copy_id, comment)
            except InvalidCommentError as exc:
                raise ApiError(400, 400, "驳回理由 comment 必填") from exc  # AC-IF-02
            except ReviewNotFoundError as exc:
                raise ApiError(404, 404, "文案不存在") from exc
        return ok({"copy_id": copy_id, "verdict": "reject", "comment": review.comment})

    # ---------- 内容 AI 处理（摘要重生成/编辑、扩写重生成/预览） ----------

    @router.post("/reviews/{copy_id}/summary/regenerate")
    async def regenerate_summary(copy_id: int):
        """AI 重新生成摘要并级联重写全部平台文案（已发布除外），单步 ≤3s。"""
        with session_factory() as session:
            try:
                data = await content_ops.regenerate_summary(session, copy_id)
            except CopyNotFoundError as exc:
                raise ApiError(404, 404, "文案不存在") from exc
        return ok(data)

    @router.put("/reviews/{copy_id}/summary")
    async def edit_summary(copy_id: int, body: dict = Body(...)):  # noqa: B008
        """手动编辑摘要（summary_text 必填），并级联重写全部平台文案。"""
        with session_factory() as session:
            try:
                data = await content_ops.update_summary(
                    session,
                    copy_id,
                    summary_text=body.get("summary_text", ""),
                    key_points=body.get("key_points"),
                    short_title=body.get("short_title", ""),
                )
            except CopyNotFoundError as exc:
                raise ApiError(404, 404, "文案不存在") from exc
            except ValueError as exc:
                raise ApiError(400, 400, str(exc)) from exc
        return ok(data)

    @router.post("/reviews/{copy_id}/copy/regenerate")
    async def regenerate_copy(copy_id: int):
        """重新扩写单条平台文案（≤3s）；已发布文案禁止改写。"""
        with session_factory() as session:
            try:
                data = await content_ops.regenerate_copy(session, copy_id)
            except CopyNotFoundError as exc:
                raise ApiError(404, 404, "文案不存在") from exc
            except PublishedCopyError as exc:
                raise ApiError(400, 400, "该文案已发布，不可重新生成") from exc
            except ValueError as exc:
                raise ApiError(400, 400, str(exc)) from exc
        return ok(data)

    @router.post("/reviews/{copy_id}/copy/preview")
    async def preview_copy(copy_id: int, body: dict = Body(...)):  # noqa: B008
        """按指定平台风格预览扩写文案（不落库，≤3s）。"""
        platform = (body.get("platform") or "").strip()
        with session_factory() as session:
            try:
                data = await content_ops.preview_copy(session, copy_id, platform)
            except CopyNotFoundError as exc:
                raise ApiError(404, 404, "文案不存在") from exc
            except ValueError as exc:
                raise ApiError(400, 400, str(exc)) from exc
        return ok(data)

    # ---------- 内容管理（删除 / 回收站） ----------

    @router.post("/reviews/batch-publish")
    def batch_publish_reviews():
        """一键通过全部待审核文案（并发安全，仅处理 verdict=pending）。"""
        with session_factory() as session:
            count = publish_pending_all(session)
        return ok({"published": count})

    @router.post("/reviews/batch-delete")
    def batch_delete_reviews(body: dict = Body(...)):  # noqa: B008
        """批量删除（软删除，移入回收站）。"""
        copy_ids = body.get("copy_ids")
        if not copy_ids or not isinstance(copy_ids, list) or not all(isinstance(i, int) for i in copy_ids):
            raise ApiError(400, 400, "请求体需包含非空 copy_ids 整数列表")
        with session_factory() as session:
            count = content_ops.batch_delete(session, copy_ids)
        return ok({"deleted": count})

    @router.post("/reviews/{copy_id}/delete")
    def delete_review(copy_id: int):
        """删除单条文案（软删除，移入回收站）。"""
        with session_factory() as session:
            try:
                content_ops.delete_copy(session, copy_id)
            except CopyNotFoundError as exc:
                raise ApiError(404, 404, "文案不存在") from exc
        return ok({"copy_id": copy_id, "deleted": True})

    @router.get("/recycle")
    def list_recycle(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
        """回收站列表（已删除文案）。"""
        with session_factory() as session:
            items, total = content_ops.list_recycle(session, page, page_size)
        return ok({"items": items, "total": total})

    @router.post("/recycle/{copy_id}/restore")
    def restore_review(copy_id: int):
        """从回收站恢复文案。"""
        with session_factory() as session:
            try:
                content_ops.restore_copy(session, copy_id)
            except CopyNotFoundError as exc:
                raise ApiError(404, 404, "回收站中不存在该文案") from exc
        return ok({"copy_id": copy_id, "restored": True})

    @router.delete("/recycle/{copy_id}")
    def purge_review(copy_id: int):
        """永久删除文案（连带审核/发布记录，不可恢复）。"""
        with session_factory() as session:
            try:
                content_ops.purge_copy(session, copy_id)
            except CopyNotFoundError as exc:
                raise ApiError(404, 404, "回收站中不存在该文案") from exc
        return ok({"copy_id": copy_id, "purged": True})

    @router.post("/recycle/batch-restore")
    def batch_restore_recycle(body: dict = Body(...)):  # noqa: B008
        """批量恢复回收站中的文案。"""
        copy_ids = body.get("copy_ids")
        if not copy_ids or not isinstance(copy_ids, list) or not all(isinstance(i, int) for i in copy_ids):
            raise ApiError(400, 400, "请求体需包含非空 copy_ids 整数列表")
        with session_factory() as session:
            count = content_ops.batch_restore(session, copy_ids)
        return ok({"restored": count})

    @router.post("/recycle/batch-purge")
    def batch_purge_recycle(body: dict = Body(...)):  # noqa: B008
        """批量永久删除回收站中的文案（连带审核/发布记录，不可恢复）。"""
        copy_ids = body.get("copy_ids")
        if not copy_ids or not isinstance(copy_ids, list) or not all(isinstance(i, int) for i in copy_ids):
            raise ApiError(400, 400, "请求体需包含非空 copy_ids 整数列表")
        with session_factory() as session:
            count = content_ops.batch_purge(session, copy_ids)
        return ok({"purged": count})

    # ---------- 手动内容上传 ----------

    @router.post("/content/manual")
    async def create_manual_content(body: dict = Body(...)):  # noqa: B008
        """手动上传文本/Markdown 内容：同步完成 摘要 → 全平台扩写 → 进入待审。"""
        title = body.get("title", "")
        content = body.get("content", "")
        with session_factory() as session:
            try:
                data = await content_ops.create_manual_content(session, title=title, content=content)
            except ValueError as exc:
                raise ApiError(400, 400, str(exc)) from exc
        return ok(data)

    @router.post("/content/manual/file")
    async def create_manual_file(file: UploadFile = File(...)):  # noqa: B008
        """手动上传文件（.txt / .md / .docx），内容提取后走完整 AI 处理流程。"""
        filename = (file.filename or "").lower()
        suffix = filename.rsplit(".", 1)[-1] if "." in filename else ""
        if suffix not in {"txt", "md", "docx"}:
            raise ApiError(400, 400, "仅支持 .txt / .md / .docx 文件")
        raw = await file.read()
        if len(raw) > 2 * 1024 * 1024:
            raise ApiError(400, 400, "文件过大（上限 2MB）")
        if suffix == "docx":
            content = extract_docx_text(raw)
        else:
            content = raw.decode("utf-8", errors="replace")
        title = filename.rsplit(".", 1)[0]
        with session_factory() as session:
            try:
                data = await content_ops.create_manual_content(session, title=title, content=content)
            except ValueError as exc:
               raise ApiError(400, 400, str(exc)) from exc
        return ok(data)

    # ---------- AI 对话助手 ----------

    @router.post("/chat")
    async def chat(body: dict = Body(...)):  # noqa: B008
        """AI 对话助手：既能问答，也能按用户需求执行模块动作。"""
        message = (body.get("message") or "").strip()
        if not message:
            raise ApiError(400, 400, "消息不能为空")
        reply = await chat_assistant(session_factory, provider, message, content_ops, scrape_service=service)
        return ok({"reply": reply["text"], "source": reply["source"], "kind": reply.get("kind"), "data": reply.get("data")})

    @router.get("/chat/history")
    def chat_history(limit: int = Query(50, ge=1, le=200)):
        """查询最近 24 小时的对话历史（时间正序）。"""
        since = datetime.now(UTC) - timedelta(hours=24)
        with session_factory() as session:
            rows = session.scalars(
                select(ChatMessage)
                .where(ChatMessage.created_at >= since)
                .order_by(ChatMessage.id.desc())
                .limit(limit)
            ).all()
            items = [
                {"id": row.id, "role": row.role, "text": row.text, "created_at": _dt(row.created_at)}
                for row in reversed(rows)
            ]
        return ok({"items": items, "total": len(items)})

    @router.post("/chat/history/clear")
    def clear_chat_history():
        """清空对话记忆（24 小时记忆一并清除）。"""
        with session_factory() as session:
            result = session.execute(delete(ChatMessage))
            session.commit()
        return ok({"cleared": result.rowcount or 0})

    # ---------- IF-05 运行状态 ----------

    @router.get("/health")
    async def health_check():
        """健康检查（E6）：DB/Redis 连通性探针，供 Docker HEALTHCHECK 与监控使用。"""
        database_ok = True
        try:
            with session_factory() as session:
                session.execute(select(1))
        except Exception:  # noqa: BLE001 探针失败仅标记不健康
            database_ok = False
        redis_ok = True
        try:
            await redis.ping()
        except Exception:  # noqa: BLE001 探针失败仅标记不健康
            redis_ok = False
        healthy = database_ok and redis_ok
        return JSONResponse(
            status_code=200 if healthy else 503,
            content={
                "code": 0 if healthy else 1,
                "message": "ok" if healthy else "unhealthy",
                "data": {"database": database_ok, "redis": redis_ok},
            },
        )

    @router.get("/status")
    async def api_status():
        with session_factory() as session:
            article_counts = dict(session.execute(select(Article.status, func.count()).group_by(Article.status)).all())
            copy_counts = dict(session.execute(select(PlatformCopy.status, func.count()).group_by(PlatformCopy.status)).all())
            copy_by_platform = dict(
                session.execute(select(PlatformCopy.platform, func.count()).group_by(PlatformCopy.platform)).all()
            )
            event_counts = dict(session.execute(select(EventLog.status, func.count()).group_by(EventLog.status)).all())
            pending_reviews = session.scalar(select(func.count()).select_from(Review).where(Review.verdict == Verdict.PENDING)) or 0
            review_verdicts = dict(session.execute(select(Review.verdict, func.count()).group_by(Review.verdict)).all())
            publish_count = session.scalar(select(func.count()).select_from(Publish)) or 0
            summary_count = session.scalar(select(func.count()).select_from(Summary)) or 0
            failed_events = event_counts.get(EventStatus.DEAD.value, 0)
            job_counts = dict(session.execute(select(ScrapeJob.status, func.count()).group_by(ScrapeJob.status)).all())
            event_type_counts = {
                "crawl.requested": session.scalar(
                    select(func.count()).select_from(EventLog).where(EventLog.event_type == "crawl.requested")
                )
                or 0,
                "article.crawled": session.scalar(
                    select(func.count()).select_from(EventLog).where(EventLog.event_type == "article.crawled")
                )
                or 0,
                "summary.generated": session.scalar(
                    select(func.count()).select_from(EventLog).where(EventLog.event_type == "summary.generated")
                )
                or 0,
                "copy.adapted": session.scalar(
                    select(func.count()).select_from(EventLog).where(EventLog.event_type == "copy.adapted")
                )
                or 0,
                "review.passed": session.scalar(
                    select(func.count()).select_from(EventLog).where(EventLog.event_type == "review.passed")
                )
                or 0,
            }
        queue_len = 0
        try:
            queue_len = await redis.xlen(settings.event_stream)
        except Exception:  # noqa: BLE001 - Redis unreachable, show 0
            queue_len = 0
        return ok(
            {
                "stream_lengths": {settings.event_stream: queue_len},
                "event_counts": event_counts,
                "event_types": event_type_counts,
                "article_counts": article_counts,
                "copy_counts": copy_counts,
                "copy_by_platform": copy_by_platform,
                "review_verdicts": review_verdicts,
                "publish_count": publish_count,
                "summary_count": summary_count,
                "failed_counts": {"events": failed_events, "articles": article_counts.get("dead_letter", 0)},
                "pending_reviews": pending_reviews,
                "scrape_jobs": job_counts,
            }
        )


    # ---------- IF-06~08 死信（PRD：基于 EventLog） ----------

    @router.get("/failed")
    def failed_events(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
        with session_factory() as session:
            stmt = select(EventLog).where(EventLog.status == EventStatus.DEAD)
            total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
            rows = session.scalars(stmt.order_by(EventLog.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
            items = [
                {
                    "event_id": e.id,
                    "event_type": e.event_type,
                    "error": e.error or "",
                    "payload": json.loads(e.payload),
                    "created_at": _dt(e.created_at),
                }
                for e in rows
            ]
        return ok({"items": items, "total": total})

    @router.post("/failed/{event_id}/retry")
    async def retry_event(event_id: str):
        with session_factory() as session:
            log = session.get(EventLog, event_id)
            if log is None or log.status != EventStatus.DEAD:
                raise ApiError(404, 404, "死信事件不存在")
            # 重新入队：以同类型/同 payload 投递新事件，旧事件标记已处理避免重复消费
            try:
                await emit_event(redis, session, log.event_type, json.loads(log.payload), settings.event_stream)
            except EventDeliveryError as exc:
                # 投递失败保持死信原状，由调用方稍后重试（不会造成重复）
                raise ApiError(503, 503, "事件投递暂时失败，请稍后重试") from exc
            log.status = EventStatus.PROCESSED
            log.processed_at = datetime.now(UTC)
            session.commit()
        return ok({"event_id": event_id, "status": "retried"})

    @router.post("/failed/{event_id}/discard")
    def discard_event(event_id: str):
        with session_factory() as session:
            log = session.get(EventLog, event_id)
            if log is None or log.status != EventStatus.DEAD:
                raise ApiError(404, 404, "死信事件不存在")
            log.status = EventStatus.DISCARDED
            log.processed_at = datetime.now(UTC)
            session.commit()
        return ok({"event_id": event_id, "status": "discarded"})

    # ---------- IF-09~13 URL 上传爬取 ----------

    @router.get("/scrape/jobs")
    def scrape_jobs(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
        """爬取任务历史列表（U1）。"""
        with session_factory() as session:
            stmt = select(ScrapeJob).order_by(ScrapeJob.created_at.desc())
            total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
            rows = session.scalars(stmt.offset((page - 1) * page_size).limit(page_size)).all()
            items = [
                {
                    "job_id": job.id,
                    "status": job.status,
                    "url_count": job.url_count,
                    "succeeded_count": job.succeeded_count,
                    "failed_count": job.failed_count,
                    "created_at": _dt(job.created_at),
                    "finished_at": _dt(job.finished_at),
                }
                for job in rows
            ]
        return ok({"items": items, "total": total})

    @router.post("/scrape/jobs")
    def create_scrape_job(body: dict = Body(...), background_tasks: BackgroundTasks = None):  # noqa: B008 — FastAPI 依赖注入惯用法; type: ignore[assignment]
        urls = body.get("urls")
        if not urls or not isinstance(urls, list) or not all(isinstance(u, str) for u in urls):
            raise ApiError(400, 400, "请求体需包含非空 url 字符串列表")  # AC-IF-03
        with session_factory() as session:
            try:
                job, dedup_count = service.create_job(session, urls)
            except QuotaExceededError as exc:
                raise ApiError(429, 429, exc.message) from exc
            except ScrapeError as exc:
                raise ApiError(400, 400, exc.message) from exc
            job_id, url_count = job.id, job.url_count
        if background_tasks is not None:
            background_tasks.add_task(service.run_job, session_factory, job_id)
        return ok({"job_id": job_id, "status": "pending", "url_count": url_count, "dedup_count": dedup_count})

    @router.get("/scrape/jobs/{job_id}")
    def scrape_job_detail(job_id: int):
        with session_factory() as session:
            job = service.get_job(session, job_id)
            if job is None:
                raise ApiError(404, 404, "任务不存在")
        return ok(
            {
                "job_id": job.id,
                "status": job.status,
                "url_count": job.url_count,
                "succeeded_count": job.succeeded_count,
                "failed_count": job.failed_count,
                "created_at": _dt(job.created_at),
                "finished_at": _dt(job.finished_at),
            }
        )

    @router.get("/scrape/jobs/{job_id}/items")
    def scrape_job_items(
        job_id: int,
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        status: str | None = None,
    ):
        with session_factory() as session:
            if service.get_job(session, job_id) is None:
                raise ApiError(404, 404, "任务不存在")
            rows, total = service.get_items(session, job_id, page, page_size, status)
            items = [
                {
                    "item_id": it.id,
                    "url": it.url,
                    "status": it.status,
                    "error_code": it.error_code,
                    "error_message": it.error_message,
                    "article_id": it.article_id,
                    "created_at": _dt(it.created_at),
                    "finished_at": _dt(it.finished_at),
                }
                for it in rows
            ]
        return ok({"items": items, "total": total})

    @router.get("/scrape/items/{item_id}")
    def scrape_item_detail(item_id: int):
        with session_factory() as session:
            item = service.get_item(session, item_id)
            if item is None:
                raise ApiError(404, 404, "爬取条目不存在")
        return ok(
            {
                "item_id": item.id,
                "url": item.url,
                "status": item.status,
                "error_code": item.error_code,
                "error_message": item.error_message,
                "article_id": item.article_id,
                "created_at": _dt(item.created_at),
                "finished_at": _dt(item.finished_at),
            }
        )

    @router.post("/scrape/jobs/{job_id}/items/{item_id}/retry")
    def retry_scrape_item(job_id: int, item_id: int, background_tasks: BackgroundTasks = None):  # type: ignore[assignment]
        with session_factory() as session:
            item = service.get_item(session, item_id)
            if item is None or item.job_id != job_id:
                raise ApiError(404, 404, "爬取条目不存在")
            try:
                job = service.create_retry_job(session, item_id)
            except QuotaExceededError as exc:
                raise ApiError(429, 429, exc.message) from exc
            except ScrapeError as exc:
                raise ApiError(400, 400, exc.message) from exc
            new_job_id = job.id
        if background_tasks is not None:
            background_tasks.add_task(service.run_job, session_factory, new_job_id)
        return ok({"new_job_id": new_job_id, "status": "created"})

    return router
