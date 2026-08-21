"""AI 对话助手：基于项目知识的问答 + 按需求执行模块功能 + 24 小时对话记忆。

LLM 可用时走对话生成；不可用时按关键词匹配回退到内置知识库。
对话会保存最近 24 小时的历史（user / assistant 消息），回答时作为上下文参考；
支持“查历史记录”查询近期对话、“清空历史”清除记忆。
当用户表达明确的执行意图（导入内容 / 发布 / 重新生成 / 状态查询 / 爬取 URL）时，
直接调用对应模块动作并把执行结果回写到对话中。
"""

import asyncio
import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select

from app.llm.provider import ChatMessage, LLMError
from app.scrape.errors import QuotaExceededError, ScrapeError
from app.storage.models import Article, PlatformCopy, Review, Summary, Verdict
from app.storage.models import ChatMessage as ChatMessageRecord
from app.web.actions import ReviewNotFoundError, publish_copy, publish_pending_all
from app.web.content_ops import CopyNotFoundError, PublishedCopyError, extract_title

_KB: list[tuple[list[str], str]] = [
    (
        ["能做什么", "功能", "介绍", "是什么", "用途"],
        "本项目是一个多平台内容总结与发布助手，核心流程为：采集 → AI 摘要 → 多平台文案扩写 → 人工审核 → 发布。"
        + "支持 URL 爬取、RSS 订阅、OpenCLI 浏览器渲染采集、手动内容上传等多种内容导入方式，"
        + "自动生成微博/朋友圈/小红书三个平台的风格文案，并在审核台进行一键通过、驳回、重新扩写等操作。",
    ),
    (
        ["如何导入", "怎么导入", "上传", "爬取", "采集", "内容导入"],
        "内容导入有两种方式：\n"
        + "1. URL 上传爬取：在内容导入页粘贴 URL，系统自动爬取正文并进入摘要→扩写→待审流程；"
        + "遇到 JS 渲染页面会自动用 OpenCLI 浏览器渲染兜底。\n"
        + "2. 手动内容上传：切换到上传文件标签，粘贴文本或上传 .txt/.md/.docx 文件，系统同样自动完成 AI 处理。",
    ),
    (
        ["平台", "哪些平台", "微博", "朋友圈", "小红书", "支持平台"],
        "当前支持三个平台的文案扩写：\n"
        + "• 微博（50-500字，1-3 个话题标签，倒金字塔口语化风格）\n"
        + "• 朋友圈（60-200字，第一人称生活化分享，0-3 个 emoji，不使用话题标签）\n"
        + "• 小红书（100-500字，种草分享风格，2-5 个话题标签 + 1-3 个 emoji）\n"
        + "新增平台只需在 platforms.yaml 中添加配置，适配器会自动按配置生成文案。",
    ),
    (
        ["爬取不到", "爬不到", "抓不到", "抓取失败", "内容为空", "渲染"],
        "爬取不到内容常见原因和解决方案：\n"
        + "1. 目标页面是 JS 渲染的 SPA（如 B站、知乎），静态爬虫抓不到正文——系统会自动用 OpenCLI 浏览器渲染兜底，"
        + "需要先安装 opencli 和 Browser Bridge 扩展并运行 opencli doctor 通过。\n"
        + "2. 需要登录态的页面——配置 ASSISTANT_OPENCLI_PROFILE 指定已登录的 Chrome 配置文件别名。\n"
        + "3. 网站反爬（403/429）——检查 robots.txt 是否允许抓取，或降低采集频率。\n"
        + "4. URL 格式错误或 DNS 解析失败——检查 URL 是否正确、网络是否畅通。",
    ),
    (
        ["一键审核", "批量", "发布", "通过", "审核流程"],
        "审核流程：内容导入后自动生成摘要和多平台文案，进入待审列表。\n"
        + "在待审列表可以：\n"
        + "• 点击文章标题进入详情，查看原文/摘要/平台文案/评分\n"
        + "• 切换平台标签查看不同平台的文案\n"
        + "• 点击重新扩写让 AI 重新生成文案\n"
        + "• 确认发布或驳回（驳回需填写理由）\n"
        + "• 一键通过全部按钮可批量发布所有待审文案\n"
        + "• 勾选多行可批量发布/驳回/删除",
    ),
    (
        ["事件总线", "架构", "智能体", "流水线", "worker"],
        "项目基于事件总线（Redis Streams）的多智能体架构：\n"
        + "调度协调 → 信息采集 → 内容处理（AI 摘要）→ 内容适配（多平台文案）→ 质量审核\n"
        + "各智能体通过事件解耦，worker 进程消费事件流自动执行流水线。"
        + "事件日志落库支持死信重跑/放弃，保证消息可靠。",
    ),
    (
        ["重新扩写", "重新生成", "改写", "变体"],
        "在详情页点击重新扩写可让 AI 重新生成当前平台文案。系统会采用不同的表达方式和结构来改写，"
        + "确保每次重新扩写的结果都不同。如果 LLM 不可用，会走 4 种模板变体循环，"
        + "保证每次至少换一种模板。审核状态会重置为待审。",
    ),
    (
        ["摘要", "总结", "要点"],
        "系统会自动从文章正文提取 AI 摘要（200-400字）、关键要点（最多5条）和短标题。"
        + "在详情页可以点击重新生成让 AI 重新提取摘要，或点击编辑手动修改。"
        + "修改摘要后会级联重新扩写全部平台的文案。",
    ),
    (
        ["回收站", "删除", "恢复"],
        "删除文案是软删除，会移入回收站。在回收站页面可以查看已删除的文案，支持恢复或永久删除。"
        + "批量删除同样移入回收站，可随时恢复。",
    ),
    (
        ["死信", "失败", "重试", "DLQ"],
        "事件处理失败后会进入死信队列。在死信管理页面可以查看失败事件，"
        + "支持重跑（重新入队）或放弃（标记为已处理）。爬取任务中失败的 URL 条目也支持单独重新提交。",
    ),
    (
        ["启动", "部署", "docker", "环境", "配置"],
        "本地开发：\n"
        + "1. pip install -e .[dev] 安装后端依赖\n"
        + "2. 配置 .env（ASSISTANT_LLM_API_KEY 等）和 sources.yaml / platforms.yaml\n"
        + "3. python -m uvicorn app.web.__main__:app --port 8000 启动后端\n"
        + "4. python -m app.worker 启动 worker\n"
        + "5. cd web-ui && npm install && npm run dev 启动前端\n"
        + "Docker 部署：docker compose up -d postgres redis worker app frontend",
    ),
    (
        ["OpenCLI", "浏览器", "渲染", "Chrome"],
        "OpenCLI 是增强版爬虫，通过驱动已登录的 Chrome 抓取需要 JS 渲染或登录态的平台（B站、知乎、小红书等）。"
        + "配置 type: opencli 的数据源，指定 site/command（如 bilibili hot）即可。"
        + "URL 上传也支持自动渲染兜底：静态抓不到内容时自动改用 opencli web read 渲染页面。"
        + "需要先 npm install -g @jackwener/opencli 并安装 Browser Bridge 扩展。",
    ),
    (
        ["快照", "截图", "OCR", "文字识别", "snapshot", "截取"],
        "快照功能是爬虫的最终兜底方案：\n"
        + "1. 当常规爬虫和 OpenCLI 渲染都无法提取页面内容时，系统自动启动 Playwright 无头浏览器加载页面\n"
        + "2. 优先从渲染后的 DOM 提取可见文本\n"
        + "3. 若 DOM 文本为空或过短（如内容以图片/canvas 形式呈现），则全页截图并用 OCR 识别文字\n"
        + "4. OCR 引擎优先使用 RapidOCR（纯 Python，无需外部二进制），备选 Tesseract\n"
        + "5. 识别出的文字经 AI 清洗噪声后返回，进入后续摘要-扩写-审核流程\n"
        + "配置项：snapshot_fallback=True 开启，snapshot_timeout_seconds 控制页面加载超时。",
    ),
    (
        ["评分", "质量", "分数"],
        "系统会对生成的摘要和文案进行质量评分，包括内容覆盖度、风格匹配度、标签使用等维度。"
        + "评分显示在待审列表和详情页中，帮助审核人员快速判断文案质量。",
    ),
]

