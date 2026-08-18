import time

from app.collector.politeness import DomainPauseRegistry, RateLimiter
from app.collector.robots import RobotsPolicy


async def test_rate_limiter_enforces_min_interval():
    limiter = RateLimiter(min_interval_seconds=0.1, random_min_seconds=0.0, random_max_seconds=0.0)
    start = time.monotonic()
    await limiter.wait("https://a.com/1")
    await limiter.wait("https://a.com/2")
    assert time.monotonic() - start >= 0.1


async def test_domain_pause_registry():
    reg = DomainPauseRegistry(pause_minutes=30)
    reg.pause("a.com")
    assert reg.is_paused("a.com")
    assert not reg.is_paused("b.com")


def test_robots_policy():
    policy = RobotsPolicy.from_text(
        "User-agent: *\nDisallow: /private/\nDisallow: /api\n"
    )
    assert policy.can_fetch("test-bot", "https://x.com/private/1") is False
    assert policy.can_fetch("test-bot", "https://x.com/public") is True


def test_robots_policy_empty_text_allows_all():
    policy = RobotsPolicy.from_text("")
    assert policy.can_fetch("test-bot", "https://x.com/anything") is True
