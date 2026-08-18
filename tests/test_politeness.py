import time

import httpx

from app.collector.politeness import DomainPauseRegistry, RateLimiter
from app.collector.robots import RobotsPolicy, fetch_robots_text, robots_url_for


async def test_rate_limiter_enforces_min_interval():
    limiter = RateLimiter(min_interval_seconds=0.15, random_min_seconds=0.0, random_max_seconds=0.0)
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


def test_robots_url_for():
    assert robots_url_for("https://x.com/a/b") == "https://x.com/robots.txt"


async def test_fetch_robots_text_404_returns_none():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404, request=r))) as client:
        assert await fetch_robots_text(client, "https://x.com/", "bot") is None


async def test_fetch_robots_text_200_returns_text():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text="User-agent: *\nDisallow: /x\n", request=r))
    ) as client:
        text = await fetch_robots_text(client, "https://x.com/", "bot")
        assert text is not None
        assert "Disallow" in text


async def test_fetch_robots_text_error_returns_none():
    def handler(request):
        raise httpx.ConnectError("boom", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await fetch_robots_text(client, "https://x.com/", "bot") is None
