from sqlalchemy import select

from app.adapter.compliance import check_compliance
from app.adapter.platforms import load_platforms
from app.adapter.wordlists import DEFAULT_AD_WORDS, DEFAULT_SENSITIVE_WORDS, load_wordlist
from app.config import Settings
from app.orchestrator.registry import SkillRegistry
from app.orchestrator.state import transition
from app.reviewer.quality import score_copy
from app.storage.models import Article, ArticleStatus, CopyStatus, PlatformCopy, Review, Summary, Verdict
from app.storage.queue import emit_event


class ReviewerService:
    def __init__(self, settings: Settings, redis, platforms=None) -> None:
        self.settings = settings
        self.redis = redis
        self.platforms = platforms if platforms is not None else load_platforms(settings.platforms_file)
        self.sensitive_words = DEFAULT_SENSITIVE_WORDS + load_wordlist(settings.sensitive_words_file)
        self.ad_words = DEFAULT_AD_WORDS + load_wordlist(settings.ad_words_file)

    async def review_copy(self, session, copy_id: int) -> Review:
        copy = session.get(PlatformCopy, copy_id)
        if copy is None:
            raise ValueError(f"unknown copy_id: {copy_id}")
        platform = self.platforms.get(copy.platform)
        if platform is None:
            raise ValueError(f"unknown platform: {copy.platform}")
        compliance = check_compliance(copy.text, self.sensitive_words, self.ad_words)
        scores = score_copy(platform, copy.text, compliance)
        review = Review(copy_id=copy.id, verdict=Verdict.PENDING, scores=scores)
        session.add(review)
        session.flush()
        copy.status = CopyStatus.REVIEWED
        summary = session.get(Summary, copy.summary_id)
        article = session.get(Article, summary.article_id)
        copies = session.scalars(select(PlatformCopy).where(PlatformCopy.summary_id == summary.id)).all()
        if all(c.status == CopyStatus.REVIEWED for c in copies) and ArticleStatus(article.status) in (
            ArticleStatus.ADAPTED,
            ArticleStatus.SUMMARIZED,
        ):
            transition(ArticleStatus(article.status), ArticleStatus.REVIEWED)
            article.status = ArticleStatus.REVIEWED
        await emit_event(
            self.redis,
            session,
            "review.passed",
            {"review_id": review.id, "copy_id": copy.id},
            self.settings.event_stream,
        )
        session.commit()
        return review


def register_reviewer_handlers(registry: SkillRegistry, settings: Settings, redis) -> None:
    service = ReviewerService(settings, redis)

    async def on_copy_adapted(payload: dict, session) -> None:
        await service.review_copy(session, payload["copy_id"])

    registry.register("copy.adapted", on_copy_adapted)
