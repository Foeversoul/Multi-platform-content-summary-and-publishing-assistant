# 多平台内容总结与发布助手

基于事件总线的多智能体内容流水线：**采集 → 处理 → 适配 → 审核 → 发布**，附 Vue 3 Web 审核台与 URL 上传爬取控制台。

## 架构总览

```
┌──────────────┐  crawl.requested  ┌──────────────┐  article.crawled  ┌──────────────┐
│ 调度协调智能体 │ ────────────────▶ │  信息采集智能体  │ ────────────────▶ │ 内容处理智能体 │
└──────────────┘                    └──────────────┘                    └──────────────┘
                                                                              │ summary.generated
┌──────────────┐  review.passed    ┌──────────────┐  copy.adapted    ┌───────▼────────┐
│ 质量审核智能体 │ ────────────────▶ │ 内容适配智能体 │ ◀─────────────── │                │
└──────────────┘                    └──────────────┘                   └────────────────┘
```

- 事件总线：Redis Streams（`assistant:events`），消费组 `workers`；事件日志落库（`event_log`）支持死信重跑/放弃
- URL 上传爬取：`ScrapeService`（任务/条目双状态机、并发 ≤5、14 种错误分类、SSRF 防护、配额限流），成功条目自动 `emit article.crawled` 接入下游
- 前端：`web-ui/`（Vue 3 + Vite + TypeScript + Pinia + Element Plus），包含审核台、URL 上传爬取控制台、死信/回收站管理、手动内容上传、AI 对话助手与状态看板，REST 契约见「REST API」

## 本地开发

环境：Python ≥3.12、Node.js ≥18（仅前端构建）、Redis ≥6（生产 7.x）。

```bash
# 1. 后端依赖
pip install -e ".[dev]"

# 2. 配置
copy .env.example .env   # 设置 ASSISTANT_LLM_API_KEY 等
# 配置 sources.yaml（RSS/web 源）与 platforms.yaml（平台规范）

# 3. 启动后端（含 REST API 与 HTML 路由）
python -m uvicorn app.web.__main__:app --host 127.0.0.1 --port 8000

# 4. 启动 worker（消费事件链）
python -m app.worker

# 5. 启动前端（Vue 审核台，开发模式，/api 自动代理到 8000）
cd web-ui
npm install
npm run dev
# 生产构建：npm run build → 产物 dist/
# 类型检查/单测：npm run typecheck / npm test
```

验证：浏览器打开 `http://localhost:5173`（前端）、`http://127.0.0.1:8000/docs`（Swagger）。

## REST API（IF-01~13）

统一响应包 `{"code": 0, "message": "ok", "data": ...}`，业务失败 `code != 0`。

| 编号 | 方法 | 路径 | 说明 |
| --- | --- | --- | --- |
| IF-01 | GET | `/api/reviews` | 待审列表（分页/筛选/搜索） |
| IF-02 | GET | `/api/reviews/{copy_id}` | 详情（原文/摘要/文案/评分/发布状态） |
| IF-03 | POST | `/api/reviews/{copy_id}/publish` | 发布 |
| IF-04 | POST | `/api/reviews/{copy_id}/reject` | 驳回（`comment` 必填） |
| IF-05 | GET | `/api/status` | 运行状态统计 |
| IF-06 | GET | `/api/failed` | 死信事件列表 |
| IF-07 | POST | `/api/failed/{event_id}/retry` | 死信重跑 |
| IF-08 | POST | `/api/failed/{event_id}/discard` | 死信放弃 |
| IF-09 | POST | `/api/scrape/jobs` | 创建爬取任务（`{urls:[...]}`，单批 ≤1000） |
| IF-10 | GET | `/api/scrape/jobs/{job_id}` | 任务进度与汇总（轮询） |
| IF-11 | GET | `/api/scrape/jobs/{job_id}/items` | 任务条目明细（分页/按状态筛选） |
| IF-12 | GET | `/api/scrape/items/{item_id}` | 单条目结果 |
| IF-13 | POST | `/api/scrape/jobs/{job_id}/items/{item_id}/retry` | 失败条目重新提交 |

