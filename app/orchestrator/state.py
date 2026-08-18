from app.storage.models import ArticleStatus

VALID_TRANSITIONS: dict[ArticleStatus, set[ArticleStatus]] = {
    ArticleStatus.PENDING: {ArticleStatus.CRAWLED, ArticleStatus.FAILED, ArticleStatus.DEAD_LETTER},
    ArticleStatus.CRAWLED: {ArticleStatus.SUMMARIZED, ArticleStatus.FAILED, ArticleStatus.DEAD_LETTER},
    ArticleStatus.SUMMARIZED: {ArticleStatus.ADAPTED, ArticleStatus.FAILED, ArticleStatus.DEAD_LETTER},
    ArticleStatus.ADAPTED: {ArticleStatus.REVIEWED, ArticleStatus.FAILED, ArticleStatus.DEAD_LETTER},
    ArticleStatus.REVIEWED: {ArticleStatus.PUBLISHED, ArticleStatus.REJECTED},
    ArticleStatus.FAILED: {ArticleStatus.PENDING, ArticleStatus.DEAD_LETTER},
    ArticleStatus.DEAD_LETTER: set(),
    ArticleStatus.REJECTED: set(),
}


class InvalidTransitionError(ValueError):
    pass


def transition(current: ArticleStatus, target: ArticleStatus) -> None:
    if target not in VALID_TRANSITIONS.get(current, set()):
        raise InvalidTransitionError(f"invalid transition: {current.value} -> {target.value}")