_SYSTEM_PROMPT = (
    "你是多平台内容总结与发布助手项目的 AI 助手。你的职责是帮助用户了解、调试和使用这个项目，并直接帮用户执行操作。\n"
    + "项目核心流程：采集 → AI 摘要 → 多平台文案扩写 → 人工审核 → 发布。\n"
    + "支持的内容导入方式：URL 上传爬取（含 OpenCLI 浏览器渲染兜底）、RSS 订阅、手动内容上传（文本/文件）。\n"
    + "支持的平台：微博（50-500字，1-3个#话题#）、朋友圈（60-200字，0-3个emoji，不用话题标签）、"
    + "小红书（100-500字，2-5个#话题#和1-3个emoji，可在 platforms.yaml 扩展）。\n"
    + "主要功能页面：待审列表、内容导入、运行总览、回收站、死信管理、AI 助手。\n"
    + "快照兜底：常规爬虫和 OpenCLI 渲染均失败时，自动用 Playwright 加载页面 + OCR 识别文字提取正文。\n"
    + "架构：基于 Redis Streams 事件总线的多智能体流水线，worker 进程消费事件自动执行。\n"
    + "你还可以直接执行模块动作：爬取指定链接、导入内容、发布所有待审、发布单条文案（指定编号）、"
    + "重新生成摘要或扩写、查询待审列表与待审数量。\n"
    + "对话记忆：系统保留最近 24 小时的对话记录，回答时会参考之前的上下文；"
    + "用户说“查历史记录/查看历史”可以查看近期对话，说“清空历史/清除记忆”可以删除记忆。\n"
    + "请用简洁清晰的中文回答用户问题，适当使用列表和换行提升可读性。不要编造不存在的功能。"
)

