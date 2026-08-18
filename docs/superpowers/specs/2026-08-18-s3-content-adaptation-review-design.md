# S3 内容适配 + 质量审核 设计文档

- 日期：2026-08-18
- 状态：由总体架构设计推导；实施前经用户确认
- 关联：总体设计 §2.1（内容适配/质量审核）、§3（数据模型）、§4（数据流步骤 4/5）、§10（质量指标）、§5（PlatformAdapter 扩展点）
- 前置：S1 采集闭环 + S2 摘要闭环均已合并至 master

## 1. 目标与范围

把 `summary`（状态 `summarized`）加工为**微博 / 朋友圈 / 小红书**三平台发布文案：规则引擎硬性限制 → LLM 按平台风格改写 → 合规校验（敏感词/广告法）→ 自动审核评分，产出 `platform_copy` 与 `review` 记录；所有文案默认进入**待人工审核**状态（S4 审核台展示）。article 状态流转 `summarized → adapted → reviewed`。

## 2. 输入 / 输出 / 事件 / 状态

| 项 | 定义 |
| --- | --- |
| 输入 | `summary`（summary_text、key_points、short_title、scores） |
| 输出 | 每平台一条 `platform_copy` + 一条 `review`（verdict=pending） |
| 事件 | 消费 `summary.generated`、`copy.adapted`；产出 `copy.adapted`、`review.passed`（S3 内均无下游消费者，按 noop 处理） |
| 状态 | article：`summarized → adapted`（全部文案生成）→ `reviewed`（全部自动审核完成）；copy：`pending → adapted → reviewed` |

## 3. 平台风格规范（锁定）

| 平台 | 字数 | 风格 | 标签 / emoji |
| --- | --- | --- | --- |
| 微博 weibo | 1~140 字 | 倒金字塔（核心信息先行）；口语化；禁长从句、标题党 | 1~3 个 `#话题#` |
| 朋友圈 moments | 60~200 字 | 第一人称分享视角；生活化、真诚；禁硬广、营销腔 | 1~3 个 emoji |
| 小红书 xhs | 100~500 字 | 第一人称种草分享；标题行作首行；禁硬广、导流 | 2~5 个 `#话题#` + 1~3 个 emoji |

风格规范写入 `platforms.yaml`（含 LLM style_prompt），新增平台实现 `PlatformAdapter` 模式（validate/format_prompt/post_process）即注册即用。

## 4. 数据模型（新增，Alembic 迁移）

| 表 | 字段 |
| --- | --- |
| `platform_copy` | id、summary_id FK、platform(weibo/moments/xhs)、text、status(pending/adapted/reviewed)、created_at、updated_at |
| `review` | id、copy_id FK unique、verdict(pending/pass/reject)、scores JSON、comment、created_at |

## 5. 处理流水线

1. **适配**（`summary.generated` 触发）：读平台配置 → 规则引擎校验 summary 可适配 → LLM 按平台 style_prompt 改写（输出 JSON `{"text": ...}`）→ 字数超限截断/回退 → 写 `platform_copy`（adapted）→ 发 `copy.adapted`；全部平台完成后 article → `adapted`。
2. **合规校验**：敏感词表 + 广告法违禁词表命中检测，违规项记录到 scores，不阻断产出（人工审核兜底），但命中即标记。
3. **审核评分**（`copy.adapted` 触发）：字数合规、标签数/emoji 数达标、敏感词/广告法命中数、风格规则分（≥80 分视为 4/5）→ 写 `review`（verdict=pending，待人工）→ copy → `reviewed`；该 article 全部 copy 审核完 → article → `reviewed` → 发 `review.passed`。

## 6. 模块与接口

| 文件 | 职责 | 关键接口 |
| --- | --- | --- |
| `app/adapter/platforms.py` | 平台配置 | `load_platforms(path) -> dict[str, PlatformConfig]`、`PlatformConfig` |
| `app/adapter/wordlists.py` | 词表 | `load_wordlist(path) -> list[str]`、内置默认敏感词/广告法词表、`find_hits(text, words) -> list[str]` |
| `app/adapter/rules.py` | 规则引擎 | `validate_text(platform, text) -> RulesResult`（字数/标签/emoji）、`count_tags`、`count_emojis` |
| `app/adapter/copywriter.py` | LLM 改写 | `async generate_copy(provider, summary, platform) -> CopyResult(text, source)`；失败回退摘要截断 |
| `app/adapter/compliance.py` | 合规 | `check_compliance(text) -> dict`（敏感词/广告法命中） |
| `app/adapter/service.py` | 适配编排+接线 | `AdapterService.adapt_summary(session, summary_id)`、`register_adapter_handlers(registry, settings, redis, provider=None)` |
| `app/reviewer/quality.py` | 审核评分 | `score_copy(platform, text, compliance) -> dict`（含 style_score 0-100） |
| `app/reviewer/service.py` | 审核编排+接线 | `ReviewerService.review_copy(session, copy_id)`、`register_reviewer_handlers(registry, settings, redis)` |
| `app/storage/models.py` | 表 | `PlatformCopy`、`Review`、`CopyStatus`、`Verdict` |
| `app/worker.py` | 注册 | main 中追加 adapter/reviewer handlers |

## 7. 质量指标与测试

| 指标 | 目标 |
| --- | --- |
| 微博字数 | 1~140（自动校验） |
| 朋友圈字数 | 60~200 |
| 小红书字数 | 100~500 |
| 风格匹配度 | ≥4/5（规则分 ≥80/100，自动） |
| 敏感词/广告法 | 词表命中检测，命中即标记（人工兜底） |
| 测试 | 规则/词表/改写/合规/评分/服务/集成逐模块单测 + 端到端（summary→三平台 copy→review→review.passed）；LLM 用 FakeProvider |

## 8. 范围外（不属于 S3）

- Web 审核台与人工发布记录（S4）。
- 图片/配图素材生成。
- 全自动 API 发布（人工辅助发布为既定形态）。
- ROUGE 标注集建设（S5）。
