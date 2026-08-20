"""Web 审核台启动入口：`python -m app.web` 或 `uvicorn app.web.__main__:app`。"""

from redis.asyncio import Redis

from app.config import get_settings
from app.storage.db import build_session_factory
from app.web.main import create_app

settings = get_settings()
app = create_app(
    build_session_factory(settings.database_url),
    Redis.from_url(settings.redis_url),
)
