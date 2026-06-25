"""API v1 任务相关 DTO — 响应体与请求体

对应前端 types/task.ts 中的类型定义，字段统一使用 snake_case。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

# ---- 共享类型别名 ----

TaskStatus = Literal[
    "discovered",
    "waiting_stable",
    "created",
    "workspace_imported",
    "ai_parsed",
    "candidates_ready",
    "queued",
    "processing",
    "agent_running",
    "waiting_user",
    "agent_failed",
    "library_import_complete",
    "completed",
    "failed",
    "deleted",
]

class TaskStep(StrEnum):
    """入库任务当前步骤的稳定子集.

    IngestTask.current_step 在 DB 层是 String(128), 业务侧可能写入
    动态 / 临时 / 内部 marker (如 runner 的 `step_N` / 自由对话的
    `previous_step` / 复杂输入决策的 decision_type / show.py 的
    block_reason), 不便收敛到枚举. 这里的枚举仅声明业务可读的稳定
    步骤, 用于前端类型与 i18n 翻译兜底; DTO 字段 `current_step`
    仍然是自由字符串, 避免新加步骤导致 /api/v1/tasks 500.
    新增稳定步骤时, 必须:
      1) 在此枚举里加成员;
      2) 在前端 TaskStep union 与 i18n taskLabel.step 里加映射;
      3) 必要时在 task-labels fallback map 里加中文兜底.
    """

    # 下载 / 入站
    DOWNLOAD_SCAN = "download_scan"
    WORKSPACE_IMPORT = "workspace_import"
    WORKSPACE_IMPORTED = "workspace_imported"

    # 媒体源选择 / 关键词
    MEDIA_SOURCE_SELECTION = "media_source_selection"
    RAW_METADATA_SEARCH = "raw_metadata_search"
    LLM_KEYWORD_CLEANUP = "llm_keyword_cleanup"
    SELECT_MEDIA_SOURCE = "select_media_source"
    GENERATE_SEARCH_KEYWORD = "generate_search_keyword"
    SEARCH_METADATA = "search_metadata"
    AI_PARSE = "ai_parse"
    METADATA_DETAIL = "metadata_detail"

    # 写入 / 发布
    JELLYFIN_MOVIE_WRITER = "jellyfin_movie_writer"
    WRITE_METADATA_ASSETS = "write_metadata_assets"
    COPY_TO_STAGING = "copy_to_staging"
    PUBLISH_TO_LIBRARY = "publish_to_library"
    MOVE_TO_LIBRARY = "move_to_library"
    LIBRARY_IMPORT_COMPLETE = "library_import_complete"
    DELETE_TASK_INPUT = "delete_task_input"

    # Agent 阶段
    AGENT_START = "agent_start"
    AGENT_RUNNING = "agent_running"
    USER_REPLIED = "user_replied"
    COMPLETED = "completed"

    # 失败 / 异常步骤
    AGENT_FAILED = "agent_failed"
    MAX_TOOL_FAILURES = "max_tool_failures"
    MAX_STEPS_EXCEEDED = "max_steps_exceeded"
    LLM_ERROR = "llm_error"
    CONFIG_ERROR = "config_error"
    AGENT_INTERRUPTED = "agent_interrupted"

    # 人工决策步骤
    SELECT_METADATA_CANDIDATE = "select_metadata_candidate"
    TARGET_CONFLICT = "target_conflict"
    TARGET_CONFLICT_DECIDED = "target_conflict_decided"
    MANUAL_SELECT = "manual_select"
    MANUAL_SELECTION_BLOCKED = "manual_selection_blocked"

    # 撤回发布步骤
    POST_REVOKE_DECISION = "post_revoke_decision"
    POST_REVOKE_DECIDED = "post_revoke_decided"
    POST_REVOKE_REINGEST = "post_revoke_reingest"

    # 源文件清理步骤
    SOURCE_CLEANUP_DECISION = "source_cleanup_decision"
    SOURCE_CLEANUP_DECIDED = "source_cleanup_decided"
    SOURCE_CLEANUP_KEPT = "source_cleanup_kept"
    SOURCE_CLEANUP_TRASHED = "source_cleanup_trashed"
    SOURCE_CLEANUP_TRASH_REFUSED = "source_cleanup_trash_refused"
    SOURCE_CLEANUP_TRASH_FAILED = "source_cleanup_trash_failed"

MediaType = Literal["movie", "show", "unknown"]

ConfidenceLevel = Literal["high", "medium", "low", "unknown"]

TimelineTone = Literal["default", "success", "warning", "error"]

ResearchScope = Literal["all", "tmdb_movie", "tmdb_show", "tpdb_adult_movie"]
ProfileSearchStatus = Literal["succeeded", "failed", "skipped"]




# ---- 下载任务摘要 ----


class DownloadTaskSummary(BaseModel):
    """下载任务摘要，供前端任务列表和详情展示"""
    id: str
    title: str
    source: str
    qb_hash: str | None = None
    save_path: str
    content_path: str | None = None
    progress: float = 0.0
    download_speed_bytes_per_second: int | None = None
    upload_speed_bytes_per_second: int | None = None
    seeders: int = 0
    leechers: int = 0
    connections: int | None = None
    qb_state: str | None = None
    status: str  # submitted/downloading/completed/failed/sync_failed/awaiting_sync/paused
    error_message: str | None = None
    ingest_task_id: str | None = None
    created_at: datetime
    updated_at: datetime


class DownloadDetailDto(BaseModel):
    """下载流程详情 — 供下载详情页使用"""
    id: str
    title: str
    source: str
    qb_hash: str | None = None
    save_path: str
    content_path: str | None = None
    progress: float = 0.0
    download_speed_bytes_per_second: int | None = None
    upload_speed_bytes_per_second: int | None = None
    seeders: int = 0
    leechers: int = 0
    connections: int | None = None
    qb_state: str | None = None
    status: str
    error_message: str | None = None
    ingest_task_id: str | None = None
    preselected_metadata_profile: str | None = None
    preselected_metadata_provider: str | None = None
    preselected_metadata_external_id: str | None = None
    created_at: datetime
    updated_at: datetime


# ---- 任务摘要（列表接口） ----

class TaskStatusSummary(BaseModel):

    """任务当前状态摘要"""
    status: TaskStatus
    # current_step 在 DB 是 String(128), 业务侧可能写入动态 / 临时 marker
    # (如 runner 的 `step_N` / 自由对话的 previous_step / 复杂输入决策的
    # decision_type / show 阻塞的 block_reason), 不便收敛到枚举. 这里
    # 用 str 接收, 稳定子集见 TaskStep 枚举; 若改回 enum, 任意新 marker
    # 都会让 /api/v1/tasks 500.
    current_step: str | None = None
    failure_reason: str | None = None
    confidence: float | None = None
    confidence_level: ConfidenceLevel = "unknown"
    latest_message: str | None = None


class FlowStatusSummary(BaseModel):
    """媒体获取流程 status 摘要.

    与 TaskStatusSummary 类似, 但 status 接受 ingest status + download
    status 的并集 (download 状态如 `downloading` / `paused` / `submitted`
    / `awaiting_sync` / `sync_failed` 不在 TaskStatus Literal 内),
    用 str 接收.
    """
    status: str
    current_step: str | None = None
    failure_reason: str | None = None
    confidence: float | None = None
    confidence_level: ConfidenceLevel = "unknown"
    latest_message: str | None = None


class AgentStatusSummary(BaseModel):
    """Agent 状态摘要，用于任务列表 Agent 列展示"""
    run_status: Literal["none", "active", "waiting_user", "completed", "failed"]
    latest_run_id: str | None = None
    pending_decision_count: int = 0
    latest_message_summary: str | None = None


class TaskSummary(BaseModel):
    """任务列表项，对应前端 TaskSummary

    8.1: 新增 flow_type 和 total_status 以支持统一流程卡片。
    - flow_type: "managed_download"（系统内下载）/ "external_import"（外部导入）
    - total_status: 流程总状态，按阶段推进（下载中/等待转入入库/入库阶段状态）
    """
    id: str
    source_path: str
    title: str | None = None
    year: int | None = None
    media_type: MediaType | None = None
    can_confirm: bool = False
    flow_type: Literal["managed_download", "external_import"] = "external_import"
    total_status: str = "discovered"  # 8.3: 下载中 → 等待转入入库 → 入库阶段状态
    file_format: str | None = None  # MKV / MP4 / BDMV / ISO / 目录 / 未知
    created_at: datetime
    updated_at: datetime
    status_summary: TaskStatusSummary
    download_task: DownloadTaskSummary | None = None
    agent_status_summary: AgentStatusSummary | None = None


# ---- 媒体获取流程摘要 (list read-model) ----


FlowType = Literal["managed_download", "external_import", "download_only"]
RouteTarget = Literal["task_detail", "download_detail"]


class FlowSummary(BaseModel):
    """媒体获取流程列表 read-model.

    聚合:

    - `managed_download` ingest + linked download
    - `external_import` watch/import ingest (无 download)
    - `download_only` download-only download (无 ingest)

    `id` 使用前缀稳定 ID (`ingest:<id>` / `download:<id>`),
    避免 IngestTask.id 与 DownloadTask.id 碰撞. 同时返回
    `ingest_task_id` / `download_task_id` 与 `route_target`,
    前端无需解析 `id`.

    字段集与 TaskSummary 平行, 列表页可以直接消费.
    """
    id: str
    flow_type: FlowType
    route_target: RouteTarget
    ingest_task_id: str | None = None
    download_task_id: str | None = None
    total_status: str = "discovered"
    title: str | None = None
    year: int | None = None
    media_type: MediaType | None = None
    can_confirm: bool = False
    file_format: str | None = None
    source_path: str | None = None
    created_at: datetime
    updated_at: datetime
    status_summary: FlowStatusSummary | None = None
    agent_status_summary: AgentStatusSummary | None = None
    download_task: DownloadTaskSummary | None = None


# ---- 媒体源选择 ----

class MediaSourceCandidateFile(BaseModel):
    """媒体源候选/排除文件"""
    path: str
    name: str
    size_bytes: int | None = None
    reason: str


class MediaSourceSelectionDto(BaseModel):
    """媒体源选择信息"""
    input_path: str
    selected_path: str | None = None
    confidence: float | None = None
    reason: str | None = None
    bdmv_detected: bool = False
    stream_file_count: int | None = None
    candidate_files: list[MediaSourceCandidateFile] = Field(default_factory=list)
    excluded_files: list[MediaSourceCandidateFile] = Field(default_factory=list)


# ---- 搜索关键词 ----

class SearchKeywordDto(BaseModel):
    """搜索关键词记录"""
    keyword: str
    source: Literal["rule", "llm", "manual"]
    confidence: float | None = None
    reason: str | None = None
    rule_keyword: str | None = None
    explanation: str | None = None
    quality_tokens: list[str] = Field(default_factory=list)
    tokens_removed: list[str] = Field(default_factory=list)


# ---- 元数据候选 ----

class MetadataCandidateDto(BaseModel):
    """元数据候选条目（原 TmdbCandidateDto）"""
    provider: str
    provider_id: str
    title: str
    original_title: str | None = None
    year: int | None = None
    media_type: MediaType
    overview: str | None = None
    poster_url: str | None = None
    confidence: float | None = None
    match_reason: str | None = None
    risk_flags: list[str] = Field(default_factory=list)
    payload: dict = Field(default_factory=dict)


# ---- AI 候选 ----

class AiCandidateInfo(BaseModel):
    """AI 解析候选信息"""
    media_type: MediaType
    title: str | None = None
    original_title: str | None = None
    year: int | None = None
    season: int | None = None
    episode: int | None = None
    confidence: float | None = None
    reason: str | None = None


# ---- 元数据详情 ----

class MetadataPersonDto(BaseModel):
    """演职员"""
    provider_id: str | None = None
    name: str
    role: str | None = None
    profile_url: str | None = None
    image_url: str | None = None


class MetadataDetailDto(BaseModel):
    """元数据详情"""
    provider: str
    provider_id: str
    media_type: MediaType
    title: str | None = None
    original_title: str | None = None
    year: int | None = None
    overview: str | None = None
    release_date: str | None = None
    runtime_minutes: int | None = None
    rating: float | None = None
    tmdb_id: str | None = None
    imdb_id: str | None = None
    genres: list[str] = Field(default_factory=list)
    countries: list[str] = Field(default_factory=list)
    studios: list[str] = Field(default_factory=list)
    directors: list[MetadataPersonDto] = Field(default_factory=list)
    actors: list[MetadataPersonDto] = Field(default_factory=list)
    poster_url: str | None = None
    fanart_url: str | None = None
    clearlogo_url: str | None = None


# ---- 写入计划与结果 ----

class WritePlanDto(BaseModel):
    """写入计划"""
    target_dir: str
    target_file: str | None = None
    nfo_path: str | None = None
    poster_path: str | None = None
    fanart_path: str | None = None
    clearlogo_path: str | None = None
    conflict_status: str | None = None
    conflict_reason: str | None = None


class WriteResultDto(BaseModel):
    """写入结果"""
    status: Literal["succeeded", "warning", "failed", "target_conflict"]
    failure_reason: str | None = None
    warnings: list[str] = Field(default_factory=list)
    written_paths: list[str] = Field(default_factory=list)


# ---- 文件产物 ----

class FileAssetDto(BaseModel):
    """文件产物"""
    role: str
    path: str
    size_bytes: int | None = None


# ---- Provider 调用记录 ----

class ProviderCallDto(BaseModel):
    """Provider 调用记录"""
    adapter_name: str
    action: str
    status: Literal["succeeded", "failed"]
    error_message: str | None = None
    created_at: datetime


# ---- 文件操作记录 ----

class OperationRecordDto(BaseModel):
    """文件操作记录"""
    operation_type: str
    permission_level: str
    source_path: str | None = None
    target_path: str | None = None
    status: str
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


# ---- 审计日志 ----

class AuditLogDto(BaseModel):
    """审计日志"""
    actor: str
    action: str
    object_type: str
    object_id: str | None = None
    created_at: datetime
    context: dict[str, Any] = Field(default_factory=dict)


# ---- 事件时间线 ----

class TimelineEventDto(BaseModel):
    """时间线事件"""
    key: str
    title: str
    detail: str | None = None
    created_at: datetime
    tone: TimelineTone = "default"


# ---- 任务详情 ----

class TaskDetailDto(BaseModel):
    """任务详情，对应前端 TaskDetailDto"""
    task: TaskSummary
    source_selection: MediaSourceSelectionDto | None = None
    search_keyword: SearchKeywordDto | None = None
    selected_candidate: MetadataCandidateDto | None = None
    metadata_detail: MetadataDetailDto | None = None
    write_plan: WritePlanDto | None = None
    write_result: WriteResultDto | None = None
    file_assets: list[FileAssetDto] = Field(default_factory=list)
    provider_calls: list[ProviderCallDto] = Field(default_factory=list)
    operation_records: list[OperationRecordDto] = Field(default_factory=list)
    audit_logs: list[AuditLogDto] = Field(default_factory=list)
    timeline: list[TimelineEventDto] = Field(default_factory=list)
    episode_mappings: list[EpisodeMappingDto] = Field(default_factory=list)
    # 剧集结构可读摘要 — 避免前端必须遍历 episode_mappings 列表拼装
    # episode_range / mapping_mode_label. None = 任务不是剧集 / 还没
    # 跑过 prepare_show_structure. spec: task-operator-workspace /
    # 工作台展示剧集绝对集数映射摘要.
    show_structure: "ShowStructureSummaryDto | None" = None


class ShowStructureSummaryDto(BaseModel):
    """剧集结构可读摘要 — 前端直接展示, 不读 raw JSON."""

    status: str  # auto_publishable / blocked / unknown
    season: int | None = None
    episode_range: str | None = None  # e.g. "S01E51-E66"
    episode_count: int = 0
    # "absolute_episode_numbering" / "standard_sxxexx" /
    # "unknown". 前端据此决定是否展示"绝对集数"提示文案.
    mapping_mode: str = "unknown"
    mapping_mode_label: str = "unknown"
    # 失败时的可读文案 key, 由前端 i18n 翻译 — 避免暴露 raw
    # block_reason / max_steps / agent_failed 等内部标识.
    block_reason: str | None = None
    block_reason_label: str | None = None
    block_reason_message: str | None = None  # 人话: 给最终用户看的说明
    detected_show_title: str | None = None


class EpisodeMappingDto(BaseModel):
    """剧集文件映射 DTO"""
    file_path: str
    season: int
    episode: int
    source: str


# Forward-reference resolution — TaskDetailDto.show_structure 引用了
# ShowStructureSummaryDto, Pydantic v2 需要显式 rebuild 才能解析字符串.
TaskDetailDto.model_rebuild()


# ---- 手动重搜摘要 ----

class ProfileSearchStatusDto(BaseModel):
    """单个 profile 的搜索状态"""
    profile: str
    label: str
    provider: str
    status: ProfileSearchStatus
    candidate_count: int = 0
    error_message: str | None = None


class SearchSummaryDto(BaseModel):
    """手动重搜摘要"""
    keyword: str
    scope: ResearchScope
    searched_profiles: list[ProfileSearchStatusDto] = Field(default_factory=list)
    total_candidates: int = 0
    kept_existing_candidates: bool = False


class ResearchResponseData(BaseModel):
    """/research 响应 data 字段"""
    candidates: list[MetadataCandidateDto] = Field(default_factory=list)
    search_summary: SearchSummaryDto
# ---- 请求体 DTO ----

class ResearchKeywordRequest(BaseModel):
    """重新搜索关键词请求"""
    keyword: str = Field(min_length=1, max_length=512)
    scope: ResearchScope = "all"


# ---- 撤销发布 ----

class RevokePublishCheckDto(BaseModel):
    """撤销发布预检结果 DTO"""
    allowed: bool
    publish_dir: str | None = None
    source_file_exists: bool = False
    is_complex_structure: bool = False
    outcome_description: str = ""


class RevokePublishResultDto(BaseModel):
    """撤销发布执行结果 DTO"""
    status: str  # "waiting_user" | "deleted"
    outcome: str
    decision_id: str | None = None


# ---- 人工辅助检索 ----

class ManualSelectRequest(BaseModel):
    """人工辅助检索选择请求"""
    provider: str
    provider_id: str
    title: str
    year: int | None = None
    original_title: str | None = None
    media_type: str = "movie"


class ManualSelectResponse(BaseModel):
    """人工辅助检索选择响应"""
    status: str  # "published" | "waiting_user" | "saved"
    summary: str
    candidate_id: str | None = None
    decision_id: str | None = None
    blocking_reasons: list[str] = []
