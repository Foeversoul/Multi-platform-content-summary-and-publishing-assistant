# 多平台内容总结与发布助手 — 总体架构设计

- 日期：2026-08-18
- 状态：已与用户逐屏确认（brainstorming 流程）
- 关联需求文档：`项目准备/多平台内容总结与发布助手.md`
- 技术路线：Python 3.12 · 单机部署 · 事件驱动单体模块化应用（方案 A）

## 1. 背景与目标

把来自多个信息源的内容（首期：RSS + 少量公开网页）自动加工为"标准摘要 + 多平台发布文案"，经自动评分与人工审核后，由用户手动发布到微博、朋友圈、小红书，帮助内容运营者提高素材生产效率。

### 1.1 关键决策（brainstorming 结论）

| 决策点 | 结论 |
| --- | --- |
| 产出方式 | 先总体架构设计，再按子项目逐个走 spec → 计划 → 实现 → 验证 |
| 第一份子项目 spec | S1 调度协调 + 信息采集 |
| 部署形态 | 单机（个人 / 小团队） |
| 发布方式 | 人工辅助发布：系统产出文案，用户复制后手动发布 |
| 目标平台 | 微博、朋友圈、小红书（均为人工发布，无平台 API 依赖） |
| 大模型 | 国内大模型 API（默认 DeepSeek），LLM Provider 抽象层可切换 |
| 技术栈 | Python 3.12 |
| 交互界面 | Web 审核台（FastAPI） |
| 数据源 | RSS 为主 + 少量公开网页正文抽取 |
| 规模假设 | 每天几十篇，单机运行 |

## 2. 总体架构

采用"单体模块化应用 + 事件驱动流水线"。一个 Python 代码库，五个逻辑模块由"调度协调"以事件路由方式驱动；基础设施只保留 PostgreSQL、Redis Streams、本地磁盘、LLM Provider。该方案保留了需求文档中"事件驱动、状态机、幂等重试"的核心设计意图，同时砍掉单机用不到的 Kafka / ES / 对象存储（保留升级路径）。

```
RSS 源 / 公开网页 / 手动提交 URL
                │
                ▼
    调度协调（事件路由 · 状态机 · 幂等重试 · 死信）
                │ 分派任务
                ▼
信息采集 → 内容处理 → 内容适配 → 质量审核
   │           │          │          │
原文入库    标准摘要    平台文案    评分/合规
                │
                ▼
PostgreSQL（状态/元数据） · Redis Streams（任务队列）
本地磁盘（原始 HTML/文本） · LLM Provider（国内大模型 API）
                │
                ▼
      Web 审核台（FastAPI） ── 人工复制发布 ──► 微博 / 朋友圈 / 小红书
```

### 2.1 模块职责

- **调度协调**：事件路由、任务状态机、幂等重试、死信管理、定时调度（APScheduler 按数据源频率策略触发采集）。
- **信息采集**：RSS 解析、网页正文抽取、URL/内容去重、限速与反爬应对、异常重试。
- **内容处理**：降噪、NER 实体抽取、关键词与句子重要性打分、标准摘要（抽取式 + LLM 生成式）。
- **内容适配**：规则引擎处理硬性限制 → LLM 按平台风格改写 → 敏感词 / 广告法合规校验。
- **质量审核**：ROUGE 评分、实体保留率、可读性评分、合规校验；结果回流用于优化。
- **Web 审核台**：待审核列表、摘要/文案预览、一键复制、标记已发布 / 驳回、运行状态页（队列长度、失败数、死信数）。

### 2.2 基础设施职责

- **PostgreSQL**：所有业务状态与元数据（source / article / summary / platform_copy / review / publish）。
- **Redis Streams**：轻量任务队列，承载各阶段事件消息；不做 Kafka。
- **本地磁盘**：`data/raw/` 存原始 HTML/文本，`data/logs/` 存日志。
- **LLM Provider**：`chat()` 抽象，默认 DeepSeek，通义 / GLM 可配置切换。

## 3. 数据模型与状态机

### 3.1 核心实体（PostgreSQL）

