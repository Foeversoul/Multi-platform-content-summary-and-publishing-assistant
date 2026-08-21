"""OpenCLI 采集适配器：通过 ``opencli`` 命令驱动内置平台适配器。

OpenCLI 通过用户已登录的浏览器（Browser Bridge 扩展 + 本地 daemon）抓取
需要登录态或 JS 渲染的平台（B站、知乎、小红书等），弥补静态 RSS/Web 爬虫
无法覆盖的能力空白。本模块把 ``opencli <site> <command> -f json`` 的输出
转成流水线通用的 :class:`~app.collector.base.Candidate`。
"""

import asyncio
import json
import os
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Protocol

from app.collector.base import Candidate
from app.collector.errors import FetchError
from app.collector.sources import SourceConfig
from app.config import Settings

TITLE_KEYS = {
    "title",
    "name",
    "headline",
    "note_title",
    "article_title",
    "subject",
    "display_name",
}
URL_KEYS = {
    "url",
    "link",
    "href",
    "permalink",
    "note_url",
    "page_url",
    "source_url",
    "article_url",
    "thread_url",
}
TEXT_KEYS = {
    "text",
    "content",
    "body",
    "summary",
    "description",
    "transcript",
    "note",
    "abstract",
    "answer",
    "detail",
    "full_text",
    "raw",
    "excerpt",
    "digest",
}
TIME_KEYS = {
    "published_at",
    "publish_time",
    "created_at",
    "created",
    "date",
    "time",
    "timestamp",
    "pub_date",
    "updated_at",
    "datetime",
}
ID_KEYS = {
    "id",
    "nid",
    "bid",
    "bvid",
    "aid",
    "note_id",
    "thread_id",
    "comment_id",
    "post_id",
    "item_id",
    "sid",
}
_WRAPPER_KEYS = ("data", "items", "rows", "results", "list", "records", "entries")


class OpenCliError(RuntimeError):
    """opencli 进程级错误（启动失败、超时等），与平台返回码区分。"""

    def __init__(self, message: str, exit_code: int | None = None, stderr: str = "") -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.stderr = stderr


@dataclass
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


class CommandRunner(Protocol):
    async def run(self, argv: list[str], timeout: float) -> CommandResult: ...


def _resolve_exec_args(argv: list[str]) -> list[str]:
    """解析命令路径。Windows 上 npm 命令是 .cmd/.bat 脚本，需经 cmd.exe 启动。"""
    program = argv[0]
    if os.path.dirname(program):
        return argv
    resolved = shutil.which(program)
    if resolved is None:
        return argv
    if os.name == "nt" and resolved.lower().endswith((".cmd", ".bat")):
        return ["cmd.exe", "/c", *argv]
    return [resolved, *argv[1:]]


