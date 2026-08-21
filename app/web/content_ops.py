"""待审内容 AI 操作服务层（摘要重生成/编辑、扩写重生成/预览、删除与回收站、手动内容上传）。

性能约束（需求：单次 AI 处理 ≤3s）：
- 所有 AI 调用经 generate_summary_quick / generate_copy_quick 限时包装，
  LLM 未配置或超时时自动回退到确定性 fallback（抽取式摘要/模板扩写），接口不卡死。
"""

import asyncio
import io
import uuid
from datetime import UTC, datetime
from xml.etree import ElementTree
from zipfile import ZipFile

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.adapter.copywriter import generate_copy_quick, next_fallback_variant
from app.adapter.platforms import load_platforms
from app.collector.dedup import hash_content, simhash, to_signed
from app.config import Settings
from app.processor.quality import score_summary
from app.processor.summarizer import SummarizerResult, generate_summary_quick
from app.storage.models import (
    Article,
    ArticleStatus,
    CopyStatus,
    PlatformCopy,
    Publish,
    Review,
    Summary,
    SummaryStatus,
    Verdict,
)

# LLM 单次生成超时上限（秒），与需求"单次处理不超过 3 秒"对齐
AI_TIMEOUT_SECONDS = 3.0

# docx 命名空间（wordprocessingml 主文档）
_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class CopyNotFoundError(Exception):
    """文案不存在或已移入回收站。"""

    def __init__(self, copy_id: int) -> None:
        self.copy_id = copy_id
        super().__init__(f"copy #{copy_id} not found")


class PublishedCopyError(ValueError):
    """已发布文案禁止改写。"""

    def __init__(self, copy_id: int) -> None:
        self.copy_id = copy_id
        super().__init__(f"copy #{copy_id} already published")


