# S5 运维优化 设计文档

- 日期：2026-08-18
- 状态：由总体架构设计推导；实施前经用户确认
- 关联：总体设计 §7（错误处理/可观测性）、§11 路线图 S5；S1 状态机、S4 审核台
- 前置：S1~S4 已合入 master（端到端闭环可用）

## 1. 目标与范围

收尾加固：

1. **统一结构化日志**：structlog 输出到控制台 + `data/logs/app.log`（RotatingFileHandler，5MB×3）。
2. **死信/失败人工兜底**：审核台新增 `/failed` 页——列出 `failed`/`dead_letter` 文章，提供"重跑"（重置 `pending` 并重新入队采集）与"放弃"（置 `rejected`）操作。
3. **接入演练与文档**：新增数据源/平台即插即用指南（SpiderInterface / platforms.yaml）；覆盖率补跑命令；一周稳定性检查清单。

## 2. 状态机扩展

`app/orchestrator/state.py` 增加人工兜底出口：

- `FAILED → {PENDING, DEAD_LETTER}`（原样）
- `DEAD_LETTER → {PENDING, REJECTED}`（新增：人工重跑 / 人工放弃）

## 3. Web 新增路由（S4 审核台扩展）

| 路由 | 方法 | 行为 |
| --- | --- | --- |
| `/failed` | GET | 列出 failed/dead_letter 文章（标题、来源、状态、更新时间、原因） |
| `/failed/{article_id}/retry` | POST | 状态→pending，emit `crawl.requested`（source_id 或 url），303 回 /failed |
| `/failed/{article_id}/discard` | POST | 状态→rejected（仅 dead_letter），303 回 /failed |

## 4. 模块与接口

| 文件 | 职责 |
| --- | --- |
| `app/log.py` | `setup_logging(log_dir=None, level=INFO)`：structlog 键值/JSON + RotatingFileHandler |
| `app/orchestrator/state.py` | 状态机扩展（DEAD_LETTER 出口） |
| `app/web/main.py` | `/failed`、`/failed/{id}/retry`、`/failed/{id}/discard` |
| `app/web/templates/failed.html` | 失败/死信列表 |
| `app/worker.py` | main 中调用 `setup_logging(settings.data_dir / "logs")` |
| `README.md` | 接入指南、覆盖率补跑命令、检查清单 |

## 5. 测试

- 状态机：DEAD_LETTER→PENDING / DEAD_LETTER→REJECTED 合法。
- Web：/failed 列表展示；retry 后状态 pending + 事件入队；discard 后 rejected。
- 日志：setup_logging 在 tmp 目录生成 app.log。

## 6. 范围外（不属于 S5）

- 真实一周连跑（由部署环境执行；本仓库提供检查清单）。
- 告警推送（邮件/微信通知）。
- Playwright 动态渲染采集（保留 render 配置位，未实现）。
- ROUGE 大样本标注集。