class SubprocessRunner:
    """默认实现：以子进程方式执行 opencli，超时则终止。"""

    async def run(self, argv: list[str], timeout: float) -> CommandResult:
        proc = await asyncio.create_subprocess_exec(
            *_resolve_exec_args(argv),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise OpenCliError(f"opencli 执行超时（>{timeout:.0f}s）", exit_code=-1) from None
        return CommandResult(
            returncode=proc.returncode or 0,
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
        )


class OpenCliSpider:
    source_type = "opencli"

    def __init__(self, settings: Settings, runner: CommandRunner | None = None) -> None:
        self.settings = settings
        self.runner = runner or SubprocessRunner()

    async def fetch_video(self, url: str, source: SourceConfig | None = None) -> list[Candidate]:
        """按官方命令抓取单个 B 站视频：`bilibili video` + `bilibili summary`。"""
        opencli_bin = (source.opencli_bin if source and source.opencli_bin else "") or self.settings.opencli_bin
        profile = (source.profile if source and source.profile else "") or self.settings.opencli_profile

        meta = await self._run_simple(["bilibili", "video", url], opencli_bin, profile)
        title = ""
        description = ""
        publish_time: datetime | None = None
        for row in _extract_rows(meta):
            field = _normalize_key(str(row.get("field") or ""))
            value = row.get("value")
            if value in (None, ""):
                continue
            text_value = str(value).strip()
            if field == "title":
                title = text_value
            elif field == "description":
                description = text_value
            elif field in ("publish_time", "published_at", "pub_date", "date"):
                publish_time = _parse_time(text_value)

        outline: list[str] = []
        try:
            summary = await self._run_simple(["bilibili", "summary", url], opencli_bin, profile)
            for row in _extract_rows(summary):
                content = row.get("content")
                if content in (None, ""):
                    continue
                stamp = row.get("time")
                prefix = f"{stamp} " if stamp not in (None, "") else ""
                line = f"{prefix}{str(content).strip()}".strip()
                if line:
                    outline.append(line)
        except FetchError:
            # 摘要接口可能未生成或需登录，失败不阻断元数据采集
            outline = []

        # 正文仅保留 AI 总结纲要，视频元数据（bvid/aid/author/view 等）不进入后续步骤；
        # AI 总结缺失时用 description 兜底（跳过无意义占位符如 "-"）
        body = ""
        if outline:
            body = "官方AI总结：\n" + "\n".join(outline)
        elif description and description not in ("-", "——", "无"):
            body = description
        if not body and not title:
            return []
        return [Candidate(url=url, title=(title or url)[:500], text=body, publish_time=publish_time)]

    async def _run_simple(self, positional: list[str], opencli_bin: str, profile: str) -> Any:
        """执行带 position 参数的 opencli 命令并以 JSON 解析输出。"""
        argv = [opencli_bin]
        if profile:
            argv += ["--profile", profile]
        argv += [*positional, "-f", "json"]
        try:
            result = await self.runner.run(argv, self.settings.opencli_timeout_seconds)
        except OpenCliError as exc:
            raise FetchError(str(exc)) from exc
        except FileNotFoundError as exc:
            raise FetchError(f"未找到 opencli 命令：{opencli_bin}，请先安装并运行 opencli doctor") from exc
        if result.returncode == 66:
            return []
        if result.returncode != 0:
            raise FetchError(self._describe_failure(result))
        return _parse_payload(result.stdout)

    async def fetch(self, source: SourceConfig) -> list[Candidate]:
        argv = self._build_argv(source)
        try:
            result = await self.runner.run(argv, self.settings.opencli_timeout_seconds)
        except OpenCliError as exc:
            raise FetchError(str(exc)) from exc
        except FileNotFoundError as exc:
            raise FetchError(f"未找到 opencli 命令：{argv[0]}，请先安装并运行 opencli doctor") from exc
        if result.returncode == 66:
            # 66 = 空结果（如榜单暂无内容），视为无新数据
            return []
        if result.returncode != 0:
            raise FetchError(self._describe_failure(result))
        payload = _parse_payload(result.stdout)
        candidates: list[Candidate] = []
        for index, row in enumerate(_extract_rows(payload)):
            candidate = _row_to_candidate(row, source, index)
            if candidate is not None and candidate.text:
                candidates.append(candidate)
        return candidates

    def _build_argv(self, source: SourceConfig) -> list[str]:
        argv = [source.opencli_bin or self.settings.opencli_bin]
        profile = source.profile or self.settings.opencli_profile
        if profile:
            argv += ["--profile", profile]
        if source.site:
            argv.append(source.site)
        if source.command:
            argv.append(source.command)
        argv += list(source.args or [])
        if source.limit and source.limit > 0 and "--limit" not in argv:
            argv += ["--limit", str(source.limit)]
        argv += ["-f", "json"]
        return argv

    def _describe_failure(self, result: CommandResult) -> str:
        detail = (result.stderr or result.stdout or "").strip()
        suffix = f": {detail[:300]}" if detail else ""
        label = {
            69: "Browser Bridge 未连接（daemon/扩展未启动）",
            75: "opencli 命令超时",
            77: "需要登录态（对应平台的 Chrome 登录可能已过期）",
            78: "opencli 配置错误",
            130: "opencli 被中断",
        }.get(result.returncode, f"opencli 执行失败（exit {result.returncode}）")
        return label + suffix


def _normalize_key(key: str) -> str:
    return key.lower().replace("-", "_").strip()


def _first_value(row: dict, keys: set[str]) -> Any | None:
    for key, value in row.items():
        if _normalize_key(key) in keys and value not in (None, "", [], {}):
            return value
    return None


def _parse_payload(stdout: str) -> Any:
    text = stdout.strip()
    if not text:
        return []
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return []


def _extract_rows(payload: Any) -> list[dict]:
    """把 opencli 的 JSON 输出规整成行列表，兼容数组 / 包装对象 / 单行对象。"""
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in _WRAPPER_KEYS:
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
        if any(isinstance(value, (str, int, float)) for value in payload.values()):
            return [payload]
    return []


def _row_to_candidate(row: dict, source: SourceConfig, index: int) -> Candidate | None:
    url = _first_value(row, URL_KEYS)
    identifier = _first_value(row, ID_KEYS)
    if not url:
        if identifier is None:
            return None
        # 无直接 URL 时用站点/命令/ID 构造稳定地址，供去重使用
        site = source.site or "site"
        command = source.command or "list"
        url = f"opencli://{site}/{command}/{identifier}"
    title = _first_value(row, TITLE_KEYS)
    text = _extract_text(row)
    if not text:
        return None
    publish_time = _parse_time(_first_value(row, TIME_KEYS))
    return Candidate(
        url=str(url),
        title=str(title or url)[:500],
        text=text,
        publish_time=publish_time,
    )


def _extract_text(row: dict) -> str:
    for key, value in row.items():
        if _normalize_key(key) in TEXT_KEYS and isinstance(value, str) and value.strip():
            return value.strip()
    # 没有正文类字段时，规整其余字段成 "key: value" 文本，确保内容不丢失
    excluded = TITLE_KEYS | URL_KEYS | TEXT_KEYS | TIME_KEYS | ID_KEYS
    parts: list[str] = []
    for key, value in row.items():
        normalized = _normalize_key(key)
        if normalized in excluded:
            continue
        if isinstance(value, str) and value.strip():
            parts.append(f"{key}: {value.strip()}")
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            parts.append(f"{key}: {value}")
        elif isinstance(value, (dict, list)):
            parts.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
    return "\n".join(parts).strip()


def _parse_time(value: Any) -> datetime | None:
    if value in (None, "", 0):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(value, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    raw = str(value)
    normalized = raw.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(raw)
        if parsed is None:
            return None
        return parsed.astimezone(UTC)
    except (TypeError, ValueError, OverflowError):
        return None
    return None
