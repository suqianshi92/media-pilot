# BDMV 电影入库实现计划

## 目标

支持已解包的 BDMV 电影目录进入现有 Agent 入库主线：完成元数据匹配后，把完整光盘目录发布到 Jellyfin 电影库，使 Jellyfin 能按现有生产样本识别播放。

生产样本对照：

```text
电影名 (年份)/
  BDMV/
    index.bdmv
    MovieObject.bdmv
    STREAM/
    PLAYLIST/
    CLIPINF/
    index.nfo
  CERTIFICATE/
  poster.jpg
  fanart.jpg
  clearlogo.png
```

## 不解决

- 不支持 `.iso` / `.img`。
- 不解析 Blu-ray playlist，不判断主 `.m2ts`。
- 不改名或移动 `BDMV/STREAM/*.m2ts`。
- 不支持剧集 BDMV。
- 不支持外挂字幕随 BDMV 发布。
- 不新增设置项。
- 不触碰生产数据；生产验证只在用户明确要求后执行。

## 关键设计

1. **输入类型拆分**
   - 现有代码把 BDMV 和 ISO 合并为 `bdmv_or_iso_not_supported`。
   - 改为区分：
     - `bdmv_movie_directory`：目录内存在 `BDMV/index.bdmv` 或 `BDMV/STREAM/`，允许电影路径继续。
     - `iso_image_not_supported`：`.iso` / `.img` 继续阻断。
   - `prepare_complex_input_decision` 对 BDMV 返回 ready，而不是 `review_complex_input`。

2. **发布计划扩展**
   - 现有 `MovieWritePlanDraft` 面向单视频文件。
   - 增加 `source_kind: "file" | "bdmv"`。
   - BDMV 发布目标是整个电影目录，不是单个视频文件：
     - staging: `<movies_dir>/.media-pilot-staging/<task_id>/<movie_dir>/`
     - final: `<movies_dir>/<movie_dir>/`
   - BDMV 的 NFO 路径为 `target_dir/BDMV/index.nfo`，对齐 TMM 样本。
   - poster/fanart/clearlogo 仍写到电影根目录。

3. **目录树写入**
   - 文件电影继续走现有 copy2 逻辑。
   - BDMV 电影使用安全的目录树复制：
     - 复制源输入节点里的 `BDMV/`。
     - 若存在同级 `CERTIFICATE/`、`MAKEMKV/` 等目录，首版可按 allowlist 复制 `CERTIFICATE/`，`MAKEMKV/` 是否复制需谨慎，默认不复制也能播放。
     - 不复制下载站广告文本等无关文件。
   - 复制完成后 staging 整体 move 到 final。

4. **冲突与覆盖**
   - BDMV 冲突以最终电影目录为单位：
     - `final_target_dir_exists` → 创建目标冲突决策。
   - 用户选择覆盖时，删除整个目标电影目录后重新发布。
   - 文件电影原有冲突语义保持不变。

5. **撤回与源文件清理**
   - 撤回入库依赖 `FileAsset` / `OperationRecord` 的发布产物记录。
   - BDMV 发布必须记录：
     - 目标电影目录创建 / 移动。
     - `BDMV/index.nfo`。
     - poster/fanart/clearlogo。
   - 源文件清理仍作用于任务输入节点，不需要为 BDMV 特殊放宽。

6. **Agent 语义**
   - `AUTO_INGEST_SYSTEM_PROMPT` 改为：BDMV 电影目录可发布，ISO 不支持。
   - `publish_movie_to_library` 描述改为支持 single-file movie 和 BDMV movie directory。
   - `get_auto_ingest_eligibility` 的 blocking reason 不再把 BDMV 与 ISO 合并。

## 影响模块

- `src/media_pilot/services/complex_input_decision.py`
- `src/media_pilot/services/auto_ingest.py`
- `src/media_pilot/services/video_source_resolver.py`
- `src/media_pilot/orchestration/jellyfin_movie_writer.py`
- `src/media_pilot/agent/tools/write.py`
- `src/media_pilot/agent/prompts.py`
- `src/media_pilot/services/publish_plan_draft.py`
- `src/media_pilot/services/target_conflict_handler.py`
- `src/media_pilot/orchestration/revoke_publish.py`，视现有 FileAsset 记录能力决定是否需要改
- `README.md` / `README.en.md` 的输出结构说明
- `CONTEXT.md` 增加“BDMV 电影目录”词汇和不变量

## 测试计划

优先补以下定向测试，不跑全量作为第一轮验收：

1. `test_complex_input_decision`
   - BDMV 目录返回 ready。
   - ISO 文件继续 unsupported。
   - BDMV 不进入 select_primary_video。

2. `test_auto_ingest_services`
   - BDMV 不产生 `bdmv_or_iso_not_supported`。
   - ISO 继续产生阻断 reason。

3. `test_jellyfin_movie_writer`
   - BDMV plan 生成 `BDMV/index.nfo`。
   - 执行发布后目标目录包含 `BDMV/index.bdmv`、`BDMV/STREAM/...`、`BDMV/index.nfo`、poster。
   - 不改名 `STREAM/*.m2ts`。
   - 覆盖发布会替换整个目标电影目录。

4. `test_target_conflict_handler`
   - BDMV 目标目录已存在时创建 target_conflict。
   - overwrite_target 对 BDMV 复用同一路径重新发布。

5. `test_worker` 或轻量 Agent runner 集成
   - mock LLM 工具链能从 BDMV 输入走到 `library_import_complete`。

## 实施顺序

1. 抽出 BDMV 输入识别 helper，先只改分析层和 eligibility。
2. 扩展 movie writer 的 plan/result，支持 `source_kind="bdmv"`。
3. 改 `publish_movie_to_library` 和目标冲突覆盖路径。
4. 补 Agent prompt / tool 描述 / draft plan 输出。
5. 更新 README、CONTEXT 和相关测试。

## 风险点

- 目录树复制比单文件复制更容易留下半截 staging；必须继续使用 staging → final move。
- 覆盖删除的是整个电影目录，必须保证目标路径在 movies/adult movies 库根内。
- BDMV 目录体积大，测试只能用小型假结构；真实播放能力以生产手工验证为准。
- Jellyfin 对 BDMV 本身支持不完善，本项目首版只保证目录结构对齐已有可播放样本。