_DEFAULT_REPLY = (
    "抱歉，我没有找到与您问题直接相关的信息。您可以尝试问我：\n"
    + "• 这个项目能做什么？\n• 如何导入内容？\n• 支持哪些平台？\n"
    + "• 爬取不到内容怎么办？\n• 如何一键审核？\n• 如何部署项目？"
)

 
_PROJECT_OVERVIEW = (
    "这是一个多平台内容总结与发布助手，核心流程为：采集 → AI 摘要 → 多平台文案扩写 → 人工审核 → 发布。\n\n"
    + "主要功能：\n"
    + "• 内容导入：粘贴 URL 自动爬取，或手动上传文本/文件\n"
    + "• 自动生成微博/朋友圈/小红书三个平台的风格文案\n"
    + "• 审核台支持一键通过、驳回、重新扩写\n"
    + "• 回收站、死信管理、运行状态监控\n\n"
    + "您可以问我更具体的问题，例如：\n"
    + "• 这个项目能做什么？\n• 如何导入内容？\n• 支持哪些平台？\n"
    + "• 爬取不到内容怎么办？\n• 如何一键审核？\n• 如何部署项目？"
)

# ------------- 动作意图关键词（严格匹配，避免误触发问答） -------------

_IMPORT_VERBS = ("导入", "收录", "添加内容", "上传内容")
_QUESTION_WORDS = ("如何", "怎么", "怎样", "能否", "可以吗", "可不可以用", "吗？", "？", "?", "介绍")
_PUBLISH_ALL_WORDS = ("发布所有", "发布全部", "全部发布", "一键通过", "全部通过", "批量通过", "全选发布")
_SUMMARY_REGENERATE_WORDS = ("重新生成摘要", "重新总结", "重新摘要")
_COPY_REGENERATE_WORDS = ("重新扩写", "重新生成文案", "改写文案", "重新生成该文案")
_LIST_WORDS = ("待审列表", "列出待审", "看看待审", "有哪些待审", "待审都有", "看下待审", "待审的都有")
_STATUS_WORDS = ("运行状态", "状态如何", "待审数量", "有多少待审", "待审的有", "待审核数量", "当前状态")
_CRAWL_VERBS = ("爬取", "抓取", "爬一下", "爬一爬", "采集这个")
_URL_RE = re.compile(r"https?://[^\s，。、；：\"'<>]+", re.IGNORECASE)
_MIN_TEXT_LEN = 20
_MEMORY_WINDOW_HOURS = 24
_MEMORY_LIMIT = 40
_HISTORY_WORDS = ("历史记录", "查历史", "查看历史", "看看历史", "历史消息", "聊天记录", "之前的对话")
_CLEAR_HISTORY_WORDS = ("清空历史", "清除历史", "清空对话", "清除记录", "清除记忆", "清空记忆")


