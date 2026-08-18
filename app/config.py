from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite:///./data/dev.db"
    redis_url: str = "redis://localhost:6379/0"
    data_dir: Path = Path("data")
    sources_file: Path = Path("sources.yaml")
    event_stream: str = "assistant:events"
    event_group: str = "workers"
    min_domain_interval_seconds: float = 1.0
    random_delay_min_seconds: float = 3.0
    random_delay_max_seconds: float = 8.0
    request_timeout_seconds: float = 15.0
    crawl_retries: int = 3
    retry_base_seconds: float = 2.0
    domain_pause_minutes: int = 30
    dedup_window_days: int = 30
    simhash_threshold: int = 3
    max_rss_entries: int = 50
    user_agents: list[str] = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    ]
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_model: str = "deepseek-chat"
    llm_timeout_seconds: float = 60.0
    llm_max_tokens: int = 2048

    model_config = {"env_file": ".env", "env_prefix": "ASSISTANT_"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
