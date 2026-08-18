# S2 内容处理 设计文档

- 日期：2026-08-18
- 状态：由总体架构设计（2026-08-18-multiplatform-content-summary-publisher-design.md）推导；实施前经用户确认
- 关联：总体设计 §2.1（内容处理）、§3（数据模型）、§4（数据流步骤 3）、§10（质量指标）
- 前置：S1 已合并至 master（采集闭环可用，article 状态 CRAWLED）

## 1. 目标与范围

把已采集的 `article`（状态 `crawled`）加工为**标准摘要**：200~400 字摘要 + 3~5 条关键要点（每条 ≤60 字）+ 精简标题（≤30 字），并产出质量评分；成功后 article 状态流转为 `summarized`，发出 `summary.generated` 事件供 S3（适配）消费。

## 2. 输入 / 输出 / 事件 / 状态

| 项 | 定义 |
| --- | --- |
| 输入 | `article`（url、title、text、entities 可重算） |
| 输出 | `summary` 行（summary_text、key_points、short_title、scores） |
| 事件 | 消费 `article.crawled`；产出 `summary.generated`（S2 内无消费方，按 noop 处理） |
| 状态 | `crawled → summarized`（成功）；失败经 registry 重试后 `failed/dead_letter` |

## 3. 处理流水线

1. **降噪**：规范化空白；按句号/问号/感叹号/分号/换行切句；剔除过短且无信息量的噪声句（<10 字且不含数字/字母）；去重连续重复句。
2. **实体抽取（NER）**：jieba.posseg 词性标注映射实体类别（人名 nr、地点 ns、机构 nt/nz、数字 m、时间 t），辅以正则识别日期/数字；输出 `entities: dict[类别, set[实体]]`。不引入额外大依赖，词典可扩展。
3. **关键词**：jieba.analyse TF-IDF 抽取 top 10。
4. **句子重要性打分**：`0.3×位置分 + 0.4×实体密度 + 0.3×标题相似度`（标题用字符 bigram Jaccard）。
5. **抽取式候选**：按分数取 top 句、按原文顺序拼接至 200~400 字预算；作为 LLM 失败时的回退与生成时的上下文。
6. **LLM 生成**：DeepSeek（`app/llm` 抽象层）按 prompt 输出 JSON `{"summary","key_points","short_title"}`；LLM 失败或输出非法时回退抽取式候选（short_title 取原文标题截断）。
7. **后处理校验**：字数 / 要点条数 / 标题长度 / 实体保留率 / 平均句长，写入 `scores`；任一硬性约束（长度、要点条数）不满足时回退抽取式。

## 4. 数据模型

新增 `summary` 表（Alembic 迁移 `add summary table`）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | int PK | |
| article_id | int FK unique | 一篇文章一条摘要 |
| summary_text | Text | 标准摘要 |
| key_points | JSON | 3~5 条要点列表 |
| short_title | String(200) | 精简标题（≤30 字，超出截断） |
| scores | JSON | 质量评分字典 |
| status | String(32) | pending / summarized / failed |
| created_at / updated_at | DateTime | |

## 5. 模块与接口

新增 `app/processor/` 与 `app/llm/`：

| 文件 | 职责 | 关键接口 |
| --- | --- | --- |
| `app/processor/clean.py` | 降噪、切句 | `clean_text(text) -> str`、`split_sentences(text) -> list[str]`、`remove_noise_sentences(list[str]) -> list[str]` |
| `app/processor/entities.py` | NER | `extract_entities(text) -> dict[str, set[str]]` |
| `app/processor/keywords.py` | 关键词 | `extract_keywords(text, top_k=10) -> list[str]` |
| `app/processor/extractive.py` | 句子打分+抽取 | `score_sentences(sentences, title, entities) -> list[float]`、`extractive_summary(sentences, scores, min_chars, max_chars) -> str` |
| `app/processor/summarizer.py` | LLM 生成+回退 | `generate_summary(provider, article_text, title) -> SummarizerResult` |
| `app/processor/quality.py` | 质量评分 | `score_summary(article_text, summary_text, key_points, short_title) -> dict` |
| `app/processor/service.py` | 编排+事件接线 | `ProcessorService.process_article(session, article_id) -> Summary`、`register_processor_handlers(registry, settings, redis, provider=None)` |
| `app/llm/provider.py` | LLM 抽象 | `ChatProvider.chat(messages, temperature) -> str`、`LLMError` |

`Settings` 增加：`llm_api_key`、`llm_base_url`（默认 `https://api.deepseek.com/v1`）、`llm_model`（默认 `deepseek-chat`）、`llm_timeout_seconds`（60）、`llm_max_tokens`（2048）。

## 6. 技术决策（已锁定）

- NER 用 jieba.posseg + 类别映射 + 正则补充；不引入 spaCy/LAC 等重型依赖，词典文件可后续扩展。
- 关键词用 jieba TF-IDF。
- 摘要策略为"抽取式候选 + LLM 生成 + 回退"：LLM 是主路径（质量更好），抽取式是确定性兜底。
- LLM 调用支持注入 transport 以便测试 mock；生产走 DeepSeek OpenAI 兼容接口。
- 事件接线不改动 S1 的 `collector.service.build_registry`，新增 `register_processor_handlers` 由 worker 调用，保持 S1 测试不变。
- 质量评分全部为可自动计算的规则（字数/条数/长度/实体保留率/平均句长）；ROUGE 仅用于标注集验证，不作为运行时门槛。

## 7. 质量指标与测试

| 指标 | 目标 |
| --- | --- |
| 摘要长度 | 200~400 字（自动校验） |
| 要点条数 | 3~5 条，每条 ≤60 字 |
| 精简标题 | ≤30 字 |
| 实体保留率 | ≥95%（样例/抽查） |
| 可读性 | 平均句长 ≤25 字 |
| ROUGE-1 | ≥0.4（标注样例集，S2 用固定 fixture 验证） |
| 测试 | 单元（clean/entities/keywords/extractive/quality/summarizer）+ 集成（article→summary 全链路，LLM mock）；核心模块覆盖率 ≥80% |

## 8. 范围外（不属于 S2）

- 平台风格改写与合规（S3）。
- 事实一致性 LLM 判定（S3 审核强化）。
- ROUGE 大样本标注集建设（S5 运维优化）。
- 去重（S1 已完成）。