爬取错误码：`INVALID_URL_FORMAT` / `UNSUPPORTED_PROTOCOL` / `DNS_FAILED` / `CONNECTION_REFUSED` / `TIMEOUT` / `SSL_ERROR` / `HTTP_403` / `HTTP_404` / `HTTP_429` / `HTTP_5XX` / `ROBOTS_BLOCKED` / `EMPTY_CONTENT` / `RENDER_UNSUPPORTED` / `DUPLICATE`。

### 补充 API（内容 AI / 内容管理 / 对话）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/scrape/jobs` | 爬取任务历史列表（分页） |
| POST | `/api/reviews/{copy_id}/summary/regenerate` | AI 重新生成摘要并级联重写各平台文案（已发布除外） |
| PUT | `/api/reviews/{copy_id}/summary` | 手动编辑摘要（`summary_text` 必填），级联重写文案 |
| POST | `/api/reviews/{copy_id}/copy/regenerate` | 重写单条平台文案（已发布禁止） |
| POST | `/api/reviews/{copy_id}/copy/preview` | 按指定平台风格预览扩写（不落库） |
| POST | `/api/reviews/batch-publish` | 一键通过全部待审核文案（仅处理 `pending`） |
| POST | `/api/reviews/batch-delete` | 批量软删除（`copy_ids` 整数列表，移入回收站） |
| POST | `/api/reviews/{copy_id}/delete` | 单条软删除（移入回收站） |
| GET | `/api/recycle` | 回收站列表（已删除文案） |
| POST | `/api/recycle/{copy_id}/restore` | 从回收站恢复 |
| POST | `/api/recycle/batch-restore` | 批量恢复（`copy_ids` 整数列表） |
| DELETE | `/api/recycle/{copy_id}` | 永久删除（连带审核/发布记录，不可恢复） |
| POST | `/api/recycle/batch-purge` | 批量永久删除（`copy_ids` 整数列表，不可恢复） |
| POST | `/api/content/manual` | 手动上传文本/Markdown，同步完成摘要 → 扩写 → 待审 |
| POST | `/api/content/manual/file` | 上传文件（`.txt` / `.md` / `.docx`，≤2MB）进入完整 AI 流程 |
| POST | `/api/chat` | AI 对话助手：问答 + 按指令执行动作（导入 / 爬取 / 发布 / 重新生成 / 查询） |
| GET | `/api/health` | 健康检查（DB/Redis 探针，`/api/health` 鉴权豁免） |

## 主要命令

```bash
python -m app.cli crawl --source-id demo-news --sync   # 采集数据源
python -m app.cli crawl --url https://example.com/a --sync  # 手动 URL
python -m pytest                       # 全量测试
python -m pytest -q tests/test_scrape_validator.py tests/test_scrape_service.py tests/test_api.py  # URL 爬取模块
python -m ruff check app tests         # 静态检查
coverage run -m pytest && coverage report --fail-under=80   # 覆盖率（要求 ≥80%）
```

## Docker 部署

```bash
docker compose up -d postgres redis worker app frontend
# 前端：http://localhost:8080（nginx 托管，/api 自动反代到后端）
# 后端：http://localhost:8000（Swagger: /docs）
# worker 自动执行 alembic upgrade head；app 服务含健康检查（/api/health）
```

> 说明：本地开发默认使用 SQLite（见 `.env` 示例）；生产容器内置 psycopg 驱动，
> 本地裸环境如改用 PostgreSQL 需先 `pip install "psycopg[binary]"`。

## API 鉴权（可选，SEC-01 基线）

设置环境变量 `ASSISTANT_API_TOKEN` 后，所有 `/api/*` 请求须携带请求头
`X-API-Token: <token>`（`/api/health` 除外，供探针使用）。前端通过
`VITE_API_TOKEN` 或 localStorage `api_token` 注入凭证，未配置时返回 401。

