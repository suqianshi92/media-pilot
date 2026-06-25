"""Read-only agent tools -- inspect task state, files, and metadata."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from media_pilot.agent.tools.base import (
    PermissionLevel,
    ToolContext,
    ToolDefinition,
    ToolResult,
)
from media_pilot.repository.repositories import (
    IngestTaskRepository,
    MediaCandidateRepository,
    MetadataDetailRepository,
)


# ── 1. get_task_context ──────────────────────────────────────────────

_GET_TASK_CONTEXT_SCHEMA = {
    "type": "object",
    "properties": {
        "task_id": {"type": "string"},
    },
    "required": ["task_id"],
    "additionalProperties": False,
}


def _handle_get_task_context(context: ToolContext, input_data: dict) -> ToolResult:
    repo = IngestTaskRepository(context.session)
    task = repo.get(input_data["task_id"])
    if task is None:
        return ToolResult(
            status="failure",
            summary=f"Task {input_data['task_id']} not found",
        )
    return ToolResult(
        status="success",
        summary=f"Task {task.id}: status={task.status}, source={task.source_path}",
        data={
            "id": task.id,
            "source_path": task.source_path,
            "status": task.status,
            "current_step": task.current_step,
            "media_type": task.media_type,
            "title": task.title,
            "year": task.year,
            "confidence": task.confidence,
            "failure_reason": task.failure_reason,
            "source_size_bytes": task.source_size_bytes,
            "created_at": task.created_at.isoformat() if task.created_at else None,
            "updated_at": task.updated_at.isoformat() if task.updated_at else None,
        },
    )


def make_get_task_context() -> ToolDefinition:
    return ToolDefinition(
        name="get_task_context",
        description="Return structured task context including status, source path, media type, title, year, current step, and timestamps.",
        parameters=_GET_TASK_CONTEXT_SCHEMA,
        permission_level=PermissionLevel.READ_ONLY,
        handler=_handle_get_task_context,
    )


# ── 2. scan_task_files ───────────────────────────────────────────────

_SCAN_TASK_FILES_SCHEMA = {
    "type": "object",
    "properties": {
        "task_id": {"type": "string"},
    },
    "required": ["task_id"],
    "additionalProperties": False,
}


def _handle_scan_task_files(context: ToolContext, input_data: dict) -> ToolResult:
    from media_pilot.services.task_input_analysis import analyze_task_input

    repo = IngestTaskRepository(context.session)
    task = repo.get(input_data["task_id"])
    if task is None:
        return ToolResult(
            status="failure",
            summary=f"Task {input_data['task_id']} not found",
        )

    source_path = Path(task.source_path)
    if not source_path.exists():
        return ToolResult(
            status="failure",
            summary=f"Source path does not exist: {source_path}",
        )

    analysis = analyze_task_input(source_path)

    return ToolResult(
        status="success",
        summary=f"Scanned {len(analysis.files)} files ({len(analysis.excluded)} excluded) in {source_path.parent if source_path.is_file() else source_path}",
        data={
            "source_path": analysis.source_path,
            "is_directory": analysis.is_directory,
            "files": [
                {
                    "path": f.path,
                    "name": f.name,
                    "size_bytes": f.size_bytes,
                    "type": f.type,
                    **({"matched_by": f.matched_by} if f.matched_by else {}),
                }
                for f in analysis.files
            ],
            "excluded": [
                {
                    "path": e.path,
                    "name": e.name,
                    "size_bytes": e.size_bytes,
                    "type": e.type,
                    "excluded_reason": e.excluded_reason or "",
                }
                for e in analysis.excluded
            ],
            "video_count": analysis.video_count,
            "subtitle_count": analysis.subtitle_count,
            "total_size_bytes": analysis.total_size_bytes,
        },
    )


def make_scan_task_files() -> ToolDefinition:
    return ToolDefinition(
        name="scan_task_files",
        description="Scan the task's source directory and return a file listing with types (video/subtitle/other), sizes, filtering out sample/trailer files.",
        parameters=_SCAN_TASK_FILES_SCHEMA,
        permission_level=PermissionLevel.READ_ONLY,
        handler=_handle_scan_task_files,
    )


# ── 3. get_current_metadata ─────────────────────────────────────────

_GET_CURRENT_METADATA_SCHEMA = {
    "type": "object",
    "properties": {
        "task_id": {"type": "string"},
    },
    "required": ["task_id"],
    "additionalProperties": False,
}


def _handle_get_current_metadata(context: ToolContext, input_data: dict) -> ToolResult:
    repo = MetadataDetailRepository(context.session)
    detail = repo.get_for_task(input_data["task_id"])
    if detail is None:
        return ToolResult(
            status="success",
            summary="No metadata detail exists for this task",
            data={"exists": False},
        )
    return ToolResult(
        status="success",
        summary=f"Metadata: {detail.title} ({detail.year}) from {detail.provider}",
        data={
            "exists": True,
            "provider": detail.provider,
            "provider_id": detail.provider_id,
            "media_type": detail.media_type,
            "title": detail.title,
            "original_title": detail.original_title,
            "year": detail.year,
            "payload": detail.payload,
            "created_at": detail.created_at.isoformat() if detail.created_at else None,
        },
    )


def make_get_current_metadata() -> ToolDefinition:
    return ToolDefinition(
        name="get_current_metadata",
        description="Return the current MetadataDetail for the task if it exists.",
        parameters=_GET_CURRENT_METADATA_SCHEMA,
        permission_level=PermissionLevel.READ_ONLY,
        handler=_handle_get_current_metadata,
    )


# ── 4. search_metadata ──────────────────────────────────────────────

_SEARCH_METADATA_SCHEMA = {
    "type": "object",
    "properties": {
        "keyword": {"type": "string"},
        "provider": {"type": "string"},
        "media_type": {"type": "string", "enum": ["movie", "show", "both"]},
    },
    "required": ["keyword"],
    "additionalProperties": False,
}


def _handle_search_metadata(context: ToolContext, input_data: dict) -> ToolResult:
    from media_pilot.services.metadata_search import search_metadata

    keyword = input_data["keyword"]
    provider_name = input_data.get("provider", "tmdb")
    media_type = input_data.get("media_type", "both")
    language = list(context.config.tmdb_language_priority)

    result = search_metadata(
        config=context.config,
        provider_name=provider_name,
        keyword=keyword,
        language_priority=language,
        media_type=media_type,
    )

    # 特殊路径: provider+media_type 已知不兼容 (e.g. TPDB+show). 这条
    # 必须按 success-with-flag 返回, 不能算 hard tool failure — 计数
    # 会污染 auto_ingest 的 max_tool_failures 安全网, 让 LLM 在第一次
    # 误判就被踢出 run. 把这条 fact 直接给 LLM 让它改 provider / 限定
    # media_type. spec: agent-metadata-search-loop-guard / Requirement:
    # TPDB+show 结构化拒绝.
    if (
        provider_name == "tpdb"
        and media_type in ("show", "both")
    ):
        return ToolResult(
            status="success",
            summary=(
                f"Provider '{provider_name}' does not support media_type "
                f"'{media_type}'. Switch provider to 'tmdb' or restrict "
                f"media_type to 'movie'."
            ),
            data={
                "incompatible_provider": True,
                "provider": provider_name,
                "media_type": media_type,
                "reason": "provider_show_not_supported",
                "message": (
                    f"Provider '{provider_name}' does not support show "
                    "search. Use provider='tmdb' or restrict media_type."
                ),
                "keyword": keyword,
                "candidates": [],
            },
        )

    # No candidates at all → structured failure
    if not result.candidates:
        if result.errors:
            return ToolResult(
                status="failure",
                summary=f"No candidates found for '{keyword}' on {provider_name} ({len(result.errors)} query error(s))",
                data={
                    "reason": "provider_errors",
                    "errors": result.errors,
                    "keyword": keyword,
                    "provider": provider_name,
                },
            )
        return ToolResult(
            status="failure",
            summary=f"No candidates found for '{keyword}' on {provider_name}",
            data={
                "reason": "no_candidates",
                "keyword": keyword,
                "provider": provider_name,
            },
        )

    results = [
        {
            "provider": c.provider or provider_name,
            "provider_id": c.provider_id,
            "title": c.title,
            "original_title": c.original_title,
            "year": c.year,
            "media_type": c.media_type,
            "overview": c.overview,
            "confidence": c.confidence,
            "match_reason": c.match_reason,
        }
        for c in result.candidates
    ]

    # ── threshold evaluation on search results ────────────────────────
    from media_pilot.orchestration.auto_confirmation import (
        has_clear_winner,
        pick_best_candidate,
    )

    confidence_threshold = context.config.metadata_auto_confirm_confidence
    margin = context.config.metadata_auto_confirm_margin

    search_has_winner = has_clear_winner(
        list(result.candidates),
        confidence_threshold=confidence_threshold,
        margin=margin,
    )

    best_candidate = None
    runner_up = None
    if result.candidates:
        best, runner = pick_best_candidate(list(result.candidates))
        best_candidate = {
            "provider": best.provider or provider_name,
            "provider_id": best.provider_id,
            "title": best.title,
            "year": best.year,
            "confidence": best.confidence,
        }
        if runner is not None:
            runner_up = {
                "provider": runner.provider or provider_name,
                "provider_id": runner.provider_id,
                "title": runner.title,
                "year": runner.year,
                "confidence": runner.confidence,
            }

    data: dict = {
        "candidates": results,
        "keyword": keyword,
        "provider": provider_name,
        "confidence_threshold": confidence_threshold,
        "margin": margin,
        "has_clear_winner": search_has_winner,
        "best_candidate": best_candidate,
        "runner_up": runner_up,
    }
    if result.errors:
        data["errors"] = result.errors

    return ToolResult(
        status="success",
        summary=f"Found {len(results)} candidates for '{keyword}' on {provider_name}"
        + (f" ({len(result.errors)} query error(s))" if result.errors else ""),
        data=data,
    )


def make_search_metadata() -> ToolDefinition:
    return ToolDefinition(
        name="search_metadata",
        description="Search for metadata candidates by keyword. Returns matching candidates with confidence scores, plus has_clear_winner, confidence_threshold, margin, best_candidate, and runner_up for auto-confirm decisions. Supports optional provider (tmdb/tpdb) and media_type (movie/show/both, default both).",
        parameters=_SEARCH_METADATA_SCHEMA,
        permission_level=PermissionLevel.READ_ONLY,
        handler=_handle_search_metadata,
    )


# ── 5. get_metadata_candidates ──────────────────────────────────────

_GET_METADATA_CANDIDATES_SCHEMA = {
    "type": "object",
    "properties": {
        "task_id": {"type": "string"},
    },
    "required": ["task_id"],
    "additionalProperties": False,
}


def _handle_get_metadata_candidates(context: ToolContext, input_data: dict) -> ToolResult:
    from media_pilot.repository.repositories import IngestTaskRepository
    from media_pilot.services.select_metadata_candidate import (
        _build_preselected_fact,
    )

    task_id = input_data["task_id"]
    task = IngestTaskRepository(context.session).get(task_id)

    # READ_ONLY 工具边界: 本工具不写库, 不调 provider. 即便 task 携带
    # preselected_metadata_* 字段, 也不主动落库 source="preselected"
    # 候选 — 那条动作只在 DRAFT 路径 (e.g. prepare_select_metadata_
    # candidate_decision) 由 _resolve_preselected_winner 做. 无持久
    # 化候选时, 仅暴露 data.preselected (构造自 task 字段) 即可.
    repo = MediaCandidateRepository(context.session)
    candidates = repo.list_for_task(task_id)

    # 把候选映射为对外 payload — 真实 provider 与 candidate_source 解耦,
    # LLM 拿 provider 调 fetch_and_save_metadata_detail / publish_* 工具.
    def _provider_for_candidate(c) -> str | None:
        c_payload = c.payload if isinstance(c.payload, dict) else {}
        if c.source == "preselected":
            return c_payload.get("preselected_provider") or None
        return c.source or None

    if not candidates:
        data: dict = {"candidates": []}
        if (
            task is not None
            and task.preselected_metadata_provider
            and task.preselected_metadata_external_id
        ):
            preselected_fact = _build_preselected_fact(
                task=task, persisted_candidates=[],
            )
            if preselected_fact is not None:
                data["preselected"] = {
                    **preselected_fact,
                    "source": "task_preselected",
                    "reason": (
                        "Preselected from DownloadTask; no persisted candidate. "
                        "Call prepare_select_metadata_candidate_decision (DRAFT) "
                        "to materialize a source=preselected row before publish."
                    ),
                }
        return ToolResult(
            status="success",
            summary="No metadata candidates exist for this task",
            data=data,
        )
    results = [
        {
            "candidate_id": c.id,
            "provider": _provider_for_candidate(c),
            "provider_id": c.external_id,
            "media_type": c.media_type,
            "title": c.title,
            "original_title": c.original_title,
            "year": c.year,
            "season": c.season,
            "episode": c.episode,
            "external_id": c.external_id,
            "confidence": c.confidence,
            "candidate_source": c.source,
            "reason": c.reason,
        }
        for c in candidates
    ]
    data = {"candidates": results}
    # 同样把 preselected 块提升到顶层, 与 get_auto_ingest_eligibility
    # 行为一致, LLM 跨工具看到的事实是稳定的. 用 _build_preselected_
    # fact 纯计算, 不写库.
    if (
        task is not None
        and task.preselected_metadata_provider
        and task.preselected_metadata_external_id
    ):
        preselected_fact = _build_preselected_fact(
            task=task, persisted_candidates=candidates,
        )
        if preselected_fact is not None:
            data["preselected"] = {
                **preselected_fact,
                "source": "task_preselected",
                "reason": (
                    "Persisted source=preselected candidate matched on "
                    "(provider, external_id, media_type)."
                ),
            }
    return ToolResult(
        status="success",
        summary=f"Found {len(results)} metadata candidate(s)",
        data=data,
    )


def make_get_metadata_candidates() -> ToolDefinition:
    return ToolDefinition(
        name="get_metadata_candidates",
        description="Return all MediaCandidate records for the task.",
        parameters=_GET_METADATA_CANDIDATES_SCHEMA,
        permission_level=PermissionLevel.READ_ONLY,
        handler=_handle_get_metadata_candidates,
    )


# ── 6. get_auto_ingest_eligibility ───────────────────────────────────

_GET_AUTO_INGEST_ELIGIBILITY_SCHEMA = {
    "type": "object",
    "properties": {
        "task_id": {"type": "string"},
    },
    "required": ["task_id"],
    "additionalProperties": False,
}


def _handle_get_auto_ingest_eligibility(context: ToolContext, input_data: dict) -> ToolResult:
    from media_pilot.services.auto_ingest import check_eligibility

    result = check_eligibility(
        session=context.session,
        config=context.config,
        task_id=input_data["task_id"],
    )

    data: dict = {
        "eligible": result.eligible,
        "media_type": result.media_type,
        "video_count": result.video_count,
        "is_single_file": result.is_single_file,
        "is_sample_or_trailer": result.is_sample_or_trailer,
        "is_bdmv_or_iso": result.is_bdmv_or_iso,
        "is_complex_directory": result.is_complex_directory,
        "candidate_count": result.candidate_count,
        "has_clear_winner": result.has_clear_winner,
        "best_candidate": result.best_candidate,
        "runner_up": result.runner_up,
        "confidence_threshold": result.confidence_threshold,
        "margin": result.margin,
        "blocking_reasons": result.blocking_reasons,
        "warnings": result.warnings,
        "task_facts": result.task_facts,
    }
    # preselected 强事实旁路: 当 task 携带 preselected_metadata_* 字段
    # 且 check_eligibility 走旁路生成了 winner 时, 把该 winner 提升为
    # 顶层 preselected 字段, 让 LLM 在第一次调本工具时就能看到 "已有
    # 强 winner" 事实, 不必再调 search_metadata 浪费 step.
    #
    # check_eligibility 已经用 _build_preselected_fact 纯计算
    # task_facts["preselected"] (无 DB 写, 无 provider 调), 本工具
    # 读它再透传给 LLM. provider 字段是真 metadata provider
    # (e.g. "tmdb"), 不是 "preselected".
    preselected_facts = (
        result.task_facts.get("preselected")
        if isinstance(result.task_facts, dict)
        else None
    )
    if preselected_facts is not None:
        data["preselected"] = {
            "provider": preselected_facts.get("provider"),
            "provider_id": preselected_facts.get("provider_id"),
            "media_type": preselected_facts.get("media_type"),
            "confidence": preselected_facts.get("confidence", 1.0),
            "title": preselected_facts.get("title"),
            "year": preselected_facts.get("year"),
            "candidate_source": preselected_facts.get("candidate_source"),
            "source": "task_preselected",
            "reason": (
                "Preselected from DownloadTask; "
                "check_eligibility 强事实旁路命中, 不必再调 search_metadata"
            ),
        }

    return ToolResult(
        status="success",
        summary=(
            f"Eligibility: {'eligible' if result.eligible else 'blocked'}"
            + (f" ({', '.join(result.blocking_reasons)})" if result.blocking_reasons else "")
        ),
        data=data,
    )


def make_get_auto_ingest_eligibility() -> ToolDefinition:
    return ToolDefinition(
        name="get_auto_ingest_eligibility",
        description="Check auto-ingest eligibility for the task. Returns confidence threshold, margin, has_clear_winner, best_candidate, runner_up, blocking_reasons, and task facts. Use this before deciding to persist or publish.",
        parameters=_GET_AUTO_INGEST_ELIGIBILITY_SCHEMA,
        permission_level=PermissionLevel.READ_ONLY,
        handler=_handle_get_auto_ingest_eligibility,
    )