| 表 | 关键字段 |
| --- | --- |
| `source` | id、type(rss/web)、url、名称、抓取频率策略、enabled |
| `article` | id、source_id、url、title、publish_time、content_hash、原文路径、status |
| `summary` | id、article_id、摘要文本、关键要点(3~5条)、精简标题(≤30字)、评分 |
| `platform_copy` | id、summary_id、platform(weibo/moments/xhs)、文本、字数、合规标记 |
| `review` | id、copy_id（或 summary_id）、verdict(pass/reject)、评分、comment |
| `publish` | id、copy_id、status(pending/published/skipped)、published_at |

### 3.2 任务状态机（article 级）

`pending → crawled → summarized → adapted → reviewed → published`

- 失败侧：`failed`（重试上限内回滚）；`dead_letter`（重试耗尽，转人工兜底）。
- 审核侧：`rejected`（人工驳回）。
- 状态迁移仅允许合法路径，非法迁移直接报错并记录。

### 3.3 事件模型

统一事件格式：`event_id · event_type · payload · created_at`。

- `article.crawled` → 触发去重 + 摘要
- `summary.generated` → 触发平台适配
- `copy.adapted` → 触发质量审核
- `review.passed` → 进入 Web 审核台待审列表

消费按 `event_id` 幂等去重。

## 4. 端到端数据流

1. **调度**：按数据源频率策略生成采集任务，写入 Redis Streams；状态 `pending`。
2. **采集**：RSS 解析 / 网页正文抽取 → 去重（URL 精确去重 + content_hash 精确去重 + simhash 近似去重，相似度阈值 0.95、30 天时间窗口）→ 原文落本地磁盘、元数据入 PostgreSQL；状态 `crawled`。
3. **处理**：降噪 → NER/关键词 → 标准摘要 200~400 字 + 3~5 条要点 + 精简标题 ≤30 字 → 质量初评；状态 `summarized`。
4. **适配**：规则引擎（字数/硬限制）→ LLM 按平台风格改写 → 敏感词 + 广告法合规校验；每个平台生成一条文案；状态 `adapted`。
5. **审核**：ROUGE / 实体保留率 / 可读性 / 合规评分；达标 → 进入待人工审核；不达标 → `failed` 并记录原因；状态 `reviewed`。
6. **人工发布**：Web 审核台预览 → 一键复制到微博/朋友圈/小红书 → 标记 `published`；驳回 → `rejected`。

## 5. 模块接口与扩展性

模块间只通过以下稳定接口通信：

| 模块 | 接口 | 说明 |
| --- | --- | --- |
| 采集 | `fetch(source) → Article`、`parse(html) → text` | 新数据源实现 `SpiderInterface`（parse / frequency_policy），配置注册即用 |
| 处理 | `summarize(article) → Summary` | 去重 / 降噪 / 抽取式 + 生成式摘要 |
| 适配 | `adapt(summary, platform) → Copy` | 新平台实现 `PlatformAdapter`（validate / format_prompt / post_process） |
| 审核 | `review(summary\|copy) → Score` | ROUGE / 实体保留率 / 可读性 / 合规 |
| 调度协调 | `emit(event) → handlers` | 事件路由 + 状态机 + 重试；`SkillRegistry` 挂接处理函数 |
| LLM | `chat(messages) → str` | Provider 抽象，配置切换厂商 |

## 6. 技术选型明细

| 领域 | 选型 |
| --- | --- |
| 语言 / 框架 | Python 3.12 · FastAPI + Jinja2（审核台，少量原生 JS） |
| ORM / 迁移 | SQLAlchemy 2.0 + Alembic |
| 存储 / 队列 | PostgreSQL · Redis Streams（redis-py）· 本地磁盘 |
| 采集 | httpx + feedparser（RSS）· Playwright（动态页面）· readability-lxml + BeautifulSoup（正文抽取） |
| NLP / 质量 | jieba + TextRank · content_hash + simhash 去重 · rouge-score |
| LLM | Provider 抽象，默认 DeepSeek（OpenAI 兼容接口） |
| 调度 | APScheduler（数据源定时抓取） |
| 测试 | pytest + pytest-asyncio，LLM 用固定响应 mock |
| 部署 | docker-compose（postgres / redis / app / worker）；本地开发可直接起 Python 进程 |

## 7. 错误处理与可观测性

