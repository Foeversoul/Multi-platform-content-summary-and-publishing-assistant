from sqlalchemy import select

from app.adapter.copywriter import generate_copy
from app.adapter.platforms import load_platforms
from app.adapter.wordlists import DEFAULT_AD_WORDS, DEFAULT_SENSITIVE_WORDS, load_wordlist
from app.config import Settings
from app.events import EVENT_COPY_ADAPTED, EVENT_SUMMARY_GENERATED
from app.orchestrator.registry import SkillRegistry
from app.orchestrator.state import transition
from app.storage.models import Article, ArticleStatus, CopyStatus, PlatformCopy, Summary
from app.storage.queue import emit_event


class AdapterService:
    def __init__(self, settings: Settings, redis, provider=None, platforms=None) -> None:
        self.settings = settings
        self.redis = redis
        self.provider = provider
        self.platforms = platforms if platforms is not None else load_platforms(settings.platforms_file)
        self.sensitive_words = DEFAULT_SENSITIVE_WORDS + load_wordlist(settings.sensitive_words_file)
        self.ad_words = DEFAULT_AD_WORDS + load_wordlist(settings.ad_words_file)

    async def adapt_summary(self, session, summary_id: int) -> list[int]:
        summary = session.get(Summary, summary_id)
        if summary is None:
            raise ValueError(f"unknown summary_id: {summary_id}")
        article = session.get(Article, summary.article_id)
        copy_ids: list[int] = []
        for platform_id, platform in self.platforms.items():
            exists = session.scalar(
                select(PlatformCopy.id).where(PlatformCopy.summary_id == summary.id, PlatformCopy.platform == platform_id)
            )
            if exists is not None:
                continue
            result = await generate_copy(self.provider, summary, platform)
            copy = PlatformCopy(summary_id=summary.id, platform=platform_id, text=result.text, status=CopyStatus.ADAPTED)
            session.add(copy)
            session.flush()
            await emit_event(self.redis, session, EVENT_COPY_ADAPTED, {"copy_id": copy.id}, self.settings.event_stream)
            copy_ids.append(copy.id)
        if ArticleStatus(article.status) == ArticleStatus.SUMMARIZED:
            transition(ArticleStatus(article.status), ArticleStatus.ADAPTED)
            article.status = ArticleStatus.ADAPTED
        session.commit()
        return copy_ids


def register_adapter_handlers(registry: SkillRegistry, settings: Settings, redis, provider=None) -> None:
    service = AdapterService(settings, redis, provider=provider)

    async def on_summary_generated(payload: dict, session) -> None:
        await service.adapt_summary(session, payload["summary_id"])

    registry.register(EVENT_SUMMARY_GENERATED, on_summary_generated)
