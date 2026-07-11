"""Auto-ingest business services -- eligibility, safety gates, metadata persistence.

All services are side-effect-free at the eligibility/check level.
Write operations (persist, fetch-and-save) are explicit and narrow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from media_pilot.adapters.metadata import MetadataDetail
from media_pilot.config import AppConfig
from media_pilot.orchestration.auto_confirmation import has_clear_winner, pick_best_candidate
from media_pilot.services.disc_input import (
    is_iso_image as is_iso_image_path,
    resolve_bdmv_movie_source,
)
from media_pilot.services.source_path_safety import is_safe_ingest_source_path
from media_pilot.services.task_input_analysis import (
    FileInfo,
    analyze_task_input,
    is_auxiliary_video,
)


# ── Eligibility check ────────────────────────────────────────────────

@dataclass(frozen=True, kw_only=True)
class EligibilityResult:
    eligible: bool
    media_type: str | None = None
    video_count: int = 0
    is_single_file: bool = False
    is_sample_or_trailer: bool = False
    is_bdmv_or_iso: bool = False
    is_bdmv_movie: bool = False
    is_iso_image: bool = False
    is_complex_directory: bool = False
    candidate_count: int = 0
    has_clear_winner: bool = False
    best_candidate: dict | None = None
    runner_up: dict | None = None
    confidence_threshold: float = 0.0
    margin: float = 0.0
    blocking_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    task_facts: dict = field(default_factory=dict)


def check_eligibility(
    *,
    session: Session,
    config: AppConfig,
    task_id: str,
) -> EligibilityResult:
    """Side-effect-free eligibility check for auto-ingest.

    Returns structured facts, safety gate results, threshold advice,
    warnings, and blocking reasons without mutating anything.
    """
    from media_pilot.repository.repositories import (
        IngestTaskRepository,
        MediaCandidateRepository,
    )

    task_repo = IngestTaskRepository(session)
    task = task_repo.get(task_id)
    if task is None:
        return EligibilityResult(
            eligible=False,
            blocking_reasons=["task_not_found"],
        )

    candidate_repo = MediaCandidateRepository(session)
    candidates = candidate_repo.list_for_task(task_id)

    blocking: list[str] = []
    warnings: list[str] = []
    facts: dict = {
        "task_id": task.id,
        "source_path": task.source_path,
        "status": task.status,
        "media_type": task.media_type,
        "title": task.title,
        "year": task.year,
    }

    # ── scan source files ──────────────────────────────────────────
    source_path = Path(task.source_path)
    if not source_path.exists():
        return EligibilityResult(
            eligible=False,
            blocking_reasons=["source_path_not_found"],
            task_facts=facts,
        )

    try:
        analysis = analyze_task_input(source_path)
    except Exception:
        return EligibilityResult(
            eligible=False,
            blocking_reasons=["file_scan_failed"],
            task_facts=facts,
        )

    video_files = [f for f in analysis.files if f.type == "video"]
    excluded_videos = [e for e in analysis.excluded if e.type == "video"]
    facts["video_count"] = analysis.video_count
    facts["video_files"] = [{"name": f.name, "size_bytes": f.size_bytes} for f in video_files]
    facts["subtitle_count"] = analysis.subtitle_count
    facts["is_directory"] = analysis.is_directory

    # ── user-decided primary video: 任务已写入 MediaSourceSelection 任务事实.
    #    用户已通过 select_primary_video 选过主视频 → 视为单视频任务,
    #    不再被 multiple_video_files_not_supported 等门禁阻塞.
    from media_pilot.repository.repositories import MediaSourceSelectionRepository
    selection = MediaSourceSelectionRepository(session).get_for_task(task.id)
    has_user_selection = (
        selection is not None
        and bool(selection.selected_path)
        and isinstance(selection.payload, dict)
        and selection.payload.get("selection_source") == "user_decision"
    )
    if has_user_selection:
        user_video = Path(selection.selected_path)  # type: ignore[arg-type]
        if user_video.exists() and user_video.is_file():
            video_files = [
                FileInfo(  # type: ignore[name-defined]
                    path=str(user_video),
                    name=user_video.name,
                    size_bytes=user_video.stat().st_size,
                    type="video",
                )
            ]
            facts["video_count"] = 1
            facts["video_files"] = [{
                "name": user_video.name,
                "size_bytes": user_video.stat().st_size,
            }]
            facts["user_selected_video"] = str(user_video)

    # ── safety hard gates ───────────────────────────────────────────

    # Gate 1: BDMV / ISO detection. BDMV movie directories are supported as
    # opaque disc inputs; ISO/IMG remains unsupported.
    bdmv_source = resolve_bdmv_movie_source(source_path)
    is_bdmv_movie = bdmv_source is not None
    is_iso_source = is_iso_image_path(source_path)
    if is_bdmv_movie:
        facts["source_kind"] = "bdmv"
        facts["bdmv_dir"] = str(bdmv_source.bdmv_dir)
    if is_iso_source:
        blocking.append("iso_image_not_supported")
        return EligibilityResult(
            eligible=False,
            media_type=task.media_type,
            video_count=analysis.video_count,
            is_bdmv_or_iso=True,
            is_iso_image=True,
            blocking_reasons=blocking,
            warnings=warnings,
            task_facts=facts,
            confidence_threshold=config.metadata_auto_confirm_confidence,
            margin=config.metadata_auto_confirm_margin,
        )

    # Gate 2: Sample / trailer detection
    # 修复 USBA-089: 必须区分 marker 排除 (``sample/trailer/auxiliary``)
    # 与 size-ratio 排除 (``low_value_size_ratio:small_companion_video``)
    # 两种 ``excluded_reason``. size 启发式命中产物 (e.g. 1.9 MB 广告)
    # 不得触发 sample/trailer 阻断, 用户已选主视频时也不得阻断.
    # is_sample 仅在以下三种情况之一成立时返回 True:
    # 1) 当前唯一候选主视频文件名命中 is_auxiliary_video (marker).
    # 2) 输入本身是单文件且被 marker 判为 auxiliary.
    # 3) excluded_videos 里有 marker 排除 (``sample/trailer/auxiliary``)
    #    且没有用户选择 + video_files 为空 (没有有效主视频可发布).
    is_sample = False
    if analysis.video_count == 1 and video_files:
        is_sample = is_auxiliary_video(Path(video_files[0].name).stem)
    if not is_sample and excluded_videos:
        marker_excluded = [
            e for e in excluded_videos
            if e.excluded_reason == "sample/trailer/auxiliary"
        ]
        # 仅在 marker 排除 + 无用户选择 + 无有效主视频时阻断. 用户已选
        # (has_user_selection) 时以 selected_path 为有效主视频事实;
        # video_files 非空 (有 main 视频) 时同理. 此外, 全部 excluded
        # 都是 low_value_size_ratio 命中的小伴随视频时, 也不得阻断.
        if marker_excluded and not has_user_selection and not video_files:
            is_sample = True

    if is_sample:
        blocking.append("sample_or_trailer_not_supported")
        return EligibilityResult(
            eligible=False,
            media_type=task.media_type,
            video_count=analysis.video_count,
            is_single_file=not analysis.is_directory,
            is_sample_or_trailer=True,
            blocking_reasons=blocking,
            warnings=warnings,
            task_facts=facts,
            confidence_threshold=config.metadata_auto_confirm_confidence,
            margin=config.metadata_auto_confirm_margin,
        )

    # Gate 3: Not a single movie. 剧集任务由 ``prepare_show_structure``
    # 做结构识别 + 阻塞判定 (cross_season / sparse / season_0 等),
    # 不在 movie-only eligibility 路径上被 ``media_type_not_movie:show``
    # 阻塞; 否则同季连续多集剧集会因 LLM 先调 eligibility 而被错判.
    if task.media_type and task.media_type not in ("movie", "show"):
        blocking.append(f"media_type_not_supported:{task.media_type}")
    if analysis.video_count == 0 and not video_files and not is_bdmv_movie:
        blocking.append("no_video_files_found")
    # 剧集允许多视频 (单集 / 同季连续多集); 仅在 movie 路径上拦截.
    if task.media_type == "movie" and analysis.video_count > 1 and not is_bdmv_movie:
        blocking.append("multiple_video_files_not_supported")

    is_complex = analysis.is_directory and analysis.video_count > 1

    # Gate 4: Path safety check
    if not is_safe_ingest_source_path(source_path, config, task_id=task.id):
        blocking.append("source_path_outside_safe_roots")

    # ── metadata threshold check (soft gate) ────────────────────────
    confidence_threshold = config.metadata_auto_confirm_confidence
    margin = config.metadata_auto_confirm_margin

    best_candidate: dict | None = None
    runner_up: dict | None = None
    has_winner = False

    # 强事实旁路 (publish gate 尊重用户选择): 用户已通过
    # select_metadata_candidate 决策显式选过的候选 (source="user_decision")
    # 是最高优先级 winner, 不得被 has_clear_winner 的 margin 边界
    # 误判为 no_clear_metadata_winner. 即使 list_for_task 里还有其它
    # 老 agent / tmdb 候选与 user_decision 接近, user_decision 仍胜出.
    user_decision_candidate = next(
        (c for c in candidates if (c.source or "") == "user_decision"),
        None,
    )

    # 强事实旁路 (preselected): DownloadTask 上挂的元数据预选. 三字段都
    # 存在时, 把它当强事实, 不得因 confidence 不足 / 无候选 / close
    # candidate 阻塞 publish. 与 user_decision 互斥时, user_decision 优先
    # (用户在 select_metadata_candidate 决策里已选过).
    has_preselected = bool(
        task.preselected_metadata_provider
        and task.preselected_metadata_external_id
    )

    if user_decision_candidate is not None:
        # 已有用户决策 → 直接当 winner, 跳过 has_clear_winner 判定.
        # 历史重复候选 (同一 external_id 的 agent / tmdb 落库) 不得
        # 阻塞 publish_movie_to_library 的发布门禁.
        has_winner = True
        best_candidate = {
            "provider": user_decision_candidate.source,
            "provider_id": user_decision_candidate.external_id,
            "title": user_decision_candidate.title,
            "year": user_decision_candidate.year,
            "confidence": user_decision_candidate.confidence,
        }
    elif has_preselected:
        # preselected 强事实旁路: 不论持久化候选列表是否为空, 都主动
        # 生成可消费 winner. check_eligibility 保持 side-effect-free
        # 语义 (不调 provider, 不写库), 用 ``_build_preselected_fact``
        # 纯计算 preselected fact dict. READ_ONLY 工具读
        # ``facts["preselected"]`` 即可拿到稳定事实, 不必再调一次
        # _resolve_preselected_winner (那条会落库 + 拉 provider).
        from media_pilot.services.select_metadata_candidate import (
            _build_preselected_fact,
        )
        from media_pilot.repository.repositories import (
            MetadataDetailRepository,
        )

        # 读 MetadataDetail 做 title / year 兜底, 不写库不调 provider.
        persisted_detail: dict | None = None
        orm_detail = MetadataDetailRepository(session).get_for_task(task.id)
        if orm_detail is not None:
            persisted_detail = {
                "title": orm_detail.title,
                "year": orm_detail.year,
            }

        preselected_fact = _build_preselected_fact(
            task=task,
            persisted_candidates=candidates,
            persisted_detail=persisted_detail,
        )
        if preselected_fact is not None:
            has_winner = True
            best_candidate = {
                "provider": preselected_fact["provider"],
                "provider_id": preselected_fact["provider_id"],
                "title": preselected_fact.get("title"),
                "year": preselected_fact.get("year"),
                "confidence": preselected_fact.get("confidence", 1.0),
            }
            facts["preselected"] = preselected_fact
        else:
            # 极少见: _build_preselected_fact 自身异常. 落回
            # no_metadata_candidates blocking.
            blocking.append("no_metadata_candidates")
    elif candidates:
        adapter_candidates = [
            type("_C", (), {
                "confidence": c.confidence,
                "title": c.title,
                "year": c.year,
                "provider_id": c.external_id,
                "media_type": c.media_type,
                "poster_url": c.payload.get("poster_url") if c.payload else None,
                "overview": c.payload.get("overview") if c.payload else None,
            })()
            for c in candidates
        ]
        has_winner = has_clear_winner(
            adapter_candidates,
            confidence_threshold=confidence_threshold,
            margin=margin,
        )
        if has_winner:
            best, runner = pick_best_candidate(adapter_candidates)
            best_candidate = {
                "provider": candidates[0].source,
                "provider_id": best.provider_id,
                "title": best.title,
                "year": best.year,
                "confidence": best.confidence,
            }
            if runner is not None:
                runner_up = {
                    "provider": candidates[0].source,
                    "provider_id": runner.provider_id,
                    "title": runner.title,
                    "year": runner.year,
                    "confidence": runner.confidence,
                }
        else:
            if adapter_candidates:
                blocking.append("no_clear_metadata_winner")
            else:
                blocking.append("no_metadata_candidates")
    else:
        blocking.append("no_metadata_candidates")

    # ── determine eligibility ───────────────────────────────────────
    eligible = len(blocking) == 0

    return EligibilityResult(
        eligible=eligible,
        media_type=task.media_type,
        video_count=analysis.video_count,
        is_single_file=not analysis.is_directory,
        is_sample_or_trailer=is_sample,
        is_bdmv_or_iso=is_bdmv_movie or is_iso_source,
        is_bdmv_movie=is_bdmv_movie,
        is_iso_image=is_iso_source,
        is_complex_directory=is_complex,
        candidate_count=len(candidates),
        has_clear_winner=has_winner,
        best_candidate=best_candidate,
        runner_up=runner_up,
        confidence_threshold=confidence_threshold,
        margin=margin,
        blocking_reasons=blocking,
        warnings=warnings,
        task_facts=facts,
    )


# ── Persist metadata selection ──────────────────────────────────────

@dataclass(frozen=True, kw_only=True)
class PersistSelectionResult:
    status: str
    summary: str
    candidate_id: str | None = None


def persist_metadata_selection(
    *,
    session: Session,
    task_id: str,
    provider_name: str,
    provider_id: str,
    media_type: str,
    title: str,
    year: int | None = None,
    confidence: float | None = None,
    original_title: str | None = None,
    payload: dict | None = None,
) -> PersistSelectionResult:
    """Persist a metadata candidate selection for the task.

    Creates a MediaCandidate record with source="agent" to track the
    Agent's selection decision.
    """
    from media_pilot.accounts.task_classification import (
        is_adult_metadata_selection,
    )
    from media_pilot.repository.repositories import (
        IngestTaskRepository,
        MediaCandidateRepository,
    )

    task_repo = IngestTaskRepository(session)
    task = task_repo.get(task_id)
    if task is None:
        return PersistSelectionResult(
            status="failure",
            summary=f"Task {task_id} not found",
        )

    candidate_repo = MediaCandidateRepository(session)
    candidate = candidate_repo.add_candidate(
        task_id=task_id,
        source="agent",
        media_type=media_type,
        title=title,
        original_title=original_title,
        year=year,
        external_id=provider_id,
        confidence=confidence,
        reason=f"Agent selected {title} from {provider_name}",
        payload=payload or {},
    )

    # Update task with selection info
    if not task.media_type or task.media_type == "unknown":
        task.media_type = media_type
    if not task.title:
        task.title = title or original_title
    if task.year is None and year is not None:
        task.year = year
    if confidence is not None:
        task.confidence = max(task.confidence or 0, confidence)
    task.is_adult = is_adult_metadata_selection(
        profile=None,
        provider=provider_name,
    )
    session.flush()

    return PersistSelectionResult(
        status="success",
        summary=f"Persisted selection: {title} ({year}) from {provider_name}",
        candidate_id=candidate.id,
    )


# ── Fetch and save metadata detail ──────────────────────────────────

@dataclass(frozen=True, kw_only=True)
class FetchAndSaveDetailResult:
    status: str
    summary: str
    provider: str | None = None
    provider_id: str | None = None
    title: str | None = None
    year: int | None = None


@dataclass(frozen=True, kw_only=True)
class MetadataDetailPayload:
    detail: MetadataDetail
    payload: dict


def fetch_metadata_detail_payload(
    *,
    config: AppConfig,
    provider_name: str,
    provider_id: str,
    media_type: str,
) -> MetadataDetailPayload:
    """Fetch metadata draft from provider and build a flattened save payload.

    Pure fetch helper, no DB session argument.
    """
    from dataclasses import asdict

    from media_pilot.services.metadata_draft import fetch_metadata_draft

    language = list(config.tmdb_language_priority)
    draft = fetch_metadata_draft(
        config=config,
        provider_name=provider_name,
        provider_id=provider_id,
        media_type=media_type,
        language_priority=language,
    )

    detail = draft.detail
    payload = asdict(detail)
    payload["directors"] = draft.directors
    payload["actors"] = draft.actors
    payload["imdb_id"] = draft.imdb_id
    payload["poster_url"] = draft.poster_url
    payload["backdrop_url"] = draft.backdrop_url
    payload["logo_url"] = draft.logo_url

    return MetadataDetailPayload(detail=detail, payload=payload)


def fetch_and_save_metadata_detail(
    *,
    session: Session,
    config: AppConfig,
    task_id: str,
    provider_name: str,
    provider_id: str,
    media_type: str,
) -> FetchAndSaveDetailResult:
    """Fetch metadata detail from a provider and save it as MetadataDetail.

    Reuses ``fetch_metadata_draft`` for the provider call and
    ``MetadataDetailRepository.save`` for persistence.
    """
    from media_pilot.repository.repositories import MetadataDetailRepository
    from media_pilot.services.metadata_draft import (
        ProviderError,
    )

    try:
        draft_payload = fetch_metadata_detail_payload(
            config=config,
            provider_name=provider_name,
            provider_id=provider_id,
            media_type=media_type,
        )
    except ValueError as exc:
        return FetchAndSaveDetailResult(
            status="failure",
            summary=f"Invalid provider or media type: {exc}",
        )
    except ProviderError as exc:
        return FetchAndSaveDetailResult(
            status="failure",
            summary=f"Provider error: {exc.provider_message}",
            provider=provider_name,
            provider_id=provider_id,
        )
    except Exception as exc:
        return FetchAndSaveDetailResult(
            status="failure",
            summary=f"Provider call failed: {exc}",
            provider=provider_name,
            provider_id=provider_id,
        )

    detail = draft_payload.detail

    repo = MetadataDetailRepository(session)
    repo.save(
        task_id=task_id,
        provider=detail.provider,
        provider_id=detail.provider_id,
        media_type=detail.media_type,
        title=detail.title,
        original_title=detail.original_title,
        year=detail.year,
        payload=draft_payload.payload,
    )
    session.flush()

    return FetchAndSaveDetailResult(
        status="success",
        summary=f"Saved metadata detail: {detail.title} ({detail.year}) from {provider_name}",
        provider=detail.provider,
        provider_id=detail.provider_id,
        title=detail.title,
        year=detail.year,
    )
