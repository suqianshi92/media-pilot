# SQLite 锁优化计划

## 背景

当前项目仍使用 SQLite 作为单机数据库。数据库层已经启用 WAL、
`busy_timeout` 和 `safe_commit`，但生产环境仍会在 Agent 运行期间遇到
`db_locked`。核心原因不是 SQLite 完全没有并发配置，而是部分 Agent
路径仍在一个 SQLAlchemy `Session` / 事务里跨越 LLM 调用、工具调用和
文件发布，导致写事务时间过长。

## 本轮目标

先做低风险止血：后台 Worker 自动入库不再同步跑完整 Agent loop，而是
复用现有 ack-only 机制。

具体边界：

- 解决：Worker 启动 Agent 时的长事务问题。
- 不解决：Agent 工具内部的细粒度事务拆分。
- 不解决：SQLite 替换为 PostgreSQL。
- 不解决：文件发布、源文件清理等副作用操作的重试语义。

## 阶段 1：Worker 自动入库改为 ack-only

Worker 对 `discovered` / `created` / `queued` 任务只做以下短事务：

1. 创建 `AgentRun`。
2. 写入初始用户消息。
3. 把任务切到 `agent_running`。
4. 提交事务并返回。

真正的 Agent loop 在后台线程里继续执行。这个模式与已有的失败重试入口
保持一致，可以避免后台扫描线程用一个事务长期占住数据库。

预期行为变化：

- `BackgroundProcessor.run_once()` 启动任务后通常返回 `agent_started`，
  不再等待同一轮内完成。
- UI 通过任务状态和 Agent 状态轮询看到后续进展。
- 如果同步 ack 阶段遇到 DB 锁，仍返回 `db_locked`，下一轮自然重试。

## 后续阶段

阶段 2 先处理 Agent loop 里最明显的长事务点：

- LLM 调用前提交已产生的 run/message/task 进度，让网络等待不持有
  SQLite 写事务。
- 已是 `active` 的 run 不再每轮写 `step_N`。这个诊断字段不值得为每次
  LLM 调用制造一次 `UPDATE` 和 `flush`。
- 从 `waiting_user` 恢复时仍会切回 `active`，并在 LLM 调用前提交。

本阶段仍不拆复杂写工具内部事务。发布文件、写 FileAsset、WriteResult、
源文件清理这些操作涉及文件系统副作用，后续需要单独按工具审查，不能为了
减少锁盲目拆开。

阶段 3 再考虑 operation-level retry：

- 只对短小、无文件副作用的写操作重跑完整 operation closure。
- 不对文件发布、删除、移动源文件做盲重试。
