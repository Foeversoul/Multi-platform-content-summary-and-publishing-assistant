import warnings
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
    # OpenCLI 采集（type=opencli）相关配置
    opencli_bin: str = "opencli"
    opencli_timeout_seconds: float = 90.0
    # OpenCLI 多 Chrome 配置别名，留空则走默认/自动选择
    opencli_profile: str = ""
    # web 源静态抓不到内容时，自动用 OpenCLI 浏览器渲染兜底（支持 JS 页面）
    opencli_render_fallback: bool = True
    opencli_render_timeout_seconds: float = 150.0
    # 快照兜底：常规爬虫 + OpenCLI 渲染均失败时，用 Playwright 截图 + OCR 提取
    snapshot_fallback: bool = True
    snapshot_timeout_seconds: float = 30.0
    # Tesseract 二进制路径，留空则自动检测；RapidOCR 优先无需此项
    tesseract_bin: str = ""
    user_agents: list[str] = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    ]
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_model: str = "deepseek-chat"
    llm_timeout_seconds: float = 60.0
    llm_max_tokens: int = 2048
    # LLM 请求重试（T6）：429/5xx 指数退避，0 表示不重试
    llm_retries: int = 3
    llm_retry_base_seconds: float = 0.5
    platforms_file: Path = Path("platforms.yaml")
    sensitive_words_file: Path | None = None
    ad_words_file: Path | None = None
    # URL 上传爬取（PRD FR-20~24 / SEC-10）
    scrape_max_jobs_inflight: int = 3
    scrape_max_batch: int = 1000
    scrape_concurrency: int = 5
    scrape_probe_timeout_seconds: float = 10.0
    # REST API 鉴权（SEC-01 基线）：配置后所有 /api 请求须携带 X-API-Token；留空表示本地开发模式不校验
    api_token: str = ""
    # API 速率限制（S5）：窗口内单 IP 最大请求数，0 表示关闭
    rate_limit_per_minute: int = 60

    model_config = {"env_file": ".env", "env_prefix": "ASSISTANT_"}


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    # O3：生产部署应使用 PostgreSQL，SQLite 仅限本地开发
    if settings.api_token and settings.database_url.startswith("sqlite"):
        warnings.warn(
            "检测到 ASSISTANT_API_TOKEN 已配置但数据库仍为 SQLite。"
            "生产环境请使用 PostgreSQL（docker compose 已就绪），SQLite 仅限本地开发。",
            stacklevel=2,
        )
    return settings
