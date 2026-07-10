from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    event,
    inspect,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from media_pilot.accounts.errors import ProtectedAdminError, UserDeletionForbiddenError
from media_pilot.repository.database import Base


def new_id() -> str:
    return str(uuid4())


def utc_now() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('admin', 'user')", name="ck_users_role"),
        CheckConstraint(
            "role != 'admin' OR can_access_adult = true",
            name="ck_admin_adult_access",
        ),
        CheckConstraint(
            "role != 'admin' OR is_enabled = true",
            name="ck_admin_enabled",
        ),
        Index(
            "uq_users_single_admin",
            "role",
            unique=True,
            sqlite_where=text("role = 'admin'"),
            postgresql_where=text("role = 'admin'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(128), nullable=False)
    normalized_username: Mapped[str] = mapped_column(
        String(128), nullable=False, unique=True
    )
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    can_access_adult: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class AccountSession(Base):
    __tablename__ = "account_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    last_active_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


@event.listens_for(User, "before_update")
def _protect_initial_admin_role(_mapper, _connection, user: User) -> None:
    role_history = inspect(user).attrs.role.history
    if "admin" in role_history.deleted and user.role != "admin":
        raise ProtectedAdminError("initial admin cannot be demoted")


@event.listens_for(User, "before_delete")
def _forbid_user_deletion(_mapper, _connection, _user: User) -> None:
    raise UserDeletionForbiddenError("users cannot be physically deleted")


class IngestTask(Base):
    __tablename__ = "ingest_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_user_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True
    )
    is_adult: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    source_path: Mapped[str] = mapped_column(String(4096), nullable=False)
    source_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    source_modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    discovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    media_type: Mapped[str | None] = mapped_column(String(32))
    # 直接持久化标题/年份，避免列表页纯依赖历史 ConfirmationRequest 的 ai_candidate
    title: Mapped[str | None] = mapped_column(String(512))
    year: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="unknown"
    )
    confidence: Mapped[float | None] = mapped_column(Float)
    current_step: Mapped[str | None] = mapped_column(String(128))
    failure_reason: Mapped[str | None] = mapped_column(String(2048))
    source_download_task_id: Mapped[str | None] = mapped_column(
        ForeignKey("download_tasks.id")
    )
    # 来自 DownloadTask 的元数据预选事实. 一旦存在, Agent 链路必须
    # 把它视为强事实: 不得走 search / 向用户确认, 直接消费.
    # preselected_metadata_provider 必须是 "tmdb" / "tpdb" 等 provider 名字,
    # preselected_metadata_external_id 是 provider_id (TMDB 走 "movie:<id>"
    # / "show:<id>" 形式). profile 仅用于 show 识别 ("tmdb_show" 等).
    preselected_metadata_profile: Mapped[str | None] = mapped_column(String(64))
    preselected_metadata_provider: Mapped[str | None] = mapped_column(String(64))
    preselected_metadata_external_id: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )


