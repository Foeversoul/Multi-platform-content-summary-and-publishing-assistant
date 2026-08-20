"""URL 上传爬取：错误码定义与采集异常映射（PRD FR-21 错误分类表）。

错误码与 PRD FR-21 表一一对应；`SSRF_BLOCKED` 为安全扩展（SEC-09）。
"""

import re

from app.collector.web_spider import FetchError

# PRD FR-21 结构化错误码
INVALID_URL_FORMAT = "INVALID_URL_FORMAT"
UNSUPPORTED_PROTOCOL = "UNSUPPORTED_PROTOCOL"
DNS_FAILED = "DNS_FAILED"
CONNECTION_REFUSED = "CONNECTION_REFUSED"
TIMEOUT = "TIMEOUT"
SSL_ERROR = "SSL_ERROR"
HTTP_403 = "HTTP_403"
HTTP_404 = "HTTP_404"
HTTP_429 = "HTTP_429"
HTTP_5XX = "HTTP_5XX"
HTTP_OTHER = "HTTP_OTHER"
ROBOTS_BLOCKED = "ROBOTS_BLOCKED"
EMPTY_CONTENT = "EMPTY_CONTENT"
RENDER_UNSUPPORTED = "RENDER_UNSUPPORTED"
DUPLICATE = "DUPLICATE"
    # 快照兜底失败（Playwright 渲染 + OCR 均无法提取内容）
SNAPSHOT_FAILED = "SNAPSHOT_FAILED"
# SEC-09 安全扩展
SSRF_BLOCKED = "SSRF_BLOCKED"
    # 兜底错误码
INTERNAL_ERROR = "INTERNAL_ERROR"

# 错误码 → 面向用户的中文提示（PRD FR-21）
ERROR_MESSAGES: dict[str, str] = {
    INVALID_URL_FORMAT: "URL 格式无效：必须为 http(s):// 开头的合法地址",
    UNSUPPORTED_PROTOCOL: "不支持的协议：仅支持 http/https",
    DNS_FAILED: "域名解析失败：请检查域名是否正确",
    CONNECTION_REFUSED: "无法连接目标服务器",
    TIMEOUT: "请求超时：目标服务器响应过慢",
    SSL_ERROR: "SSL 证书验证失败",
    HTTP_403: "访问被拒绝（403）：目标站点禁止抓取",
    HTTP_404: "页面不存在（404）",
    HTTP_429: "请求过于频繁（429）：已按限速策略处理",
    HTTP_5XX: "目标服务器错误（5xx）：请稍后重试",
    HTTP_OTHER: "请求被拒绝（4xx）：目标服务器返回未分类的错误状态码",
    ROBOTS_BLOCKED: "目标站点 robots.txt 禁止抓取该路径",
    EMPTY_CONTENT: "无法提取正文：页面可能为空或需登录",
    RENDER_UNSUPPORTED: "该页面为动态渲染，当前版本暂不支持",
    DUPLICATE: "该内容 30 天内已采集过",
SNAPSHOT_FAILED: "快照采集失败：Playwright 渲染与 OCR 均无法提取内容",
    SSRF_BLOCKED: "目标地址为内网/保留地址，已被安全策略拦截",
    INTERNAL_ERROR: "内部处理异常，请稍后重试",
}


class ScrapeError(Exception):
    """带结构化错误码的爬取失败。"""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        self.message = message or ERROR_MESSAGES.get(code, code)
        super().__init__(self.message)


class QuotaExceededError(ScrapeError):
    """SEC-10 配额超限。"""

    def __init__(self) -> None:
        super().__init__(INTERNAL_ERROR, "在途爬取任务数已达上限，请稍后再试")


def _classify_exc_repr(repr_text: str) -> str:
    lowered = repr_text.lower()
    if "timeout" in lowered:
        return TIMEOUT
    if "ssl" in lowered:
        return SSL_ERROR
    if "gaierror" in lowered or "dns" in lowered or "nodename" in lowered:
        return DNS_FAILED
    if "refused" in lowered:
        return CONNECTION_REFUSED
    return CONNECTION_REFUSED


def map_fetch_error(exc: FetchError, url: str) -> tuple[str, str]:
    """将 WebSpider 抛出的 FetchError 映射为 PRD 结构化错误码。"""
    text = str(exc)
    m = re.search(r"blocked by server \((403|429)\)", text)
    if m:
        return (HTTP_403, ERROR_MESSAGES[HTTP_403]) if m.group(1) == "403" else (HTTP_429, ERROR_MESSAGES[HTTP_429])
    if "robots.txt disallows" in text:
        return ROBOTS_BLOCKED, ERROR_MESSAGES[ROBOTS_BLOCKED]
    if "render=true" in text:
        return RENDER_UNSUPPORTED, ERROR_MESSAGES[RENDER_UNSUPPORTED]
    if "domain paused" in text:
        return HTTP_429, ERROR_MESSAGES[HTTP_429]
    m = re.search(r"http (4\d\d|5\d\d):", text)
    if m:
        status = m.group(1)
        if status == "404":
            return HTTP_404, ERROR_MESSAGES[HTTP_404]
        if status == "403":
            return HTTP_403, ERROR_MESSAGES[HTTP_403]
        if status.startswith("4"):
            return HTTP_OTHER, ERROR_MESSAGES[HTTP_OTHER]
        return HTTP_5XX, ERROR_MESSAGES[HTTP_5XX]
    m = re.search(r"fetch failed after \d+ attempts:.*\((.*)\)", text, re.DOTALL)
    if m:
        code = _classify_exc_repr(m.group(1))
        return code, ERROR_MESSAGES[code]
    if "unreachable" in text:
        return CONNECTION_REFUSED, ERROR_MESSAGES[CONNECTION_REFUSED]
    return INTERNAL_ERROR, text[:200]
