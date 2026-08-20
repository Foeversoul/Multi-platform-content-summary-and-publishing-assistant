from app.config import Settings
from app.events import EVENT_ARTICLE_CRAWLED, EVENT_SUMMARY_GENERATED
from app.orchestrator.registry import SkillRegistry
from app.orchestrator.state import transition
from app.processor.clean import clean_text, remove_noise_sentences, split_sentences
from app.processor.entities import extract_entities
from app.processor.extractive import score_sentences
from app.processor.keywords import extract_keywords
from app.processor.quality import score_summary
from app.processor.summarizer import generate_summary
from app.storage.models import Article, ArticleStatus, Summary, SummaryStatus
from app.storage.queue import emit_event


class ProcessorService:
    def __init__(self, settings: Settings, redis, provider=None) -> None:
        self.settings = settings
        self.redis = redis
        self.provider = provider

    async def process_article(self, session, article_id: int) -> Summary:
        article = session.get(Article, article_id)
        if article is None:
            raise ValueError(f"unknown article_id: {article_id}")
        transition(ArticleStatus(article.status), ArticleStatus.SUMMARIZED)
        text = clean_text(article.text)
        sentences = remove_noise_sentences(split_sentences(text))
        entities = extract_entities(text)
        result = await generate_summary(self.provider, text, article.title or "")
        scores = score_summary(text, result.summary_text, result.key_points, result.short_title)
        scores["keywords"] = extract_keywords(text)
        scores["sentence_count"] = len(sentences)
        scores["top_sentence_scores"] = [round(x, 4) for x in score_sentences(sentences, article.title or "", entities)]
        summary = Summary(
            article_id=article.id,
            summary_text=result.summary_text,
            key_points=result.key_points,
            short_title=result.short_title,
            scores=scores,
            status=SummaryStatus.SUMMARIZED,
        )
        session.add(summary)
        session.flush()
        article.status = ArticleStatus.SUMMARIZED
        await emit_event(self.redis, session, EVENT_SUMMARY_GENERATED, {"summary_id": summary.id}, self.settings.event_stream)
        session.commit()
        return summary


def register_processor_handlers(registry: SkillRegistry, settings: Settings, redis, provider=None) -> None:
    service = ProcessorService(settings, redis, provider=provider)

    async def on_article_crawled(payload: dict, session) -> None:
        await service.process_article(session, payload["article_id"])

    registry.register(EVENT_ARTICLE_CRAWLED, on_article_crawled)