## 接入新数据源 / 新平台

- 新数据源：`sources.yaml` 增加条目（type: rss/web、url、frequency_minutes），实现对应 `SpiderInterface` 即插即用
- 新平台：`platforms.yaml` 增加条目（字数/标签/emoji/style_prompt），适配器按配置生成文案

### OpenCLI 数据源（增强版爬虫）

内置 `RSS`/`Web` 爬虫只能抓取静态页面，遇到需要登录态或 JS 渲染的平台（B站、知乎、小红书等）会受限。
`type: opencli` 的数据源通过 [OpenCLI](https://github.com/jackwener/OpenCLI) 命令驱动你已登录的
Chrome 抓取这些平台，并把输出接入本项目的去重/落库/事件流水线。

前置要求：Node.js ≥ 20、安装 `opencli`（`npm install -g @jackwener/opencli`）、安装 Browser Bridge
扩展并让 `opencli doctor` 通过。配置示例（见 `sources.yaml`）：

```yaml
- id: bilibili-hot
  name: B站热榜
  type: opencli
  site: bilibili
  command: hot
  limit: 20
  frequency_minutes: 120
```

字段说明：`site`/`command` 对应 opencli 内置适配器（如 `bilibili hot`、`zhihu hot`、
`xiaohongshu search`）；`limit` 自动追加 `--limit N`；`args` 透传额外参数（如搜索词）；
`profile` 指定多 Chrome 配置文件别名；`opencli_bin` 自定义命令路径。

对应命令即 `opencli <site> <command> [args] -f json`，输出会规整为标题/正文/链接/发布时间后入库。
手动触发：`python -m app.cli crawl --source-id bilibili-hot --sync`。

### URL 上传自动渲染兜底

在为爬取控制台输入任意 URL 时，若目标页是 JS 渲染（如 B站排行榜这类 SPA），静态 `WebSpider`
抓不到正文，会自动改用 `opencli web read --url <url> --stdout true` 通过已登录浏览器渲染并导出
Markdown 后入库。该行为由 `ASSISTANT_OPENCLI_RENDER_FALLBACK` 控制（默认开启），多 Chrome 配置下
可通过 `ASSISTANT_OPENCLI_PROFILE` 指定别名（如 `9hrejvdm`）。这样“粘贴 URL → 自动爬取正文”
即可覆盖常规静态页面和需要 JS 渲染 / 登录态的页面。

### 视频链接采集

粘贴 B 站 / YouTube 视频链接进行爬取时，会走专门的视频解析：优先按官方命令抓取
（B 站 `bilibili video` + `bilibili summary`），失败则回退到页面 `meta` 简介，提取干净的
标题、官方简介与时间戳章节大纲，剔除评论区、推荐位、互动数字等噪声后入库。由于视频正文通常很短，
摘要器会对这类短素材自动补写一段完整的中文描述（不编造素材中不存在的内容）。

支持平台：`bilibili.com`（含 `b23.tv` 短链与 `/video/` 页面）、YouTube（`watch` / `shorts` / `youtu.be`）。

### AI 对话助手（可执行动作）

`POST /api/chat` 的消息既是问答也可以是执行指令。LLM 可用时走对话，离线时按关键词回退到知识库；
当消息命中明确的执行意图时，会直接调用对应模块动作并把结果回写到回答中，包含：

- **导入**：粘贴文本/Markdown（≥20 字），自动完成摘要 → 全平台扩写 → 待审
- **爬取**：消息含链接（如「帮我爬取 https://…」），创建爬取任务并在后台抓取
- **发布**：单条（指定编号，如「发布 #3」）或一键发布全部待审
- **重新生成**：按编号重新生成摘要或扩写单条平台文案
- **查询**：列出待审列表、当前待审数量 / 运行状态

带疑问语气（“如何…”“…？”）的消息不会触发动作，会当作普通问答处理。
