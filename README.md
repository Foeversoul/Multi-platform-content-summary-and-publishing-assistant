# 多平台内容总结与发布助手（S1：调度协调 + 信息采集）

## 本地开发
1. `pip install -e ".[dev]"`
2. 复制 `.env.example` 为 `.env`，按需修改
3. 配置 `sources.yaml`
4. 采集一个数据源：`python -m app.cli crawl --source-id demo-news --sync`
5. 手动提交 URL：`python -m app.cli crawl --url https://example.com/article --sync`
6. 跑测试：`python -m pytest`

## Docker 部署（S1）
`docker compose up -d postgres redis worker`

## S2：内容处理
- 事件：`article.crawled` → 生成 `summary` → 发出 `summary.generated`
- 摘要标准：200~400 字；要点 3~5 条（≤60 字）；标题 ≤30 字
- LLM：默认 DeepSeek，配置 `.env` 的 `ASSISTANT_LLM_API_KEY`；失败自动回退抽取式摘要
- 质量：摘要长度/要点数/实体保留率/平均句长自动评分，写入 `summary.scores`

## S3：内容适配 + 质量审核
- 事件：`summary.generated` → 三平台文案 `platform_copy` → `copy.adapted` → 自动评分 `review`（verdict=pending 待人工）
- 平台规范：微博 1~140 字+1~3 标签；朋友圈 60~200 字+emoji；小红书 100~500 字+2~5 标签+emoji（见 `platforms.yaml`）
- 合规：敏感词/广告法违禁词命中即标记；可配置 `ASSISTANT_SENSITIVE_WORDS_FILE` / `ASSISTANT_AD_WORDS_FILE`
- 审核：`style_score` 0-100（≥80 视为 4/5），所有文案默认进入待人工审核

## S4：Web 审核台
启动：`python -m uvicorn app.web.main:app --host 127.0.0.1 --port 8000`
- `/` 待审列表；`/copy/{id}` 预览+复制；标记发布/驳回；`/status` 运行状态

## S5：运维优化
- 日志：worker 启动自动写入 `data/logs/app.log`（轮转 5MB×3），structlog JSON 格式
- 死信兜底：`/failed` 页列出 failed/dead_letter，可"重跑"（重置 pending 重新采集）或"放弃"（dead_letter→rejected）

### 接入新数据源
在 `sources.yaml` 增加条目（type: rss 或 web、url、frequency_minutes），实现对应 `SpiderInterface`（现有 rss/web 均已实现），配置即插即用。

### 接入新平台
在 `platforms.yaml` 增加条目（字数/标签/emoji/style_prompt），适配器按配置生成文案，配置即插即用。

### 覆盖率补跑（正常环境）
```bash
coverage run --source=app -m pytest
coverage report --omit='app/cli.py,app/worker.py' --fail-under=80
```

### 一周稳定性检查清单
- 日志无 ERROR 持续堆积（`data/logs/app.log`）
- 死信可在 `/failed` 人工重跑/放弃
- Redis 队列无积压（`/status` 队列长度回落）
- 采集成功率与端到端延迟符合指标（≥95% / <5 分钟）
