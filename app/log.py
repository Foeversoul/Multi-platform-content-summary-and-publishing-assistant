"""日志体系（Q3/O4）：structlog JSON 与标准 logging 统一输出 JSON 行；按天归档。"""

import json
import logging
from datetime import UTC, datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import structlog


class _JsonFormatter(logging.Formatter):
    """标准 logging 的 JSON 行格式化器，与 structlog 输出对齐（含 extra 字段）。"""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": datetime.now(UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        extra = getattr(record, "extra", None)
        if isinstance(extra, dict):
            payload.update(extra)
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(log_dir: Path | None = None, level: int = logging.INFO) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        # O4：按天归档，保留 7 天，避免单文件无限增长
        handlers.append(
            TimedRotatingFileHandler(
                log_dir / "app.log",
                when="midnight",
                backupCount=7,
                encoding="utf-8",
            )
        )
    for handler in handlers:
        handler.setFormatter(_JsonFormatter())
    logging.basicConfig(level=level, handlers=handlers)
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
    )
