"""视频链接增强采集：识别视频页面并提取干净的简介与时间戳大纲。

视频页面正文通常很短（标题 + 简介），直接走通用正文解析往往拿到的是
评论区、推荐位等无关信息。本模块针对 B 站 / YouTube 解析页面里官方的
``meta`` 简介与分段时间轴（章节），并把导航、话题标签、互动数字等噪声
清洗掉，拼成结构化文本供下游 AI 使用。
"""

import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from app.collector.base import Candidate

_BILI_HOSTS = {"bilibili.com", "www.bilibili.com", "m.bilibili.com", "b23.tv"}
_YT_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "youtu.be",
    "music.youtube.com",
}

# 匹配形如 00:00、01:23、1:02:30 的分段时间戳（后面可接章节标题）
_TIMECODE_RE = re.compile(r"\b(?:(\d{1,2}):)?([0-5]?\d):([0-5]\d)\b\s*(.*)", re.DOTALL)

_META_DESCRIPTION_KEYS = (
    "og:description",
    "twitter:description",
    "description",
    "itemprop:description",
)
_META_TITLE_KEYS = ("og:title", "twitter:title")

# 与视频内容无关的噪声行（互动、导航、推荐、话题等）
_NOISE_SUBSTRINGS = (
    "展开",
    "相关推荐",
    "推荐视频",
    "更多内容",
    "关注",
    "点赞",
    "投币",
    "收藏",
    "分享视频",
    "一键三连",
    "弹幕",
    "评论区",
    "http://",
    "https://",
)


def resolve_video_platform(url: str) -> str | None:
    """识别视频链接所属平台，仅支持 B 站与 YouTube，其它返回 None。"""
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if host in _BILI_HOSTS and ("/video/" in parsed.path or host == "b23.tv"):
        return "bilibili"
    if host in _YT_HOSTS and (parsed.path.startswith("/watch") or parsed.path.startswith("/shorts") or host == "youtu.be"):
        return "youtube"
    return None


def _normalize(text: str) -> str:
    return " ".join((text or "").split())


def _meta_content(soup: BeautifulSoup, keys: tuple[str, ...]) -> str:
    for key in keys:
        tag = soup.find("meta", attrs={"property": key}) or soup.find("meta", attrs={"name": key})
        if tag and tag.get("content"):
            return str(tag["content"]).strip()
    return ""


def _extract_chapters(text: str) -> list[str]:
    """从简介/正文行内提取时间戳章节，保留“时间码 + 章节标题”。"""
    chapters: list[str] = []
    for line in (text or "").replace("\r", "\n").splitlines():
        line = _normalize(line)
        if not line:
            continue
        m = _TIMECODE_RE.match(line)
        if not m:
            continue
        hour, minute, second, label = m.groups()
        label = _normalize(label)
        if not label:
            continue
        if hour:
            chapter = f"{hour}:{minute}:{second} {label}"
        else:
            chapter = f"{minute}:{second} {label}"
        if chapter not in chapters:
            chapters.append(chapter)
    return chapters


def clean_video_noise(text: str) -> str:
    """清洗视频相关文本里的无关信息，按行剔除导航/互动/推荐/链接等噪声。"""
    kept: list[str] = []
    for line in (text or "").replace("\r", "\n").splitlines():
        line = _normalize(line)
        if not line:
            continue
        if any(marker in line for marker in _NOISE_SUBSTRINGS):
            continue
        # 去掉纯话题/标签行（形如 #xxx# 或 ##xx##）
        if re.fullmatch(r"(?:#[\w\u4e00-\u9fff]+#?)+", line):
            continue
        kept.append(line)
    return _normalize("\n".join(kept))


def build_video_candidate(html: str, url: str) -> Candidate | None:
    """从视频页面 HTML 构建清洗后的 Candidate；无可用内容时返回 None。"""
    soup = BeautifulSoup(html or "", "html.parser")
    title = _meta_content(soup, _META_TITLE_KEYS) or _normalize(soup.title.get_text() if soup.title else "")
    description = _meta_content(soup, _META_DESCRIPTION_KEYS)

    # 正文文本也给时间戳章节一个机会（保留换行以逐行解析章节）
    body_text = soup.get_text("\n", strip=True)
    description_raw = f"{description}\n{body_text}"
    chapters = _extract_chapters(description_raw)

    description = _normalize(clean_video_noise(description))
    if not description and not chapters:
        return None

    lines = [f"标题：{title}"] if title else []
    if description:
        lines.append(f"官方简介/摘要：{description}")
    if chapters:
        lines.append("时间戳大纲：")
        lines.extend(chapters)
    text = "\n".join(lines).strip()
    return Candidate(url=url, title=(title or url)[:500], text=text)
