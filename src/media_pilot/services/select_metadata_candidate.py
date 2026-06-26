"""通用 ``select_metadata_candidate`` 决策服务.

电影和剧集共用同一决策语义: 当元数据候选没有 clear winner 时,
由后端基于已持久化的 MediaCandidate 列表生成 AgentDecisionRequest
选项, payload 携带稳定的 ``candidate_id`` 引用, 不让 LLM 拼接
provider_id / media_type / 路径 payload. 决策回复后:

- ``task.media_type`` 由被选候选的 ``media_type`` 决定, 后续 Agent
  续跑按电影或剧集分流 (电影 → publish_movie_to_library; 剧集 →
  prepare_show_structure / publish_show_to_library).
- 选中的候选会以 ``source="user_decision"`` 写入新的 MediaCandidate,
  ``external_id / title / year / confidence`` 一并落库, 后续
  fetch_and_save_metadata_detail / publish_* 工具消费该事实, 不需要
  重新猜.

边界:
- 只对 ``MediaCandidateRepository.list_for_task`` 的结果操作, 不
  重新调用 provider.
- 选项的稳定引用是 ``candidate_id`` (MediaCandidate.id), 不是
  provider_id. 回复 handler 据此校验拒绝伪造 option_id.
- 复用 ``orchestration.auto_confirmation.has_clear_winner`` 决定
  是否 auto-confirm, 与 ``search_metadata`` / ``get_auto_ingest_eligibility``
  保持一致.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from media_pilot.config import AppConfig

if TYPE_CHECKING:
    from media_pilot.repository.models import MediaCandidate


# Status returns for prepare_select_metadata_candidate_decision.
STATUS_AUTO_CONFIRM = "auto_confirm"
STATUS_DECISION_REQUESTED = "decision_requested"
STATUS_NO_CANDIDATES = "no_candidates"
STATUS_EMPTY_CANDIDATES = "empty_candidates"
# MP-Test-02 (Titanic) 现场引入: 恢复 ``search_metadata`` 历史时,
# ``output`` 缺关键字段 (candidates / keyword / provider / media_type)
# 或单条候选缺 external_id / title / media_type. 工具返 failure, 不写
# 半成品 MediaCandidate.
STATUS_RECOVERED_OUTPUT_INVALID = "recovered_output_invalid"

# Status returns for handle_select_metadata_candidate.
REPLY_STATUS_RECORDED = "recorded"
REPLY_STATUS_FAILED = "failed"


@dataclass(frozen=True, kw_only=True)
class CandidateOption:
    """后端标准化生成的候选选项 — payload 由后端写入, 不让 LLM 拼路径."""

    id: str
    label: str
    description: str = ""
    payload: dict = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class SelectMetadataCandidateResult:
    """``prepare_select_metadata_candidate_decision`` 的结构化结果.

    - ``status == STATUS_AUTO_CONFIRM``: 已有 clear winner, 工具直接
      返回 winner 事实, 提示 Agent 跳过决策.
    - ``status == STATUS_DECISION_REQUESTED``: 已创建
      ``select_metadata_candidate`` AgentDecisionRequest, Agent 续跑
      必须停止, 等用户回复.
    - ``status == STATUS_NO_CANDIDATES``: 任务没有任何候选, 工具返回
      failure, Agent 应当先去 search_metadata 拉候选.
    - ``status == STATUS_EMPTY_CANDIDATES``: 任务事实不一致 (failed);
      视为 failure.
    """

    status: str
    decision_id: str | None = None
    decision_type: str | None = None
    question: str | None = None
    options: list[CandidateOption] = field(default_factory=list)
    best_candidate: dict | None = None
    reason: str = ""


def _candidate_label(c: "MediaCandidate") -> str:
    """人类可读标签 — 给前端展示, 不含路径."""
    title = (c.title or c.original_title or "(untitled)").strip()
    if c.year:
        return f"{title} ({c.year})"
    return title


def _candidate_description(c: "MediaCandidate") -> str:
    """来源 / 类型 / 置信度 — 让用户能横向对比."""
    pieces: list[str] = []
    media_type = c.media_type or "unknown"
    if c.season is not None and c.episode is not None:
        pieces.append(f"{media_type} S{c.season:02d}E{c.episode:02d}")
    else:
        pieces.append(media_type)
    if c.confidence is not None:
        pieces.append(f"confidence={c.confidence:.2f}")
    if c.source:
        pieces.append(f"source={c.source}")
    return " · ".join(pieces)


def _candidate_overview(c: "MediaCandidate") -> str:
    """从 payload 取 overview — 给前端摘要展示, 不让 LLM 改写."""
    if not isinstance(c.payload, dict):
        return ""
    return str(c.payload.get("overview") or "").strip()


def build_candidate_options(
    candidates: list["MediaCandidate"],
) -> list[CandidateOption]:
    """把已持久化的 MediaCandidate 列表翻译成 AgentDecisionRequest 的选项.

    选项 ``id`` 统一为 ``candidate_<MediaCandidate.id>`` 形式; payload
    携带 ``candidate_id`` 稳定引用, 供 reply handler 校验. **不**让
    LLM 拼 provider_id / 路径 / media_type 字符串.

    provider 字段语义: 必须是真实 metadata provider (e.g. "tmdb" /
    "tpdb"), 不是 "preselected". 对 source="preselected" 的候选从
    payload.preselected_provider 读真 provider. candidate_source 字
    段单独表达来源 ("preselected" / "user_decision" / "agent" /
    provider 名), 跟 provider 解耦, 避免 LLM 把 "preselected" 当
    provider 名字传给 fetch_and_save_metadata_detail.
    """
    options: list[CandidateOption] = []
    for c in candidates:
        c_payload = c.payload if isinstance(c.payload, dict) else {}
        # 真 provider: payload.preselected_provider 优先, 否则 source
        # (但 source == "preselected" 时退到 None, 由调用方用 default).
        if c.source == "preselected":
            real_provider = c_payload.get("preselected_provider") or None
        else:
            real_provider = c.source or None
        options.append(CandidateOption(
            id=f"candidate_{c.id}",
            label=_candidate_label(c),
            description=_candidate_description(c),
            payload={
                "candidate_id": c.id,
                "provider": real_provider,
                "provider_id": c.external_id or None,
                "media_type": c.media_type or None,
                "title": c.title or None,
                "year": c.year,
                "confidence": c.confidence,
                "candidate_source": c.source or None,
                "overview": _candidate_overview(c),
            },
        ))
    return options


def _drop_agent_shadow_candidates(
    candidates: list["MediaCandidate"],
) -> list["MediaCandidate"]:
    """Drop Agent-created selections that duplicate a real provider candidate.

    ``persist_metadata_selection`` creates source="agent" records to record what
    the Agent picked. If the original provider candidate is still present, both
    rows can share the same external_id/confidence and falsely look like a tie.
    Keep the richer provider row for winner detection and decision options.
    """
    provider_keys = {
        (c.media_type, c.external_id)
        for c in candidates
        if c.source != "agent" and c.external_id
    }
    if not provider_keys:
        return candidates
    return [
        c for c in candidates
        if not (
            c.source == "agent"
            and c.external_id
            and (c.media_type, c.external_id) in provider_keys
        )
    ]


def _real_provider_for_candidate(
    candidate: "MediaCandidate",
    candidates: list["MediaCandidate"],
) -> str | None:
    payload = candidate.payload if isinstance(candidate.payload, dict) else {}
    if candidate.source == "preselected":
        return payload.get("preselected_provider") or None
    if candidate.source == "user_decision":
        source_candidate_id = payload.get("source_candidate_id")
        original = next(
            (c for c in candidates if c.id == source_candidate_id),
            None,
        )
        if original is not None and original.source != "user_decision":
            return _real_provider_for_candidate(original, candidates)
        return payload.get("provider") or None
    return candidate.source or None


def _candidate_winner_payload(
    candidate: "MediaCandidate",
    candidates: list["MediaCandidate"],
) -> dict | None:
    real_provider = _real_provider_for_candidate(candidate, candidates)
    if real_provider is None:
        return None
    return {
        "candidate_id": candidate.id,
        "provider": real_provider,
        "provider_id": candidate.external_id or None,
        "media_type": candidate.media_type or None,
        "title": candidate.title or None,
        "year": candidate.year,
        "confidence": candidate.confidence,
        "candidate_source": candidate.source or None,
    }


def _latest_user_decision_winner(
    candidates: list["MediaCandidate"],
) -> dict | None:
    """Existing user selection is a strong fact; never ask the user again."""
    for candidate in reversed(candidates):
        if candidate.source == "user_decision":
            return _candidate_winner_payload(candidate, candidates)
    return None


def _pick_best_winner(
    *,
    config: AppConfig,
    candidates: list["MediaCandidate"],
) -> dict | None:
    """复用 has_clear_winner 决定 clear winner; 命中则返回其轻量 payload."""
    from media_pilot.orchestration.auto_confirmation import (
        has_clear_winner,
        pick_best_candidate,
    )

    if not candidates:
        return None

    adapter_candidates = [
        type("_C", (), {
            "confidence": c.confidence,
            "title": c.title,
            "year": c.year,
            "provider_id": c.external_id,
            "media_type": c.media_type,
        })()
        for c in candidates
    ]
    if not has_clear_winner(
        adapter_candidates,
        confidence_threshold=config.metadata_auto_confirm_confidence,
        margin=config.metadata_auto_confirm_margin,
    ):
        return None

    best, _ = pick_best_candidate(adapter_candidates)
    if best is None:
        return None
    # 用 candidate_id 反查 MediaCandidate, 保留完整身份
    match = next(
        (c for c in candidates
         if c.external_id == getattr(best, "provider_id", None)
         and (c.media_type or None) == getattr(best, "media_type", None)
         and c.title == getattr(best, "title", None)
         and c.year == getattr(best, "year", None)
         and c.confidence == getattr(best, "confidence", None)),
        None,
    )
    if match is None:
        return None
    return _candidate_winner_payload(match, candidates)


def _persist_recovered_candidates(
    *,
    session: Session,
    task_id: str,
    recovered_search_results: list[dict],
    provider_name: str,
    keyword: str,
) -> int:
    """把工具层恢复的 ``search_metadata`` ``AgentToolCall.output.candidates``
    落库为 ``MediaCandidate``, 走原 auto_confirm / 决策路径.

    与 ``_search_and_persist_candidates`` 的区别: 后者重新调
    ``search_metadata`` 拉新结果, 本函数用已经拉过 / 已经存到
    ``AgentToolCall.output`` 的旧结果, 避免重复 provider 调用 + 兜底
    "LLM 漏传 keyword/provider" 的失败模式 (MP-Test-02 Titanic).

    入参格式: ``recovered_search_results`` 是 ``AgentToolCall.output.
    candidates`` 的字面 list[dict] (与 ``_handle_search_metadata`` 写入
    的结构一致). 任何候选缺 ``external_id`` / ``title`` / ``media_type``
    都会拒绝整批 (raise ValueError), 不写半成品.
    """
    from media_pilot.repository.repositories import MediaCandidateRepository

    repo = MediaCandidateRepository(session)
    persisted = 0
    for idx, c in enumerate(recovered_search_results):
        if not isinstance(c, dict):
            raise ValueError(
                f"recovered_search_results[{idx}] is not a dict: {type(c).__name__}"
            )
        # AgentToolCall.output.candidates 用 ``provider_id`` 形式 (与
        # ``_handle_search_metadata`` 写入结构一致); 同步接受
        # ``external_id`` 别名以便未来切换.
        external_id = c.get("external_id") or c.get("provider_id")
        title = c.get("title")
        media_type = c.get("media_type")
        missing = []
        if not external_id:
            missing.append("external_id|provider_id")
        if not title:
            missing.append("title")
        if not media_type:
            missing.append("media_type")
        if missing:
            raise ValueError(
                f"recovered_search_results[{idx}] missing required keys: "
                f"{', '.join(missing)}"
            )
        repo.add_candidate(
            task_id=task_id,
            source=c.get("provider") or provider_name,
            media_type=media_type,
            title=title,
            original_title=c.get("original_title"),
            year=c.get("year"),
            external_id=external_id,
            confidence=c.get("confidence") or 0.0,
            reason=(
                f"Recovered from search_metadata call (run keyword={keyword!r}, "
                f"provider={provider_name})"
            ),
            payload={
                "overview": c.get("overview") or "",
                "match_reason": c.get("match_reason") or "",
            },
        )
        persisted += 1
    session.flush()
    return persisted


def _search_and_persist_candidates(
    *,
    session: Session,
    config: AppConfig,
    task_id: str,
    keyword: str,
    provider_name: str,
    media_type: str,
) -> int:
    """在没有持久化候选时, 调用 search_metadata 重新搜索并落库.

    返回持久化的候选数量; search_metadata 失败 / 无结果返回 0.
    LLM 不需要自行拼接 provider_id / media_type / 路径 payload ——
    搜索结果由后端转写为 MediaCandidate 落库, 选项继续走
    ``build_candidate_options`` 用稳定 candidate_id 引用.
    """
    from media_pilot.repository.repositories import (
        MediaCandidateRepository,
    )
    from media_pilot.services.metadata_search import search_metadata

    language = list(getattr(config, "tmdb_language_priority", []) or [])
    result = search_metadata(
        config=config,
        provider_name=provider_name,
        keyword=keyword,
        language_priority=language,
        media_type=media_type,
    )

    if not result.candidates:
        return 0

    repo = MediaCandidateRepository(session)
    persisted = 0
    for c in result.candidates:
        repo.add_candidate(
            task_id=task_id,
            source=provider_name,
            media_type=c.media_type or media_type or "unknown",
            title=c.title,
            original_title=c.original_title,
            year=c.year,
            external_id=c.provider_id,
            confidence=c.confidence,
            reason=(
                f"Auto-search for task {task_id} via "
                f"{provider_name} keyword={keyword!r}"
            ),
            payload={
                "overview": c.overview,
                "match_reason": c.match_reason,
            },
        )
        persisted += 1
    session.flush()
    return persisted


def _build_preselected_fact(
    *,
    task,  # noqa: F821  # IngestTask
    persisted_candidates: list | None = None,
    persisted_detail: dict | None = None,
) -> dict | None:
    """纯计算: 从 ``task.preselected_metadata_*`` 构造只读 preselected fact.

    与 ``_resolve_preselected_winner`` (DRAFT 路径, 允许落库 + 调 provider)
    互补. 本函数:

    - 不写库 (无 ``add_candidate``).
    - 不调 provider (无 ``fetch_and_save_metadata_detail``).
    - 不持久化任何 ``MetadataDetail``.

    适用于 READ_ONLY 工具 (``get_auto_ingest_eligibility`` /
    ``get_metadata_candidates``) 与 ``check_eligibility`` 这类"检查
    路径", 必须保持 side-effect-free 语义.

    返回字段语义同 ``_resolve_preselected_winner``:

    - ``provider``: 真实 metadata provider (e.g. "tmdb").
    - ``provider_id``: 真实 provider id.
    - ``candidate_source``: "preselected" 标记来源.
    - ``candidate_id``: 若在 persisted_candidates 中命中同 external_id
      的 MediaCandidate, 返回它的 id; 否则 None (DRAFT 路径落库后
      才会有).
    - ``title`` / ``year``: 优先级 persisted candidate → persisted
      detail → task 自身字段. 任何来源都拿不到时返回 None, 调用方
      决定是否再走 DRAFT 路径 fetch.
    """
    if not (
        task.preselected_metadata_provider
        and task.preselected_metadata_external_id
    ):
        return None

    provider = task.preselected_metadata_provider
    external_id = task.preselected_metadata_external_id
    profile = task.preselected_metadata_profile
    media_type = "show" if profile in ("tmdb_show",) else "movie"
    provider_id = (
        f"show:{external_id}" if media_type == "show" else external_id
    )

    matched = None
    if persisted_candidates:
        matched = next(
            (c for c in persisted_candidates
             if c.external_id == provider_id
             and (c.media_type or "movie") == media_type
             and (c.source or "") in ("preselected", provider)
             and c.source is not None),
            None,
        )

    # title / year 优先级: matched candidate → persisted detail → task.
    title: str | None = None
    year: int | None = None
    if matched is not None:
        title = matched.title
        year = matched.year
    if (title is None or year is None) and isinstance(persisted_detail, dict):
        title = title or persisted_detail.get("title")
        year = year or persisted_detail.get("year")
    if title is None:
        title = getattr(task, "title", None)
    if year is None:
        year = getattr(task, "year", None)

    return {
        "provider": provider,
        "provider_id": provider_id,
        "media_type": media_type,
        "title": title,
        "year": year,
        "confidence": 1.0,
        "candidate_source": "preselected",
        "candidate_id": matched.id if matched is not None else None,
    }


def _resolve_preselected_winner(
    *,
    session: Session,
    config: AppConfig,  # noqa: ARG001 - 保留 config 接口, 后续可能用于 trigger 后续
    task,  # noqa: F821  # IngestTask
    provider: str,
    external_id: str,
    profile: str | None,
    candidates: list,
    fetch_detail_on_empty: bool = True,
) -> dict | None:
    """把 task.preselected_metadata_* 解析为 best_candidate 事实.

    本函数只在 DRAFT / WRITE 路径被调 (e.g. ``prepare_select_metadata_
    candidate_decision``), 允许落库 + 调 provider. READ_ONLY 工具不得
    走本函数 — 它们的 preselected facts 走 ``_build_preselected_fact``
    纯计算, 不写库不拉 detail.

    返回字段语义:
    - ``provider``: 真实 metadata provider (e.g. "tmdb"), 不是
      "preselected". LLM 拿这个字段去调 ``fetch_and_save_metadata_
      detail(provider_name=provider, ...)``.
    - ``candidate_source``: "preselected" 标记候选来源, 与 provider
      解耦. 下游展示 / 决策追溯用这个字段.
    - ``candidate_id``: 持久化候选的稳定 id, 后续 fetch / publish 走它.

    优先级:
    1. 在已持久化候选中按 (external_id, media_type) 匹配已有
       MediaCandidate. source 字段允许 "preselected" (本函数前轮落
       库) / provider 名 (search_metadata / persist_metadata_
       selection 路径落库). 命中后把 confidence 升到 1.0. provider
       字段从 payload.preselected_provider 读, 避免 source="preselected"
       被冒充为 provider.
    2. 没匹配上则用 preselected 自身字段落库一条新 MediaCandidate
       (source="preselected"), confidence=1.0, payload 写
       preselected_provider = 真 provider. 当 ``fetch_detail_on_
       empty=True`` (默认) 时, 落库前先尝试 ``fetch_and_save_
       metadata_detail`` 拉真实 detail, 让 ``title`` / ``year`` 来
       自 provider 而非 None. 失败回退到 ``task.title`` /
       ``task.year`` 作为 best_candidate 显示值, 避免
       prepare_select_metadata_candidate_decision 工具摘要出现
       字面量 "None".
    3. provider 不识别 / 落库失败 → 返回 None, 调用方继续走常规
       候选路径 (search + clear winner).
    """
    from media_pilot.repository.repositories import (
        MediaCandidateRepository,
    )

    media_type = "show" if profile in ("tmdb_show",) else "movie"
    provider_id_for_match = (
        f"show:{external_id}" if media_type == "show" else external_id
    )

    # 1) 优先复用已持久化的同 external_id 候选. 匹配维度是
    # (external_id, media_type), source 字段可以是 "preselected" (本
    # 函数前轮落库) / provider 名 (search_metadata / persist_metadata
    # _selection 路径落库). 只要 external_id 与 media_type 命中即视为
    # 同一条 preselected 事实, 复用即可, 避免重复落库.
    matched = next(
        (c for c in candidates
         if c.external_id == provider_id_for_match
         and (c.media_type or "movie") == media_type
         and (c.source or "") in ("preselected", provider)
         and c.source is not None),
        None,
    )
    if matched is not None:
        # 升 confidence 到 1.0 — 强事实, 不得被 has_clear_winner 的
        # margin 边界误判为 no_clear_winner. 复用既有 candidate_id
        # 作为 best_candidate.candidate_id, 后续 fetch / publish 工具
        # 走同一条事实, 不需要再落库.
        if (matched.confidence or 0) < 1.0:
            matched.confidence = 1.0
            session.flush()
        # provider 字段语义: 必须返回真 metadata provider ("tmdb" 等),
        # 不是 "preselected". payload.preselected_provider 是本函数
        # 落库时写入的真 provider; 若没有 payload (e.g. provider 名
        # search 路径落库), 退回到 matched.source.
        matched_payload = matched.payload if isinstance(matched.payload, dict) else {}
        real_provider = (
            matched_payload.get("preselected_provider")
            or (matched.source if matched.source != "preselected" else provider)
        )
        return {
            "candidate_id": matched.id,
            "provider": real_provider,
            "provider_id": matched.external_id,
            "media_type": matched.media_type or media_type,
            "title": matched.title,
            "year": matched.year,
            "confidence": 1.0,
            "candidate_source": "preselected",
        }

    # 2) 没有同 external_id 候选 → 落库一条 preselected 候选. 先尝试
    # fetch_and_save_metadata_detail 拉真实 detail (provider 真实 title /
    # year), 失败回退到 task.title / task.year, 让 best_candidate
    # 始终带非 None 显示值.
    repo = MediaCandidateRepository(session)
    resolved_title: str | None = None
    resolved_year: int | None = None
    detail_fetched = False

    if fetch_detail_on_empty:
        # fetch_and_save_metadata_detail 内部用 provider adapter 拉 detail
        # 并落库 MetadataDetail. 成功时 MetadataDetail.title / year 真实
        # 可用, 直接作为 MediaCandidate 的 title / year. 失败 (ProviderError
        # / network error) 时回退到 task.title / task.year.
        from media_pilot.services.auto_ingest import fetch_and_save_metadata_detail

        fetch_result = fetch_and_save_metadata_detail(
            session=session,
            config=config,
            task_id=task.id,
            provider_name=provider,
            provider_id=external_id,
            media_type=media_type,
        )
        if fetch_result.status == "success":
            resolved_title = fetch_result.title
            resolved_year = fetch_result.year
            detail_fetched = True

    if not detail_fetched:
        # 兜底: 用 task 自身字段 (e.g. Titanic / 1997), 让 best_candidate
        # 至少能展示 "Titanic (1997)" 而不是 "None (None)".
        resolved_title = getattr(task, "title", None)
        resolved_year = getattr(task, "year", None)

    new_candidate = repo.add_candidate(
        task_id=task.id,
        source="preselected",
        media_type=media_type,
        title=resolved_title,
        original_title=None,
        year=resolved_year,
        external_id=provider_id_for_match,
        confidence=1.0,
        reason=(
            f"Preselected from DownloadTask: provider={provider} "
            f"external_id={external_id} profile={profile or '-'}"
            + (" (fetched detail)" if detail_fetched else " (task fallback)")
        ),
        payload={
            "preselected_from_download": True,
            "preselected_provider": provider,
            "preselected_external_id": external_id,
            "preselected_profile": profile or None,
            "preselected_resolved_via": (
                "provider_detail" if detail_fetched else "task_fallback"
            ),
        },
    )
    session.flush()
    return {
        "candidate_id": new_candidate.id,
        "provider": provider,
        "provider_id": provider_id_for_match,
        "media_type": media_type,
        "title": resolved_title,
        "year": resolved_year,
        "confidence": 1.0,
        "candidate_source": "preselected",
    }


def prepare_select_metadata_candidate_decision(
    *,
    session: Session,
    config: AppConfig,
    task_id: str,
    keyword: str | None = None,
    provider_name: str | None = None,
    media_type: str | None = None,
    recovered_search_results: list[dict] | None = None,
) -> SelectMetadataCandidateResult:
    """读取已持久化的 MediaCandidate 列表, 决定 auto-confirm / 决策 / 失败.

    这是 ``prepare_select_metadata_candidate_decision`` Agent 工具的
    后端判定层, 工具负责 ``AgentDecisionRequest`` 的落库; 本函数是
    纯 side-effect-free 的判定, 方便单测.

    三个可选参数 ``keyword`` / ``provider_name`` / ``media_type`` 是
    "低置信搜索后续路径" 的搜索提示: 当任务没有任何持久化候选时,
    本函数会先按这些提示调用 search_metadata, 把结果落库, 再走
    常规 auto-confirm / 决策判定. LLM 不需要自行拼接 provider_id /
    media_type / 路径 payload, 搜索结果完全由后端转写为
    MediaCandidate, 候选选项继续用稳定的 candidate_id 引用.

    这条路径的目的: LLM 在 search_metadata 返回 has_clear_winner=False
    时, 不必因为 "候选还没持久化" 而无法走 select_metadata_candidate
    决策 —— 同一关键词 / provider 的搜索结果由本工具内部持久化.
    """
    from media_pilot.repository.repositories import (
        IngestTaskRepository,
        MediaCandidateRepository,
    )

    task = IngestTaskRepository(session).get(task_id)
    if task is None:
        return SelectMetadataCandidateResult(
            status=STATUS_EMPTY_CANDIDATES,
            reason="task_not_found",
        )

    # ── 强事实旁路: DownloadTask 上挂的 preselected 元数据. 三字段都
    # 存在时, 把它当强事实, 不向用户确认同一个元数据. 优先在已持久化
    # 候选中按 (provider, external_id) 匹配; 没匹配上则用 preselected
    # 落库一条新候选 (source="preselected") 作为后续 fetch / publish
    # 工具的唯一事实来源. Auto-confirm 的 confidence 拉到 1.0, 避免
    # has_clear_winner 边界条件 (margin) 把它当 close candidate.
    if (
        task.preselected_metadata_provider
        and task.preselected_metadata_external_id
    ):
        winner = _resolve_preselected_winner(
            session=session,
            config=config,
            task=task,
            provider=task.preselected_metadata_provider,
            external_id=task.preselected_metadata_external_id,
            profile=task.preselected_metadata_profile,
            candidates=MediaCandidateRepository(session).list_for_task(task_id),
        )
        if winner is not None:
            # 同步回写 task 主字段 — 与 persist_metadata_selection /
            # handle_select_metadata_candidate 行为一致. 修复 USBA-089:
            # preselected 落库 MediaCandidate / MetadataDetail 后, 后续
            # draft_publish_plan 必须能读到 task.media_type 才能决定
            # "电影 / 剧集" 分流, 否则报 "Task has no media_type;
            # cannot determine write plan type" 失败.
            # winner 同时覆盖 matched-existing 与 newly-persisted 两条
            # 返回路径 (两条都返回同一 dict 结构: title / year /
            # media_type / confidence).
            if not task.media_type or task.media_type == "unknown":
                task.media_type = winner.get("media_type") or task.media_type
            if not task.title and winner.get("title"):
                task.title = winner["title"]
            if task.year is None and winner.get("year") is not None:
                task.year = winner["year"]
            if winner.get("confidence") is not None:
                task.confidence = max(task.confidence or 0, winner["confidence"])
            session.flush()
            return SelectMetadataCandidateResult(
                status=STATUS_AUTO_CONFIRM,
                best_candidate=winner,
                reason="preselected_from_download_task",
            )

    candidates = MediaCandidateRepository(session).list_for_task(task_id)

    # 没有持久化候选, 但工具层注入了 ``recovered_search_results`` (从
    # 同 run 最近一次成功 ``search_metadata`` 的 ``AgentToolCall.output``
    # 反查) → 直接落库. 覆盖 LLM 漏传 keyword/provider/media_type 的
    # 失败模式 (MP-Test-02 Titanic 现场).
    if not candidates and recovered_search_results is not None:
        try:
            persisted = _persist_recovered_candidates(
                session=session,
                task_id=task_id,
                recovered_search_results=recovered_search_results,
                provider_name=provider_name or "tmdb",
                keyword=keyword or "",
            )
        except ValueError as exc:
            # 恢复字段缺失 → 工具返 failure, 不写半成品
            return SelectMetadataCandidateResult(
                status=STATUS_RECOVERED_OUTPUT_INVALID,
                reason=f"recovered_output_invalid:{exc}",
            )
        if persisted == 0:
            return SelectMetadataCandidateResult(
                status=STATUS_NO_CANDIDATES,
                reason="recovered_search_results_empty",
            )
        candidates = MediaCandidateRepository(session).list_for_task(task_id)
    # 没有持久化候选, 但 LLM 提供了搜索提示 → 重新搜索并落库.
    # 这覆盖 search_metadata 工具因 has_clear_winner=False 之后
    # 直接调用本工具的场景. 同一 keyword/provider 再次搜索会落
    # 一组与 search_metadata 工具结果一致的 MediaCandidate.
    elif (
        not candidates
        and keyword
        and provider_name
        and media_type in ("movie", "show", "both")
    ):
        persisted = _search_and_persist_candidates(
            session=session,
            config=config,
            task_id=task_id,
            keyword=keyword,
            provider_name=provider_name,
            media_type=media_type,
        )
        if persisted == 0:
            return SelectMetadataCandidateResult(
                status=STATUS_NO_CANDIDATES,
                reason="search_returned_no_candidates",
            )
        candidates = MediaCandidateRepository(session).list_for_task(task_id)
    elif not candidates:
        return SelectMetadataCandidateResult(
            status=STATUS_NO_CANDIDATES,
            reason="no_persisted_candidates",
        )
    candidates = _drop_agent_shadow_candidates(candidates)
    if not candidates:
        return SelectMetadataCandidateResult(
            status=STATUS_NO_CANDIDATES,
            reason="no_persisted_candidates",
        )

    user_decision_winner = _latest_user_decision_winner(candidates)
    if user_decision_winner is not None:
        return SelectMetadataCandidateResult(
            status=STATUS_AUTO_CONFIRM,
            best_candidate=user_decision_winner,
            reason="existing_user_decision",
        )

    winner = _pick_best_winner(config=config, candidates=candidates)
    if winner is not None:
        return SelectMetadataCandidateResult(
            status=STATUS_AUTO_CONFIRM,
            best_candidate=winner,
            reason="clear_winner",
        )

    options = build_candidate_options(candidates)
    return SelectMetadataCandidateResult(
        status=STATUS_DECISION_REQUESTED,
        decision_type="select_metadata_candidate",
        question=(
            f"为任务找到 {len(candidates)} 个元数据候选, 但没有清晰的胜出者。"
            "请选择正确的元数据。"
        ),
        options=options,
        reason="no_clear_winner",
    )


# ── Reply handler ────────────────────────────────────────────────────


@dataclass(frozen=True, kw_only=True)
class SelectMetadataCandidateReplyResult:
    """``handle_select_metadata_candidate`` 的结果.

    - ``status == "recorded"``: 选项已落库, 调用方 (decision_reply)
      继续 AgentRun.
    - ``status == "failed"``: option_id 非法 / candidate 不存在, 拒绝写入.
    """

    status: str
    candidate_id: str | None = None
    media_type: str | None = None
    reason: str = ""


def _parse_option_id(option_id: str) -> str | None:
    """``candidate_<id>`` → 裸 ``<id>``; 其它视为非法."""
    if not option_id or not option_id.startswith("candidate_"):
        return None
    return option_id[len("candidate_"):]


def handle_select_metadata_candidate(
    *,
    session: Session,
    config: AppConfig,  # noqa: ARG001 - 保留 config 接口, 后续可能用于 trigger 后续
    decision,
    option_id: str,
) -> SelectMetadataCandidateReplyResult:
    """把用户选中的候选固化为 ``user_decision`` MediaCandidate + 更新 task 字段.

    ``decision`` 是 ``_DecisionShim`` 或 ORM ``AgentDecisionRequest``,
    只需要 ``.options`` 属性 (list[dict]). 不会修改 decision.status,
    那个由 ``decision_reply`` 统一处理.
    """
    from media_pilot.repository.repositories import (
        IngestTaskRepository,
        MediaCandidateRepository,
    )

    raw_candidate_id = _parse_option_id(option_id)
    if raw_candidate_id is None:
        return SelectMetadataCandidateReplyResult(
            status=REPLY_STATUS_FAILED,
            reason="invalid_option_id",
        )

    candidate_repo = MediaCandidateRepository(session)
    chosen = candidate_repo.list_for_task(decision.task_id)
    chosen = next((c for c in chosen if c.id == raw_candidate_id), None)
    if chosen is None:
        return SelectMetadataCandidateReplyResult(
            status=REPLY_STATUS_FAILED,
            reason="candidate_not_found",
        )

    # 把用户选择落库为新 MediaCandidate, source="user_decision".
    # 保留完整 provider / title / year / confidence, 后续
    # fetch_and_save_metadata_detail / publish_* 工具只需消费最新候选.
    new_candidate = candidate_repo.add_candidate(
        task_id=decision.task_id,
        source="user_decision",
        media_type=chosen.media_type or "unknown",
        title=chosen.title,
        original_title=chosen.original_title,
        year=chosen.year,
        external_id=chosen.external_id,
        confidence=chosen.confidence,
        reason=(
            f"User selected {chosen.title or chosen.original_title or '(untitled)'} "
            f"from {chosen.source or 'unknown'}"
        ),
        payload={
            "decision_id": decision.id,
            "source_candidate_id": chosen.id,
            "overview": (chosen.payload or {}).get("overview"),
        },
    )

    # 同步更新 task 字段 — media_type 是后续 Agent 续跑的电影/剧集分流依据.
    task_repo = IngestTaskRepository(session)
    task = task_repo.get(decision.task_id)
    if task is not None:
        if not task.media_type or task.media_type == "unknown":
            task.media_type = chosen.media_type
        if not task.title:
            task.title = chosen.title or chosen.original_title
        if task.year is None and chosen.year is not None:
            task.year = chosen.year
        if chosen.confidence is not None:
            task.confidence = max(task.confidence or 0, chosen.confidence)
        session.flush()

    return SelectMetadataCandidateReplyResult(
        status=REPLY_STATUS_RECORDED,
        candidate_id=new_candidate.id,
        media_type=chosen.media_type,
        reason="recorded",
    )
