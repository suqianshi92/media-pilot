# 手动检索元数据与成人影片搜索收口迭代计划

## 背景

生产环境真实导入暴露了两类问题：

- 成人影片文件名形如 `ANAV-001`、`STCV-593`、`WAAA-245` 时，Agent 容易先把前缀当普通电影标题搜索 TMDB，连续无候选后进入 `agent_failed`。
- 任务进入失败、等待确认或已入库后，用户缺少一个稳定的任务内入口来手动检索元数据、选择候选并继续处理。

本迭代目标是修复这些任务工作台级别的纠错能力，同时保持 Agent-first 方向：代码负责安全边界与确定性副作用，语义判断仍优先由 Agent 基于工具事实推进。

## 目标

- 去掉仓库 Docker Compose 示例中 downloads / watch 的只读挂载，避免默认配置与源文件清理策略冲突。
- 调整 Agent 提示词，让明显成人番号资源优先尝试 TPDB，不再反复把番号前缀当 TMDB 片名搜索。
- 调整搜索失败语义，让 `search_metadata` 的 `no_candidates` 成为可恢复业务结果，不轻易触发 `MAX_TOOL_FAILURES`。
- 在任务工作台新增“手动检索元数据”入口。
- 用户手动选择元数据后，走确定性后端链路继续处理，不再让 LLM 猜下一步。
- 支持在 `agent_failed`、`waiting_user`、`library_import_complete` 等状态下手动纠正元数据。

## 非目标

- 不实现 BDMV/ISO 入库。
- 不重构源文件清理状态机；`source_cleanup_policy=trash` 与只读挂载的历史问题另行处理。
- 不新增强制成人番号识别 helper 来决定媒体类型或 provider。
- 不实现跨 provider 搜索策略框架，例如 `search_failed_on_provider -> try alternative provider / ask user`。
- 不允许 `agent_running` 状态下并发手动检索元数据。

## 状态分流

用户在任务工作台通过“手动检索元数据”选择候选后，后端按任务状态分流。

| 当前状态 | 行为 |
| --- | --- |
| `agent_failed` | 清除失败态，保存新候选与元数据详情，然后尝试确定性发布。 |
| `waiting_user / select_metadata_candidate` | 将旧候选决策视为被人工覆盖，保存新选择并继续确定性发布。 |
| `waiting_user / target_conflict` | 作废旧目标冲突决策，按新元数据重新计算发布目标；如仍冲突，再创建新的目标冲突决策。 |
| `waiting_user / review_complex_input` | 允许保存新元数据，但不能绕过复杂输入或 BDMV 门禁。 |
| `library_import_complete` | 先执行撤回入库，删除旧发布产物，再用新元数据重新入库。 |
| `agent_running` | 暂不允许，返回明确错误，提示等待 Agent 完成或使用卡住恢复。 |
| `deleted` | 不允许。 |

## 后端设计

- 复用 `/tasks/{task_id}/research` 作为候选检索接口。
- 强化 `/tasks/{task_id}/manual-select` 或其内部服务，形成统一的“应用人工元数据选择”链路：
  - 校验当前任务状态。
  - 清理或降权旧错误候选、旧 metadata detail 与旧 pending metadata decision。
  - 持久化用户新选择。
  - 拉取并保存 metadata detail。
  - 未发布任务直接尝试发布。
  - 已发布任务先撤回入库，再按新元数据发布。
  - 遇到目标冲突时创建 `target_conflict` 决策。
  - 遇到复杂输入门禁时只保存元数据事实，不绕过门禁。
- `search_metadata` 的 `no_candidates` 不应作为硬工具失败累计；provider 错误、异常、schema 错误仍可算硬失败。

## 前端设计

- 在任务工作台左侧元数据区域附近新增 `ManualMetadataResearchSection`。
- 区块标题使用“手动检索元数据”。
- 表单包含：
  - 关键词输入。
  - 搜索范围：全部、TMDB 电影、TMDB 剧集、TPDB 成人影片。
  - 搜索按钮。
- 候选展示复用现有元数据候选卡，不展示原始 JSON。
- 选择候选后：
  - 调用 `manualSelect`。
  - refetch 当前任务详情、Agent messages、decisions、tool calls。
  - invalidate `flows`。
  - 根据返回状态显示 i18n toast 或 inline message。
- `agent_running` 时禁用选择并展示原因。

## Prompt 调整

- 明确文件名包含成人番号模式时，应优先尝试 TPDB movie 搜索。
- 明确不要把 `ANAV`、`STCV`、`WAAA` 这类番号前缀当普通 TMDB 片名。
- 明确同一 provider + 同一关键词连续无候选时，应换 provider、换关键词或请求用户决策，而不是重复调用。

## 验证计划

后端相关测试：

- `search_metadata` no candidates 不触发 hard tool failure 累计。
- `agent_failed` 任务手动选择 TPDB 候选后可继续发布。
- `waiting_user / select_metadata_candidate` 可被手动检索覆盖。
- `library_import_complete` 手动选新元数据时先撤回旧发布再重新发布。
- `agent_running` 手动选择返回 409 或等价业务错误。

前端相关测试：

- 任务详情显示“手动检索元数据”区块。
- 搜索候选、选择候选、成功后刷新任务详情与 Agent 面板数据。
- `agent_running` 状态下区块禁用并展示说明。
- 候选过多时不撑破页面。

配置验证：

- `docker-compose.yml` 中 `/data/downloads` 与 `/data/watch` 不再默认只读。

## 子任务拆分建议

- 子 Agent 可处理前端 `ManualMetadataResearchSection` UI 与 i18n、测试补充。
- 主线程保留后端状态分流、撤回入库再发布、runner/search failure 语义等核心逻辑。
