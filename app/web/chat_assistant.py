"""AI 对话助手：基于项目知识的问答模块。

LLM 可用时走对话生成；不可用时按关键词匹配回退到内置知识库。
"""

import asyncio

from app.llm.provider import ChatMessage, LLMError

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
        + "• 微博（≤140字，1-3 个话题标签，倒金字塔口语化风格）\n"
        + "• 朋友圈（60-200字，第一人称分享视角，可加 emoji）\n"
        + "• 小红书（100-500字，种草分享风格，2-5 个话题标签 + emoji）\n"
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
    "你是多平台内容总结与发布助手项目的 AI 助手。你的职责是帮助用户了解和使用这个项目。\n"
    + "项目核心流程：采集 → AI 摘要 → 多平台文案扩写 → 人工审核 → 发布。\n"
    + "支持的内容导入方式：URL 上传爬取（含 OpenCLI 浏览器渲染兜底）、RSS 订阅、手动内容上传（文本/文件）。\n"
    + "支持的平台：微博、朋友圈、小红书（可在 platforms.yaml 扩展）。\n"
   + "主要功能页面：待审列表、内容导入、运行总览、回收站、死信管理。\n"
   + "快照兜底：常规爬虫和 OpenCLI 渲染均失败时，自动用 Playwright 加载页面 + OCR 识别文字提取正文。\n"
   + "架构：基于 Redis Streams 事件总线的多智能体流水线，worker 进程消费事件自动执行。\n"
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


def _keyword_fallback(message: str) -> str:
    """关键词匹配回退：按命中率返回最相关的知识条目。"""
    best: tuple[float, str] = (0.0, _PROJECT_OVERVIEW)
    for keywords, answer in _KB:
        # 关键词长度加权：更长的关键词命中权重更高，避免短词误匹配
        score = sum(len(kw) for kw in keywords if kw in message)
        if score > best[0]:
            best = (score, answer)
    return best[1]


async def chat_assistant(provider, message: str) -> dict:
    """AI 对话助手：LLM 可用时走对话，不可用走关键词回退。"""
    if provider is None:
        return {"text": _keyword_fallback(message), "source": "fallback"}
    try:
        raw = await asyncio.wait_for(
            provider.chat([
                ChatMessage("system", _SYSTEM_PROMPT),
                ChatMessage("user", message),
            ]),
            timeout=20.0,
        )
        text = raw.strip()
        # LLM 返回过短或空内容时保底走关键词回退
        if len(text) < 5:
            return {"text": _keyword_fallback(message), "source": "fallback"}
        return {"text": text, "source": "llm"}
    except (LLMError, TimeoutError, OSError, Exception):  # noqa: BLE001 — 终极保底
        return {"text": _keyword_fallback(message), "source": "fallback"}
