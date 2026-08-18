# S4 Web 审核台 设计文档

- 日期：2026-08-18
- 状态：由总体架构设计推导；实施前经用户确认
- 关联：总体设计 §2.1（Web 审核台）、§3.1（publish 表）、§9（部署形态）
- 前置：S1~S3 已合并至 master（采集→摘要→适配→自动审核，`review.verdict=pending` 待人工）

## 1. 目标与范围

FastAPI + Jinja2 服务端渲染的审核台（少量原生 JS 做复制），承接 S3 产出的"待人工审核文案"：

- **待审列表**（`/`）：展示 verdict=pending 的文案（平台、标题、摘要、文案、风格分），按时间倒序。
- **详情预览**（`/copy/{id}`）：文章摘要 + 三平台文案 + 评分明细 + 一键复制按钮。
- **标记发布**（`POST /copy/{id}/publish`）：写 `publish` 记录（published + published_at），review → pass，离开待审列表。
- **驳回**（`POST /copy/{id}/reject`）：review → reject + 备注。
- **运行状态页**（`/status`）：各状态 article/copy 数量、event_log 待处理/死信数、Redis 队列长度、最近事件。

## 2. 数据模型（新增，Alembic 迁移）

| 表 | 字段 |
| --- | --- |
| `publish` | id、copy_id FK unique、status(pending/published/skipped)、published_at、created_at |

## 3. 页面与路由

| 路由 | 方法 | 行为 |
| --- | --- | --- |
| `/` | GET | 待审列表 |
| `/copy/{id}` | GET | 详情预览（复制按钮走客户端 clipboard） |
| `/copy/{id}/publish` | POST | 标记已发布 |
| `/copy/{id}/reject` | POST | 驳回（可带 comment 表单字段） |
| `/status` | GET | 运行状态 |

模板：`app/web/templates/{base,list,detail,status}.html`；静态资源内联在模板中（无构建步骤）。

## 4. 模块与接口

| 文件 | 职责 |
| --- | --- |
| `app/web/__init__.py` | 包标记 |
| `app/web/main.py` | `create_app(session_factory, redis) -> FastAPI`；全部路由 |
| `app/storage/models.py` | 追加 `Publish` / `PublishStatus` |
| `app/web/templates/*.html` | 页面模板 |
| `tests/test_web.py` | TestClient 端到端（列表/详情/发布/驳回/状态） |

应用工厂模式：`create_app` 持有 `session_factory` 与 `redis` 于 `app.state`，路由内用同步 session（FastAPI 自动丢线程池），便于测试注入 fakeredis + SQLite。

## 5. 运行方式

```bash
python -m uvicorn app.web.main:app --host 127.0.0.1 --port 8000
```
（生产用 `create_app` 工厂 + `docker-compose` 的 app 服务，S1 compose 预留）

## 6. 测试

TestClient + fakeredis + SQLite：待审列表只含 pending；发布后离开列表且 publish 记录 published；驳回写 reject+comment；状态页展示计数；未知 copy 返回 404。

## 7. 范围外（不属于 S4）

- 登录/权限/多用户。
- 配图素材与富文本编辑。
- 全自动 API 发布（保持人工辅助发布形态）。
- 通知推送（S5 可评估）。
