from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


def _enum_column[EnumT: StrEnum](enum_cls: type[EnumT], **kwargs):
    """枚举状态列（Q2/E5）：DB 存储枚举值并附带 CHECK 约束，读取时还原为枚举类型。

    values_callable 保证 DB 中存 value（如 "crawled"）而非 name（"CRAWLED"），
    与既有存量数据兼容，无需数据回填。
    """
    return mapped_column(
        SAEnum(
            enum_cls,
            native_enum=False,
            values_callable=lambda cls: [member.value for member in cls],
            create_constraint=True,
        ),
        **kwargs,
    )


class ArticleStatus(StrEnum):
    PENDING = "pending"
    CRAWLED = "crawled"
    SUMMARIZED = "summarized"
    ADAPTED = "adapted"
    REVIEWED = "reviewed"
    PUBLISHED = "published"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"
    REJECTED = "rejected"


class EventStatus(StrEnum):
    QUEUED = "queued"
    PROCESSED = "processed"
    DEAD = "dead"
    DISCARDED = "discarded"


class SummaryStatus(StrEnum):
    PENDING = "pending"
    SUMMARIZED = "summarized"
    FAILED = "failed"


class CopyStatus(StrEnum):
    PENDING = "pending"
    ADAPTED = "adapted"
    REVIEWED = "reviewed"


class Verdict(StrEnum):
    PENDING = "pending"
    PASS = "pass"
    REJECT = "reject"


class PublishStatus(StrEnum):
    PENDING = "pending"
    PUBLISHED = "published"
    SKIPPED = "skipped"


class ScrapeJobStatus(StrEnum):
    PENDING = "pending"
    VALIDATING = "validating"
    CRAWLING = "crawling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL = "partial"


class ScrapeItemStatus(StrEnum):
    PENDING = "pending"
    VALIDATED = "validated"
    CRAWLING = "crawling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class Source(Base):
    __tablename__ = "source"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    type: Mapped[str] = mapped_column(String(16))
    url: Mapped[str] = mapped_column(String(1024))
    frequency_minutes: Mapped[int] = mapped_column(Integer, default=60)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    site: Mapped[str] = mapped_column(String(64), default="")
    command: Mapped[str] = mapped_column(String(256), default="hot")
    limit: Mapped[int] = mapped_column(Integer, default=0)
    args: Mapped[list] = mapped_column(JSON, default=list)
    profile: Mapped[str] = mapped_column(String(64), default="")
    opencli_bin: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_crawled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Article(Base):
    __tablename__ = "article"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("source.id"), nullable=True)
    url: Mapped[str] = mapped_column(String(2048), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(500))
    publish_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    simhash_value: Mapped[int] = mapped_column(BigInteger, index=True)
    raw_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    status: Mapped[ArticleStatus] = _enum_column(ArticleStatus, default=ArticleStatus.PENDING, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class EventLog(Base):
    __tablename__ = "event_log"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[str] = mapped_column(Text)
    status: Mapped[EventStatus] = _enum_column(EventStatus, default=EventStatus.QUEUED, index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Summary(Base):
    __tablename__ = "summary"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("article.id"), unique=True, index=True)
    summary_text: Mapped[str] = mapped_column(Text)
    key_points: Mapped[list] = mapped_column(JSON)
    short_title: Mapped[str] = mapped_column(String(200))
    scores: Mapped[dict] = mapped_column(JSON)
    status: Mapped[SummaryStatus] = _enum_column(SummaryStatus, default=SummaryStatus.PENDING, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class PlatformCopy(Base):
    __tablename__ = "platform_copy"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    summary_id: Mapped[int] = mapped_column(ForeignKey("summary.id"), index=True)
    platform: Mapped[str] = mapped_column(String(16), index=True)
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[CopyStatus] = _enum_column(CopyStatus, default=CopyStatus.PENDING, index=True)
    # 软删除时间（回收站）：NULL 表示正常，非 NULL 表示已移入回收站
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Review(Base):
    __tablename__ = "review"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    copy_id: Mapped[int] = mapped_column(ForeignKey("platform_copy.id"), unique=True, index=True)
    verdict: Mapped[Verdict] = _enum_column(Verdict, default=Verdict.PENDING)
    scores: Mapped[dict] = mapped_column(JSON)
    comment: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Publish(Base):
    __tablename__ = "publish"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    copy_id: Mapped[int] = mapped_column(ForeignKey("platform_copy.id"), unique=True, index=True)
    status: Mapped[PublishStatus] = _enum_column(PublishStatus, default=PublishStatus.PENDING)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ScrapeJob(Base):
    """URL 上传爬取任务（PRD FR-20~24）。"""

    __tablename__ = "scrape_job"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[ScrapeJobStatus] = _enum_column(ScrapeJobStatus, default=ScrapeJobStatus.PENDING, index=True)
    url_count: Mapped[int] = mapped_column(Integer, default=0)
    succeeded_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ScrapeJobItem(Base):
    """爬取任务中的单个 URL 条目（PRD FR-20~24）。"""

    __tablename__ = "scrape_job_item"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("scrape_job.id"), index=True)
    url: Mapped[str] = mapped_column(String(2048))
    status: Mapped[ScrapeItemStatus] = _enum_column(ScrapeItemStatus, default=ScrapeItemStatus.PENDING, index=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    article_id: Mapped[int | None] = mapped_column(ForeignKey("article.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ChatMessage(Base):
    """AI 对话助手消息（24 小时记忆）：role 取 user / assistant。"""

    __tablename__ = "chat_message"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    role: Mapped[str] = mapped_column(String(16), index=True)
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
