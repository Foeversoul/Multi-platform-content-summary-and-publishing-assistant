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