class DownloadTask(Base):
    __tablename__ = "download_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_user_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True
    )
    is_adult: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    indexer: Mapped[str | None] = mapped_column(String(128))
    qb_hash: Mapped[str | None] = mapped_column(String(64))
    qb_name: Mapped[str | None] = mapped_column(String(512))
    save_path: Mapped[str] = mapped_column(String(4096), nullable=False)
    content_path: Mapped[str | None] = mapped_column(String(4096))
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    download_speed_bytes_per_second: Mapped[int | None] = mapped_column(BigInteger)
    upload_speed_bytes_per_second: Mapped[int | None] = mapped_column(BigInteger)
    seeders: Mapped[int] = mapped_column(Integer, default=0)
    leechers: Mapped[int] = mapped_column(Integer, default=0)
    connections: Mapped[int | None] = mapped_column(Integer)
    qb_state: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="submitted")
    error_message: Mapped[str | None] = mapped_column(String(2048))
    # 元数据预选字段（本轮可不传，预留边界）
    preselected_metadata_profile: Mapped[str | None] = mapped_column(String(64))
    preselected_metadata_provider: Mapped[str | None] = mapped_column(String(64))
    preselected_metadata_external_id: Mapped[str | None] = mapped_column(String(256))
    # 关联入库任务
    ingest_task_id: Mapped[str | None] = mapped_column(ForeignKey("ingest_tasks.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MediaCandidate(Base):
    __tablename__ = "media_candidates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("ingest_tasks.id"), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    media_type: Mapped[str | None] = mapped_column(String(32))
    title: Mapped[str | None] = mapped_column(String(512))
    original_title: Mapped[str | None] = mapped_column(String(512))
    year: Mapped[int | None] = mapped_column(Integer)
    season: Mapped[int | None] = mapped_column(Integer)
    episode: Mapped[int | None] = mapped_column(Integer)
    language: Mapped[str | None] = mapped_column(String(128))
    version: Mapped[str | None] = mapped_column(String(256))
    external_id: Mapped[str | None] = mapped_column(String(256))
    confidence: Mapped[float | None] = mapped_column(Float)
    reason: Mapped[str | None] = mapped_column(String(2048))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class EpisodeMapping(Base):
    """剧集文件→季/集映射记录（show 路径专用）"""
    __tablename__ = "episode_mappings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("ingest_tasks.id"), nullable=False)
    file_path: Mapped[str] = mapped_column(String(4096), nullable=False)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    episode: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)  # "filename" | "parent_dir"
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class MediaSourceSelection(Base):
    __tablename__ = "media_source_selections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("ingest_tasks.id"), nullable=False)
    input_path: Mapped[str] = mapped_column(String(4096), nullable=False)
    selected_path: Mapped[str | None] = mapped_column(String(4096))
    confidence: Mapped[float | None] = mapped_column(Float)
    reason: Mapped[str | None] = mapped_column(String(2048))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SearchKeywordRecord(Base):
    __tablename__ = "search_keyword_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("ingest_tasks.id"), nullable=False)
    keyword: Mapped[str] = mapped_column(String(512), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    reason: Mapped[str | None] = mapped_column(String(2048))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class MetadataDetail(Base):
    __tablename__ = "metadata_details"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("ingest_tasks.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_id: Mapped[str] = mapped_column(String(256), nullable=False)
    media_type: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str | None] = mapped_column(String(512))
    original_title: Mapped[str | None] = mapped_column(String(512))
    year: Mapped[int | None] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class WritePlan(Base):
    __tablename__ = "write_plans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("ingest_tasks.id"), nullable=False)
    target_dir: Mapped[str] = mapped_column(String(4096), nullable=False)
    target_file: Mapped[str | None] = mapped_column(String(4096))
    nfo_path: Mapped[str | None] = mapped_column(String(4096))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class WriteResult(Base):
    __tablename__ = "write_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("ingest_tasks.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class FileAsset(Base):
    __tablename__ = "file_assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("ingest_tasks.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    path: Mapped[str] = mapped_column(String(4096), nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    checksum: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class OperationRecord(Base):
    __tablename__ = "operation_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("ingest_tasks.id"))
    file_asset_id: Mapped[str | None] = mapped_column(ForeignKey("file_assets.id"))
    operation_type: Mapped[str] = mapped_column(String(64), nullable=False)
    permission_level: Mapped[str] = mapped_column(String(64), nullable=False)
    source_path: Mapped[str | None] = mapped_column(String(4096))
    target_path: Mapped[str | None] = mapped_column(String(4096))
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("ingest_tasks.id"))
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    object_type: Mapped[str] = mapped_column(String(128), nullable=False)
    object_id: Mapped[str | None] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AppSetting(Base):
    __tablename__ = "app_settings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    enabled_metadata_profiles: Mapped[list] = mapped_column(JSON, default=list)
    enabled_library_formats: Mapped[list] = mapped_column(JSON, default=list)
    preferred_metadata_language: Mapped[str] = mapped_column(
        String(8), nullable=False, default="zh"
    )
    suspicious_file_threshold_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=314572800
    )
    metadata_auto_confirm_confidence: Mapped[float] = mapped_column(Float, default=0.9)
    metadata_auto_confirm_margin: Mapped[float] = mapped_column(Float, default=0.08)
    source_cleanup_policy: Mapped[str] = mapped_column(
        String(16), nullable=False, default="keep"
    )
    download_rate_limit_bytes_per_second: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    upload_rate_limit_bytes_per_second: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    synced_download_rate_limit_bytes_per_second: Mapped[int | None] = mapped_column(
        BigInteger
    )
    synced_upload_rate_limit_bytes_per_second: Mapped[int | None] = mapped_column(
        BigInteger
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AdapterCall(Base):
    __tablename__ = "adapter_calls"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("ingest_tasks.id"))
    adapter_name: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    request_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    response_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(String(2048))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("ingest_tasks.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="active")
    current_step: Mapped[str | None] = mapped_column(String(128))
    error_message: Mapped[str | None] = mapped_column(String(2048))
    run_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class AgentMessage(Base):
    __tablename__ = "agent_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str | None] = mapped_column(String)
    tool_calls: Mapped[dict | None] = mapped_column(JSON)
    tool_call_id: Mapped[str | None] = mapped_column(String(64))
    tool_name: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AgentToolCall(Base):
    __tablename__ = "agent_tool_calls"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), nullable=False)
    message_id: Mapped[str | None] = mapped_column(ForeignKey("agent_messages.id"))
    tool_call_id: Mapped[str | None] = mapped_column(String(128))
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False)
    input: Mapped[dict] = mapped_column(JSON, nullable=False)
    output: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="pending")
    error_message: Mapped[str | None] = mapped_column(String(2048))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class AgentDecisionRequest(Base):
    __tablename__ = "agent_decision_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), nullable=False)
    task_id: Mapped[str] = mapped_column(ForeignKey("ingest_tasks.id"), nullable=False)
    decision_type: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="pending")
    question: Mapped[str | None] = mapped_column(String)
    free_text_allowed: Mapped[bool] = mapped_column(default=False)
    options: Mapped[list] = mapped_column(JSON, default=list)
    # Stable per-decision context (e.g. target_conflict writes final_target_dir /
    # final_target_file / conflict reason so the user sees the exact path being
    # decided on, and the handler can refuse to overwrite a different path).
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    decision: Mapped[dict | None] = mapped_column(JSON)
    decided_by: Mapped[str | None] = mapped_column(String(128))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
