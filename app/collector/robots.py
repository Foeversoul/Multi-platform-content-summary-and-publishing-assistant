from urllib.parse import urlparse, urlunparse
from urllib.robotparser import RobotFileParser


class RobotsPolicy:
    def __init__(self, parser: RobotFileParser) -> None:
        self._parser = parser

    @classmethod
    def from_text(cls, content: str) -> "RobotsPolicy":
        parser = RobotFileParser()
        parser.parse(content.splitlines())
        return cls(parser)

    def can_fetch(self, user_agent: str, url: str) -> bool:
        return self._parser.can_fetch(user_agent, url)


def robots_url_for(base_url: str) -> str:
    parsed = urlparse(base_url)
    return urlunparse((parsed.scheme, parsed.netloc, "/robots.txt", "", "", ""))


async def fetch_robots_text(client, base_url: str, user_agent: str) -> str | None:
    try:
        resp = await client.get(robots_url_for(base_url), headers={"User-Agent": user_agent})
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.text
    except Exception:
        return None