class ContentOpsService:
    def __init__(self, settings: Settings, provider=None, platforms=None) -> None:
        self.settings = settings
        self.provider = provider
        self.platforms = platforms if platforms is not None else load_platforms(settings.platforms_file)

    # ---------- 查询辅助 ----------

    @staticmethod
    def _get_active_copy(session: Session, copy_id: int) -> PlatformCopy:
        copy = session.get(PlatformCopy, copy_id)
        if copy is None or copy.deleted_at is not None:
            raise CopyNotFoundError(copy_id)
        return copy

    @staticmethod
    def _is_published(session: Session, copy_id: int) -> bool:
        return session.scalar(select(Publish).where(Publish.copy_id == copy_id)) is not None

    # ---------- 摘要处理 ----------

    @staticmethod
    def _apply_summary(summary: Summary, result: SummarizerResult, article: Article) -> None:
        """将生成结果写入摘要记录并重算质量分（scores 与生成源保持一致）。"""
        summary.summary_text = result.summary_text
        summary.key_points = result.key_points
        summary.short_title = result.short_title
        summary.scores = score_summary(article.text, result.summary_text, result.key_points, result.short_title)
        summary.status = SummaryStatus.SUMMARIZED

    async def _rewrite_all_copies(self, session: Session, summary_id: int) -> None:
        """摘要变更后级联重新扩写全部平台文案；已发布文案保留原样避免覆盖。"""
        copies = session.scalars(
            select(PlatformCopy).where(PlatformCopy.summary_id == summary_id, PlatformCopy.deleted_at.is_(None))
        ).all()
        targets = [copy for copy in copies if not self._is_published(session, copy.id)]
        if not targets:
            return
        await asyncio.gather(*(self._rewrite_copy(session, copy) for copy in targets))

    async def _rewrite_copy(self, session: Session, copy: PlatformCopy) -> None:
        """重写单条文案：LLM 限时生成（超时回退模板），并重置审核为待审。"""
        platform_cfg = self.platforms.get(copy.platform)
        if platform_cfg is None:
            return
        summary = session.get(Summary, copy.summary_id)
        result = await generate_copy_quick(self.provider, summary, platform_cfg, timeout_seconds=AI_TIMEOUT_SECONDS)
        copy.text = result.text
        copy.status = CopyStatus.ADAPTED
        review = session.scalar(select(Review).where(Review.copy_id == copy.id))
        if review is not None:
            review.verdict = Verdict.PENDING
            review.comment = ""

    async def regenerate_summary(self, session: Session, copy_id: int) -> dict:
        """AI 重新生成摘要并级联重写全部平台文案（已发布除外）。"""
        copy = self._get_active_copy(session, copy_id)
        summary = session.get(Summary, copy.summary_id)
        article = session.get(Article, summary.article_id)
        result = await generate_summary_quick(self.provider, article.text, article.title, timeout_seconds=AI_TIMEOUT_SECONDS)
        self._apply_summary(summary, result, article)
        await self._rewrite_all_copies(session, summary.id)
        session.commit()
        return {
            "summary_id": summary.id,
            "summary_text": summary.summary_text,
            "key_points": summary.key_points,
            "short_title": summary.short_title,
            "source": result.source,
        }

    async def update_summary(
        self,
        session: Session,
        copy_id: int,
        *,
        summary_text: str,
        key_points: list[str] | None = None,
        short_title: str = "",
    ) -> dict:
        """手动编辑摘要并级联重写全部平台文案（已发布除外）。"""
        summary_text = (summary_text or "").strip()
        if not summary_text:
            raise ValueError("摘要内容不能为空")
        copy = self._get_active_copy(session, copy_id)
        summary = session.get(Summary, copy.summary_id)
        article = session.get(Article, summary.article_id)
        result = SummarizerResult(
            summary_text=summary_text,
            key_points=[str(k).strip() for k in (key_points or []) if str(k).strip()][:5],
            short_title=(short_title or "").strip()[:200],
            source="manual",
        )
        self._apply_summary(summary, result, article)
        await self._rewrite_all_copies(session, summary.id)
        session.commit()
        return {
            "summary_id": summary.id,
            "summary_text": summary.summary_text,
            "key_points": summary.key_points,
            "short_title": summary.short_title,
            "source": "manual",
        }

    # ---------- 多平台扩写 ----------

    async def regenerate_copy(self, session: Session, copy_id: int) -> dict:
        """重新扩写单条平台文案；已发布文案禁止改写。"""
        copy = self._get_active_copy(session, copy_id)
        if self._is_published(session, copy_id):
            raise PublishedCopyError(copy_id)
        platform_cfg = self.platforms.get(copy.platform)
        if platform_cfg is None:
            raise ValueError(f"不支持的平台：{copy.platform}")
        summary = session.get(Summary, copy.summary_id)
        variant = next_fallback_variant(copy.text)
        result = await generate_copy_quick(
            self.provider,
            summary,
            platform_cfg,
            timeout_seconds=15.0,
            variant=variant,
            current_text=copy.text,
        )
        copy.text = result.text
        copy.status = CopyStatus.ADAPTED
        review = session.scalar(select(Review).where(Review.copy_id == copy.id))
        if review is not None:
            review.verdict = Verdict.PENDING
            review.comment = ""
        session.commit()
        return {"copy_id": copy.id, "platform": copy.platform, "text": copy.text, "source": result.source}

    async def preview_copy(self, session: Session, copy_id: int, platform: str) -> dict:
        """风格预览：按指定平台风格生成文案，不落库。"""
        copy = self._get_active_copy(session, copy_id)
        platform_cfg = self.platforms.get(platform)
        if platform_cfg is None:
            raise ValueError(f"不支持的平台：{platform}")
        summary = session.get(Summary, copy.summary_id)
        result = await generate_copy_quick(self.provider, summary, platform_cfg, timeout_seconds=AI_TIMEOUT_SECONDS)
        return {"platform": platform, "text": result.text, "source": result.source}

    # ---------- 删除与回收站 ----------

    def delete_copy(self, session: Session, copy_id: int) -> None:
        """软删除：移入回收站（保留审核记录，可从回收站恢复）。"""
        copy = self._get_active_copy(session, copy_id)
        copy.deleted_at = datetime.now(UTC)
        session.commit()

    def batch_delete(self, session: Session, copy_ids: list[int]) -> int:
        """批量软删除，返回实际删除条数（跳过不存在/已在回收站的）。"""
        copies = session.scalars(
            select(PlatformCopy).where(PlatformCopy.id.in_(copy_ids), PlatformCopy.deleted_at.is_(None))
        ).all()
        now = datetime.now(UTC)
        for copy in copies:
            copy.deleted_at = now
        session.commit()
        return len(copies)

    def list_recycle(self, session: Session, page: int, page_size: int) -> tuple[list[dict], int]:
        stmt = (
            select(PlatformCopy, Review, Summary, Article)
            .join(Review, Review.copy_id == PlatformCopy.id)
            .join(Summary, Summary.id == PlatformCopy.summary_id)
            .join(Article, Article.id == Summary.article_id)
            .where(PlatformCopy.deleted_at.is_not(None))
        )
        total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = session.execute(stmt.order_by(PlatformCopy.deleted_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
        items = [
            {
                "copy_id": copy.id,
                "platform": copy.platform,
                "article_title": article.title,
                "verdict": review.verdict,
                "text": copy.text,
                "deleted_at": copy.deleted_at.isoformat() if copy.deleted_at else None,
            }
            for copy, review, _summary, article in rows
        ]
        return items, total

    def restore_copy(self, session: Session, copy_id: int) -> None:
        """从回收站恢复。"""
        copy = session.get(PlatformCopy, copy_id)
        if copy is None or copy.deleted_at is None:
            raise CopyNotFoundError(copy_id)
        copy.deleted_at = None
        session.commit()

    def batch_restore(self, session: Session, copy_ids: list[int]) -> int:
        """批量恢复回收站中的文案，返回实际恢复条数（跳过不存在/未删除的）。"""
        copies = session.scalars(
            select(PlatformCopy).where(PlatformCopy.id.in_(copy_ids), PlatformCopy.deleted_at.is_not(None))
        ).all()
        for copy in copies:
            copy.deleted_at = None
        session.commit()
        return len(copies)

    def purge_copy(self, session: Session, copy_id: int) -> None:
        """永久删除：连带清理审核与发布记录，不可恢复。"""
        copy = session.get(PlatformCopy, copy_id)
        if copy is None or copy.deleted_at is None:
            raise CopyNotFoundError(copy_id)
        session.execute(delete(Review).where(Review.copy_id == copy_id))
        session.execute(delete(Publish).where(Publish.copy_id == copy_id))
        session.delete(copy)
        session.commit()

    def batch_purge(self, session: Session, copy_ids: list[int]) -> int:
        """批量永久删除：连带清理审核与发布记录，返回实际删除条数（跳过不存在/未删除的）。"""
        copies = session.scalars(
            select(PlatformCopy).where(PlatformCopy.id.in_(copy_ids), PlatformCopy.deleted_at.is_not(None))
        ).all()
        for copy in copies:
            session.execute(delete(Review).where(Review.copy_id == copy.id))
            session.execute(delete(Publish).where(Publish.copy_id == copy.id))
            session.delete(copy)
        session.commit()
        return len(copies)

    # ---------- 手动内容上传 ----------

    async def create_manual_content(self, session: Session, *, title: str, content: str) -> dict:
        """手动内容入库并同步完成 摘要 → 全平台扩写 → 进入待审，全程 ≤ 3s/AI 步骤。"""
        content = (content or "").strip()
        if not content:
            raise ValueError("内容不能为空")
        if len(content) > 100_000:
            raise ValueError("内容过长（上限 10 万字符）")
        title = (title or extract_title(content)).strip()[:500]
        article = Article(
            source_id=None,
            url=f"manual://{uuid.uuid4().hex}",
            title=title,
            publish_time=None,
            text=content,
            content_hash=hash_content(content),
            simhash_value=to_signed(simhash(content)),
            status=ArticleStatus.CRAWLED,
        )
        session.add(article)
        session.flush()
        result = await generate_summary_quick(self.provider, content, title, timeout_seconds=AI_TIMEOUT_SECONDS)
        summary = Summary(
            article_id=article.id,
            summary_text=result.summary_text,
            key_points=result.key_points,
            short_title=result.short_title,
            scores=score_summary(content, result.summary_text, result.key_points, result.short_title),
            status=SummaryStatus.SUMMARIZED,
        )
        session.add(summary)
        session.flush()
        article.status = ArticleStatus.SUMMARIZED
        copy_ids: list[int] = []
        for platform_id, platform_cfg in self.platforms.items():
            copy_result = await generate_copy_quick(self.provider, summary, platform_cfg, timeout_seconds=AI_TIMEOUT_SECONDS)
            copy = PlatformCopy(
                summary_id=summary.id,
                platform=platform_id,
                text=copy_result.text,
                status=CopyStatus.ADAPTED,
            )
            session.add(copy)
            session.flush()
            # 手动上传不走事件流，需直接创建审核记录进入待审列表
            session.add(Review(copy_id=copy.id, verdict=Verdict.PENDING, scores={}, comment=""))
            copy_ids.append(copy.id)
        session.commit()
        return {"article_id": article.id, "summary_id": summary.id, "copy_ids": copy_ids}


def extract_docx_text(raw: bytes) -> str:
    """标准库解析 .docx（zip + XML），提取段落文本，零第三方依赖。"""
    with ZipFile(io.BytesIO(raw)) as zf:
        document_xml = zf.read("word/document.xml")
    root = ElementTree.fromstring(document_xml)
    paragraphs = []
    for para in root.iter(f"{_W_NS}p"):
        text = "".join(node.text or "" for node in para.iter(f"{_W_NS}t"))
        if text.strip():
            paragraphs.append(text)
    return "\n".join(paragraphs)


def extract_title(content: str) -> str:
    """取内容首个非空行作为默认标题。"""
    for line in content.splitlines():
        if line.strip():
            return line.strip()[:500]
    return "手动上传内容"