- **分模块重试**：采集 4xx 直接失败、5xx 指数退避 3 次；LLM 调用超时 + 重试；处理 / 适配各重试 2 次。
- **死信 + 人工兜底**：重试耗尽 → `dead_letter`，审核台可手动重跑或放弃。
- **限速合规**：单域名 QPS≤1、随机延时 3~8 秒、遵守 robots.txt、UA 池轮换；IP 封禁时暂停该域名 30 分钟。
- **幂等**：按 `event_id` 去重消费；状态机只允许合法迁移。
- **日志 / 观测**：structlog 结构化日志带 trace id；审核台"运行状态"页展示队列长度、失败数、死信数、最近任务。

## 8. 测试策略

1. **单元测试**：每模块纯逻辑（去重、降噪、字数校验、合规过滤、状态迁移），核心路径覆盖率 ≥80%。
2. **集成测试**：本地 fixture（样例 RSS / 网页）跑全链路，LLM 用固定响应 mock，验证状态机终点正确。
3. **质量测试**：标注样例集验证 ROUGE / 实体保留率 / 字数合规阈值。
4. **人工验收**：审核台走查——列表、预览、复制、标记发布全流程。

## 9. 部署形态与目录结构

### 9.1 运行方式

docker-compose 提供 `postgres`、`redis`、`app`（FastAPI 审核台）、`worker`（流水线消费进程）四个服务；本地开发也可不依赖 Docker 直接运行 Python 进程。

### 9.2 配置

- `.env`：LLM 密钥、数据库 / Redis 连接串。
- `sources.yaml`：数据源清单（RSS / 网页、频率策略）。
- `platforms.yaml`：平台风格参数（微博 ≤140 字、朋友圈 60~200 字等）。

### 9.3 建议目录

```
content-assistant/
├─ app/
│  ├─ orchestrator/   # 调度协调：事件路由、状态机、重试、死信
│  ├─ collector/      # 信息采集：RSS / 网页抓取、去重、限速
│  ├─ processor/      # 内容处理：降噪、NER、摘要
│  ├─ adapter/        # 内容适配：规则引擎 + LLM 改写 + 合规
│  ├─ reviewer/       # 质量审核：评分与校验
│  ├─ web/            # FastAPI 审核台
│  ├─ storage/        # DB 访问、本地文件存储
│  ├─ llm/            # LLM Provider 抽象
│  ├─ config.py / main.py / worker.py
├─ data/              # 原始文件 + 日志（gitignore）
├─ tests/
├─ alembic/
├─ docker-compose.yml
└─ pyproject.toml
```

## 10. 质量指标（验收基线）

以下指标来自需求文档，作为系统验收基线；标注样例集用于自动验证，无标注数据时以人工审核抽查为准。

| 指标 | 目标值 |
| --- | --- |
| 采集成功率 | ≥95% |
| 标准摘要长度 | 200~400 字 |
| 关键实体保留率 | ≥95% |
| 事实一致性 | ≥95% |
| 可读性评分 | ≥4/5 |
| 微博文案字数 | ≤140 字 |
| 朋友圈文案字数 | 60~200 字 |
| 风格匹配度 | ≥4/5 |
| 端到端成功率 | ≥95% |
| 端到端延迟 | <5 分钟 |
| 单测覆盖率 | 核心路径 ≥80% |

## 11. 子项目路线图

每个子项目单独走 spec → 计划 → 实现 → 验证，S1 之后纵向接力。

| 编号 | 范围 | 验证标准 |
| --- | --- | --- |
| S1 | 调度协调 + 信息采集（第一份详细 spec） | 采集闭环：URL/源 → 去重入库；pytest 通过 |
| S2 | 内容处理 | 摘要闭环：入库文章 → 标准摘要；质量阈值测试通过 |
| S3 | 内容适配 + 质量审核 | 三平台文案闭环 + 合规校验 |
| S4 | Web 审核台 | 待审列表 / 预览 / 复制 / 标记发布 / 运行状态页人工走查通过 |
| S5 | 运维优化 | 日志监控完善、死信人工兜底、新源/新平台接入演练；连跑一周无 P0 问题 |

## 12. 明确不在范围内

- 全自动 API 发布（微博 API 等；人工辅助跑通后再评估）。
- 多租户、横向扩展、高可用集群。
- Kafka / Elasticsearch / 对象存储（仅保留升级路径，不在首期引入）。
- 配图素材生成（首期仅文案，用户自行配图；可作后续增强项）。
- 移动端 App。
