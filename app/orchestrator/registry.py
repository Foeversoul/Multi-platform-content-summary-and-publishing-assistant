import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Literal

from sqlalchemy.orm import Session

from app.orchestrator.state import transition
from app.storage.models import Article, ArticleStatus

logger = logging.getLogger(__name__)

Handler = Callable[[dict, Session], Awaitable[None]]
Outcome = Literal["ok", "dead", "noop"]


class SkillRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, Handler] = {}

    def register(self, event_type: str, handler: Handler) -> None:
        self._handlers[event_type] = handler

    def has(self, event_type: str) -> bool:
        return event_type in self._handlers

    async def dispatch(
        self,
        event_type: str,
        payload: dict,
        session: Session,
        retries: int = 2,
        base_seconds: float = 1.0,
    ) -> Outcome:
        handler = self._handlers.get(event_type)
        if handler is None:
            logger.warning("no handler registered", extra={"event_type": event_type})
            return "noop"
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                await handler(payload, session)
                return "ok"
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                session.rollback()
                if attempt < retries:
                    await asyncio.sleep(base_seconds * (2**attempt))
        article_id = payload.get("article_id")
        if article_id:
            article = session.get(Article, article_id)
            if article is not None:
                transition(ArticleStatus(article.status), ArticleStatus.DEAD_LETTER)
                article.status = ArticleStatus.DEAD_LETTER
                session.commit()
        logger.error(
            "handler failed permanently",
            extra={"event_type": event_type, "error": repr(last_error)},
        )
        return "dead"
