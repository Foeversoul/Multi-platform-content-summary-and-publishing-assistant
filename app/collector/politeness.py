import asyncio
import random
import time
from urllib.parse import urlparse


def domain_of(url: str) -> str:
    return urlparse(url).netloc.lower()


class RateLimiter:
    def __init__(
        self,
        min_interval_seconds: float = 1.0,
        random_min_seconds: float = 3.0,
        random_max_seconds: float = 8.0,
    ) -> None:
        self.min_interval_seconds = min_interval_seconds
        self.random_min_seconds = random_min_seconds
        self.random_max_seconds = random_max_seconds
        self._last: dict[str, float] = {}

    async def wait(self, url: str) -> None:
        domain = domain_of(url)
        now = time.monotonic()
        if domain in self._last:
            wait = self._last[domain] + self.min_interval_seconds - now
            if wait > 0:
                await asyncio.sleep(wait)
        delay = random.uniform(self.random_min_seconds, self.random_max_seconds)
        if delay > 0:
            await asyncio.sleep(delay)
        self._last[domain] = time.monotonic()


class DomainPauseRegistry:
    def __init__(self, pause_minutes: int = 30) -> None:
        self.pause_minutes = pause_minutes
        self._until: dict[str, float] = {}

    def pause(self, domain: str, minutes: int | None = None) -> None:
        self._until[domain] = time.monotonic() + (minutes or self.pause_minutes) * 60

    def is_paused(self, domain: str) -> bool:
        until = self._until.get(domain, 0.0)
        return time.monotonic() < until
