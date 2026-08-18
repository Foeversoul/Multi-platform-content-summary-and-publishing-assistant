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