def _is_question(message: str) -> bool:
    """判断消息是否为提问，避免把"如何导入？"误判成执行动作。"""
    return any(word in message for word in _QUESTION_WORDS)


def _copy_id_from(message: str) -> int | None:
    """从消息中提取文案编号（支持 #3、第3条、直接数字）。"""
    match = re.search(r"(?:#|＃|第)?\s*(\d+)", message)
    return int(match.group(1)) if match else None


def _parse_import(message: str) -> str | None:
    """若消息携带导入指令和足够正文，则返回待导入内容，否则返回 None。"""
    if _is_question(message):
        return None
    idx = -1
    verb = None
    for candidate in _IMPORT_VERBS:
        pos = message.find(candidate)
        if pos != -1 and (idx == -1 or pos < idx):
            idx, verb = pos, candidate
    if idx == -1:
        return None
    content = message[idx + len(verb):].lstrip("：:，, \n\t").replace("...", "").replace("……", "")
    return content if len(content) >= _MIN_TEXT_LEN else None


def _query_pending(session_factory, limit: int = 10) -> list[tuple[int, str, str]]:
    """查询最近的待审文案（copy_id, 文章标题, 平台）。"""
    with session_factory() as session:
        rows = session.execute(
            select(PlatformCopy.id, Article.title, PlatformCopy.platform)
            .join(Summary, Summary.id == PlatformCopy.summary_id)
            .join(Article, Article.id == Summary.article_id)
            .join(Review, Review.copy_id == PlatformCopy.id)
            .where(Review.verdict == Verdict.PENDING, PlatformCopy.deleted_at.is_(None))
            .order_by(Review.created_at.desc())
            .limit(limit)
        ).all()
        return [(row[0], row[1] or "", row[2]) for row in rows]


def _load_history(session_factory, limit: int = _MEMORY_LIMIT) -> list[tuple[str, str]]:
    """返回最近 24 小时内的对话历史（时间正序，最多 limit 条）。"""
    since = datetime.now(UTC) - timedelta(hours=_MEMORY_WINDOW_HOURS)
    with session_factory() as session:
        rows = session.scalars(
            select(ChatMessageRecord)
            .where(ChatMessageRecord.created_at >= since)
            .order_by(ChatMessageRecord.id.desc())
            .limit(limit)
        ).all()
    return [(row.role, row.text) for row in reversed(rows)]


def _save_chat_message(session_factory, role: str, text: str) -> None:
    with session_factory() as session:
        session.add(ChatMessageRecord(role=role, text=(text or "")[:4000]))
        session.commit()


def _clear_chat_history(session_factory) -> int:
    with session_factory() as session:
        result = session.execute(delete(ChatMessageRecord))
        session.commit()
        return result.rowcount or 0


def _format_history(rows: list[tuple[str, str]]) -> str:
    if not rows:
        return "最近 24 小时没有对话记录。"
    lines = [
        f"- {'我' if role == 'user' else '助手'}：{text}"
        for role, text in rows
    ]
    return "最近 24 小时的对话记忆：\n" + "\n".join(lines)


