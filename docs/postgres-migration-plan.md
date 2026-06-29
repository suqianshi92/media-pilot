# PostgreSQL 迁移执行计划

## 目标

把生产数据库从 SQLite 迁移到 PostgreSQL，降低 Agent 并发写入时的锁冲突风险。

本分支保留 SQLite 支持作为开发和回退路径，但生产部署优先使用 PostgreSQL。

## 不做

- 不删除 SQLite 支持。
- 不删除旧 SQLite 文件。
- 不删除 watch、downloads、媒体库、回收区里的任何影片文件。
- 不引入 Alembic。当前阶段仍使用 SQLAlchemy metadata 初始化 schema。
- 不在本轮重构所有业务长事务；PostgreSQL 迁移先解决 SQLite 单 writer 限制。

## 影响模块

- 配置：增加 `MEDIA_PILOT_DATABASE_URL`，优先级高于 `MEDIA_PILOT_DATABASE_DIR`。
- 数据库入口：`src/media_pilot/repository/database.py` 支持 SQLite / PostgreSQL 两种 engine。
- 初始化：通用表创建走 `Base.metadata.create_all()`；SQLite 专用补列和 PRAGMA 只在 SQLite 执行。
- Docker：compose 增加 PostgreSQL 服务，应用通过服务名连接数据库。
- 迁移：新增一次性迁移脚本，从 SQLite 读取旧数据并写入 PostgreSQL。
- 文档：更新 README / deployment 文档中的数据库说明和回退方式。

## 生产数据保护

- 迁移前必须备份现有 SQLite 文件。
- 迁移脚本只读取 SQLite 文件，不修改 SQLite。
- 迁移脚本写入 PostgreSQL 前应检查目标库是否为空，避免重复导入。
- watch 源文件保留不动，让被清理的脏任务可以重新触发。
- 工作区清理只允许删除 `/data/workspace` 下与明确任务 ID 对应的缓存目录；不允许碰 watch/downloads/library。

## 脏数据策略

当前生产库存在 SQLite 锁导致的 `agent_running` / `active` 残留。迁移时不应把这些状态原样带到 PostgreSQL。

规则：

- `agent_runs.status='active'` 且关联任务仍是 `agent_running`：迁移时标记为 `failed` / `agent_failed`，错误信息写明 `stale active run during PostgreSQL migration`。
- 如果用户明确要求重新跑某些任务，可以在迁移后删除对应 `ingest_tasks`，由保留在 watch 中的源文件重新触发。
- `waiting_user` 决策保留，除非明确判断为旧脏数据。
- 已完成入库任务保留。

## 实施步骤

1. 配置与 engine 支持
   - `AppConfig` 增加 `database_url: str | None`。
   - 环境变量读取 `MEDIA_PILOT_DATABASE_URL`。
   - `create_engine_from_config()` 根据 URL scheme 选择 SQLite / PostgreSQL。
   - SQLite fallback 行为保持现状。

2. 初始化与 schema
   - `initialize_database()` 返回数据库标识字符串或路径兼容值。
   - SQLite 执行现有 `_ensure_column()`、PRAGMA 和 SQLite DDL。
   - PostgreSQL 跳过 SQLite PRAGMA / `PRAGMA table_info`。
   - PostgreSQL 创建 `idx_one_active_agent_run_per_task` partial unique index。

3. Docker compose
   - 增加 `media-pilot-postgres` 服务。
   - PostgreSQL 数据目录挂载到独立 host path。
   - `media-pilot` 注入 `MEDIA_PILOT_DATABASE_URL=postgresql+psycopg://...@media-pilot-postgres:5432/...`。
   - `media-pilot` depends_on PostgreSQL healthcheck。

4. 迁移脚本
   - 新增 `python -m media_pilot.deployment.migrate_sqlite_to_postgres`。
   - 参数：
     - `--sqlite-path`
     - `--database-url`
     - `--clean-stale-active-runs`
   - 保持表级顺序导入，避免外键依赖问题。
   - JSON 字段保持 Python dict/list 写入，不做字符串二次编码。

5. 生产迁移流程
   - 停止生产服务。
   - 备份 SQLite。
   - 启动 PostgreSQL。
   - 执行迁移脚本。
   - 启动应用。
   - 验证首页、任务列表、设置页、资源搜索、watch 自动入库。

6. 并发验证
   - 保留 watch 中的测试源文件。
   - 让多个 watch 任务同时进入 Agent。
   - 验证不再出现 `sqlite3.OperationalError: database is locked`。
   - 验证 Agent 状态不会残留 `active` / `agent_running`。

## 验证命令

开发侧：

```bash
.venv/bin/python -m pytest tests/test_database.py tests/test_runtime_app.py
```

迁移脚本相关测试：

```bash
.venv/bin/python -m pytest tests/test_postgres_migration.py
```

生产侧只读检查：

```bash
docker compose ps
docker logs --since 10m media-pilot
```

## 回退

- 保留旧 SQLite 文件和旧镜像。
- 如果 PostgreSQL 迁移失败，停止新 compose，切回旧分支/旧镜像和 SQLite 配置。
- 回退时不需要恢复 watch 源文件，因为迁移流程不会删除它们。
