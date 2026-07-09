# BDMV 电影入库现状

本文记录当前代码对 BDMV 的真实支持范围。历史实现计划已完成，不再作为待办清单维护。

## 当前支持

- 支持已解包的 BDMV 电影目录作为电影入库输入。
- 支持两种输入形态：
  - `Movie Folder/BDMV/...`，可带同级 `CERTIFICATE/`。
  - 任务输入节点本身就是 `BDMV/...`。
- BDMV 判定条件是目录中存在 `index.bdmv` 或 `STREAM/`。
- Agent 主线遇到 BDMV 电影目录时不应仅因 `source_kind=bdmv` 暂停或请求人工选择主视频。
- 电影发布会把 BDMV 当作 opaque movie source：
  - 复制完整 `BDMV/` 到电影目标目录。
  - 同级 `CERTIFICATE/` 存在时一起复制。
  - 生成 `BDMV/index.nfo`。
  - poster / fanart / clearlogo 写在电影根目录。
- 无元数据入库也支持 BDMV 电影目录，只发布已确认的目录内容，不生成 NFO 或图片。
- 目标冲突按最终电影目录粒度判断；覆盖时替换整个目标电影目录。

示例输出：

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
  电影名 (年份)-poster.jpg
  电影名 (年份)-fanart.jpg
  电影名 (年份)-clearlogo.png
```

## 明确不支持

- 不支持 `.iso` / `.img` 镜像输入；这类输入直接视为不支持类型。
- 不解析 Blu-ray playlist。
- 不判断或抽取主 `.m2ts`。
- 不改名或移动 `BDMV/STREAM/*.m2ts`。
- 不支持剧集 BDMV。
- 不支持外挂字幕随 BDMV 发布。
- 不为 BDMV 增加单独设置项。

## 相关实现边界

- `src/media_pilot/services/disc_input.py` 负责识别已解包 BDMV 目录和 ISO/IMG。
- `src/media_pilot/services/complex_input_decision.py` 对 BDMV 返回 `ready`，对 ISO/IMG 返回 `iso_image_not_supported`。
- `src/media_pilot/services/video_source_resolver.py` 把 BDMV 解析为 `source_kind="bdmv"`，避免把 `STREAM/*.m2ts` 当作多视频候选。
- `src/media_pilot/orchestration/jellyfin_movie_writer.py` 负责 BDMV 元数据入库的目录复制、NFO 路径和冲突判断。
- `src/media_pilot/services/no_metadata_publish.py` 负责无元数据 BDMV 入库。
- `src/media_pilot/api/task_mapper.py` 兼容旧 `bdmv_detected` payload 和当前 `source_kind="bdmv"` payload，前端文件格式展示为 `BDMV`。

## 验证风险

- 仓库测试使用小型假 BDMV 目录验证结构复制、NFO 写入、冲突和覆盖语义。
- 真实 BDMV 目录体积较大，真实 Jellyfin 播放效果仍需要在生产媒体库中手工验证。
- 本项目只保证输出目录结构与现有样本对齐，不承诺 Jellyfin 对所有 BDMV 变体都能稳定播放。