async def _try_action(session_factory, content_ops, message: str, scrape_service=None) -> dict | None:
    """尝试识别并执行动作意图；未命中任何动作时返回 None（进入问答）。"""
    if any(word in message for word in _HISTORY_WORDS) and not _is_question(message):
        rows = _load_history(session_factory)
        return {
            "text": _format_history(rows),
            "source": "memory",
            "kind": "history",
            "data": {"items": [{"role": role, "text": text} for role, text in rows]},
        }

    if any(word in message for word in _CLEAR_HISTORY_WORDS) and not _is_question(message):
        cleared = _clear_chat_history(session_factory)
        return {
            "text": f"已清空 {_MEMORY_WINDOW_HOURS} 小时内的对话记忆，共清除 {cleared} 条消息。",
            "source": "action",
            "kind": "clear_history",
            "data": {"cleared": cleared},
        }

    content = _parse_import(message)
    if content:
        try:
            with session_factory() as session:
                data = await content_ops.create_manual_content(session, title=extract_title(content), content=content)
        except ValueError as exc:
            return {"text": f"内容导入失败：{exc}", "source": "action", "kind": "import"}
        return {
            "text": f"内容已导入并完成摘要与全平台扩写，进入待审。文章 #{data['article_id']}，共 {len(data['copy_ids'])} 个平台文案。",
            "source": "action",
            "kind": "import",
            "data": data,
        }

    if scrape_service is not None and not _is_question(message) and any(word in message for word in _CRAWL_VERBS):
        urls = list(dict.fromkeys(u.rstrip(".,;!?）)") for u in _URL_RE.findall(message)))
        if not urls:
            return {
                "text": "未在这段文字中找到链接。请直接粘贴要爬取的链接，或附上带链接的文字（例如：帮我爬取 这篇不错 https://example.com/a）。",
                "source": "action",
                "kind": "scrape",
                "data": None,
            }
        try:
            with session_factory() as session:
                job, dedup_count = scrape_service.create_job(session, urls)
        except QuotaExceededError:
            return {"text": "在途爬取任务太多，请稍后再试。", "source": "action", "kind": "scrape", "data": None}
        except ScrapeError as exc:
            return {"text": f"爬取任务创建失败：{exc.message}", "source": "action", "kind": "scrape", "data": None}
        job_id = job.id
        asyncio.create_task(scrape_service.run_job(session_factory, job_id))
        suffix = f"（跳过 {dedup_count} 条重复）" if dedup_count else ""
        preview = "、".join(urls[:3]) + (" 等" if len(urls) > 3 else "")
        return {
            "text": f"已识别 {len(urls)} 个链接并创建爬取任务 #{job_id}，后台正在抓取：{preview}{suffix}。",
            "source": "action",
            "kind": "scrape",
            "data": {"job_id": job_id, "urls": urls, "dedup_count": dedup_count},
        }

    if any(word in message for word in _PUBLISH_ALL_WORDS) and not _is_question(message):
        with session_factory() as session:
            count = publish_pending_all(session)
        return {"text": f"已一键通过并发布 {count} 条待审文案。", "source": "action", "kind": "publish_all", "data": {"published": count}}

    if "发布" in message and not _is_question(message):
        copy_id = _copy_id_from(message)
        if copy_id:
            try:
                with session_factory() as session:
                    publish = publish_copy(session, copy_id)
            except ReviewNotFoundError:
                return {"text": f"未找到待审文案 #{copy_id}，可能已被发布或不存在。", "source": "action", "kind": "publish"}
            published_at = publish.published_at.replace(microsecond=0).isoformat() if publish.published_at else ""
            return {"text": f"文案 #{copy_id} 已发布，发布时间 {published_at}。", "source": "action", "kind": "publish", "data": {"copy_id": copy_id}}

    if any(word in message for word in _SUMMARY_REGENERATE_WORDS) and not _is_question(message):
        copy_id = _copy_id_from(message)
        if not copy_id:
            return {"text": "请告诉我需要重新生成摘要的文案编号，例如：重新生成摘要 #3。", "source": "action", "kind": "regenerate_summary"}
        try:
            with session_factory() as session:
                data = await content_ops.regenerate_summary(session, copy_id)
        except CopyNotFoundError:
            return {"text": f"未找到文案 #{copy_id}。", "source": "action", "kind": "regenerate_summary"}
        return {"text": f"文案 #{copy_id} 的摘要已重新生成，并已级联重写全部平台文案。", "source": "action", "kind": "regenerate_summary", "data": data}

    if any(word in message for word in _COPY_REGENERATE_WORDS) and not _is_question(message):
        copy_id = _copy_id_from(message)
        if not copy_id:
            return {"text": "请告诉我需要重新扩写的文案编号，例如：重新扩写 #5。", "source": "action", "kind": "regenerate_copy"}
        try:
            with session_factory() as session:
                data = await content_ops.regenerate_copy(session, copy_id)
        except (CopyNotFoundError, PublishedCopyError):
            return {"text": f"无法为文案 #{copy_id} 重新扩写，可能已被发布或不存在。", "source": "action", "kind": "regenerate_copy"}
        return {"text": f"文案 #{copy_id}（{data['platform']}）已重新扩写，并重置为待审。", "source": "action", "kind": "regenerate_copy", "data": data}

    if any(word in message for word in _LIST_WORDS):
        rows = _query_pending(session_factory)
        if not rows:
            return {"text": "目前没有待审文案。", "source": "action", "kind": "pending_list", "data": {"items": []}}
        lines = [f"- #{copy_id}  {title}（{platform}）" for copy_id, title, platform in rows]
        return {"text": "当前待审文案：\n" + "\n".join(lines), "source": "action", "kind": "pending_list", "data": {"items": rows}}

    if any(word in message for word in _STATUS_WORDS):
        with session_factory() as session:
            pending = session.scalar(select(func.count()).select_from(Review).where(Review.verdict == Verdict.PENDING)) or 0
        return {"text": f"当前共有 {pending} 条文案待审核。", "source": "action", "kind": "status", "data": {"pending": pending}}

    return None


