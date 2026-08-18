from pathlib import Path

import fakeredis
import fakeredis.aioredis
import pytest

from app.config import Settings
from app.storage.db import build_session_factory


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url="sqlite:///:memory:",
        redis_url="redis://localhost:6379/0",
        data_dir=tmp_path / "data",
        sources_file=tmp_path / "sources.yaml",
        random_delay_min_seconds=0.0,
        random_delay_max_seconds=0.0,
        retry_base_seconds=0.01,
    )


@pytest.fixture
def session_factory():
    return build_session_factory("sqlite:///:memory:")


@pytest.fixture
async def redis():
    server = fakeredis.FakeServer()
    client = fakeredis.aioredis.FakeRedis(server=server)
    yield client
    await client.aclose()
