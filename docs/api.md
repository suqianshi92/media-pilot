# JSON API

后端提供 `/api/v1` JSON API，用于前端 SPA 对接。所有响应使用统一 envelope：

```json
{
  "status": "success",
  "data": {},
  "messages": [{"level": "info", "code": "task_loaded", "text": "任务已加载"}],
  "meta": {"page": 1, "page_size": 50, "total": 120}
}
```

## 主要端点

- `GET /api/v1/flows` — 统一流程列表，聚合下载任务与入库任务，支持状态筛选和分页
- `GET /api/v1/tasks` — 入库任务列表
- `GET /api/v1/tasks/{id}` — 入库任务详情
- `GET /api/v1/tasks/{id}/agent-decisions` — 待回复的 Agent 决策列表
- `POST /api/v1/agent-decisions/{decision_id}/reply` — 回复 Agent 决策
- `GET /api/v1/tasks/{id}/agent-messages` — Agent 对话历史
- `GET /api/v1/tasks/{id}/agent-tool-calls` — Agent 工具调用历史
- `POST /api/v1/tasks/{id}/agent-runs` — 启动或重试 Agent run
- `POST /api/v1/tasks/{id}/agent-runs/recover-stuck` — 卡住恢复入口
- `GET /api/v1/downloads` — 下载任务列表
- `POST /api/v1/tasks/{id}/research` — 关键词重搜
- `POST /api/v1/tasks/{id}/revoke-publish` — 撤回已发布
- `POST /api/v1/tasks/{id}/delete-input` — 删除未发布任务的源文件

## Agent 决策

Agent 在以下节点创建 `AgentDecisionRequest`：

- 多候选无法自动确认
- 目标路径冲突
- 复杂输入目录需要选主视频 / 选字幕
- 元数据源无法解析 / 解析错误

任务工作台支持查看决策卡、编辑回复、查看 Agent 工具调用历史。任务长期停在 `agent_running` 且无决策提示时，任务详情页提供卡住恢复入口。