def _keyword_fallback(message: str) -> str:
    """关键词匹配回退：按命中率返回最相关的知识条目。"""
    best: tuple[float, str] = (0.0, _PROJECT_OVERVIEW)
    for keywords, answer in _KB:
        # 关键词长度加权：更长的关键词命中权重更高，避免短词误匹配
        score = sum(len(kw) for kw in keywords if kw in message)
        if score > best[0]:
            best = (score, answer)
    return best[1]


async def chat_assistant(session_factory, provider, message: str, content_ops=None, scrape_service=None) -> dict:
    """AI 对话助手：先识别动作，再退回带 24 小时记忆的 LLM 问答 / 知识库回退。"""
    message = (message or "").strip()
    if not message:
        return {"text": "请输入您的问题或要执行的指令。", "source": "fallback", "kind": "empty"}

    history = _load_history(session_factory)
    if content_ops is not None:
        action_result = await _try_action(session_factory, content_ops, message, scrape_service)
        if action_result is not None:
            _save_chat_message(session_factory, "user", message)
            _save_chat_message(session_factory, "assistant", action_result["text"])
            return action_result

    _save_chat_message(session_factory, "user", message)
    context: list[ChatMessage] = [ChatMessage("system", _SYSTEM_PROMPT)]
    context.extend(ChatMessage(role, text) for role, text in history)
    context.append(ChatMessage("user", message))

    if provider is None:
        reply = {"text": _keyword_fallback(message), "source": "fallback", "kind": "qa"}
        _save_chat_message(session_factory, "assistant", reply["text"])
        return reply
    try:
        raw = await asyncio.wait_for(provider.chat(context), timeout=20.0)
        text = raw.strip()
        # LLM 返回过短或空内容时保底走关键词回退
        if len(text) < 5:
            reply = {"text": _keyword_fallback(message), "source": "fallback", "kind": "qa"}
            _save_chat_message(session_factory, "assistant", reply["text"])
            return reply
        reply = {"text": text, "source": "llm", "kind": "qa"}
        _save_chat_message(session_factory, "assistant", text)
        return reply
    except (LLMError, OSError, Exception):  # noqa: BLE001 — 终极保底
        reply = {"text": _keyword_fallback(message), "source": "fallback", "kind": "qa"}
        _save_chat_message(session_factory, "assistant", reply["text"])
        return reply
