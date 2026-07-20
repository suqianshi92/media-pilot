"""Agent turn runner -- creates and executes a single AgentRun.

This is a manually-triggered service, NOT the default ingest entrypoint.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from media_pilot.agent.llm_client import AgentLLMClient, LLMConfigurationError, LLMResponse
from media_pilot.agent.prompts import (
    AUTO_INGEST_SYSTEM_PROMPT,
    FREEFORM_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    make_initial_user_message,
)
from media_pilot.agent.tool_schema import get_allowed_tool_names, get_allowed_tool_schemas
from media_pilot.agent.tools.base import ToolContext
from media_pilot.agent.tools.registry import get_tool_registry, register_builtin_tools
from media_pilot.config import AppConfig
from media_pilot.services.auto_ingest import (  # noqa: E402  (used in safety net; tests monkeypatch this symbol)
    fetch_and_save_metadata_detail,
)

MAX_STEPS = 15
MAX_TOOL_FAILURES = 3

logger = logging.getLogger(__name__)


# 工具输出 LLM 上下文裁剪阈值.
# overview / payload / cast / images 等大字段会被截到 TOOL_OUTPUT_*_MAX
# 字符, 既避免长 token 灌爆上下文, 又保留 LLM 续跑所需的关键字.
TOOL_OUTPUT_STR_MAX = 240
TOOL_OUTPUT_OVERVIEW_MAX = 200
TOOL_OUTPUT_LIST_MAX_ITEMS = 10
TOOL_OUTPUT_PAYLOAD_MAX = 320


def _commit_before_llm_call(session: Session, *, run_id: str, step: int) -> None:
    """Commit pending Agent progress before waiting on an LLM network call.

    SQLite only allows one writer at a time. The runner creates messages,
    tool-call rows, and task/run status updates before each model call; keeping
    those writes uncommitted while the LLM request is in flight turns a network
    wait into a long SQLite write transaction. Commit here to make the model
    call happen outside a write transaction.
    """
    try:
        session.commit()
    except OperationalError:
        logger.warning(
            "Agent run %s step %s commit-before-LLM hit OperationalError",
            run_id, step,
        )
        try:
            session.rollback()
        except Exception:
            logger.exception(
                "Agent run %s rollback failed after commit-before-LLM",
                run_id,
            )
        raise


def _commit_before_tool_execution(
    session: Session, *, run_id: str, tool_name: str,
) -> None:
    """Commit assistant/tool-call records before executing a tool.

    Tool handlers may perform slow network or filesystem work. Persist the
    assistant message and the ``running`` AgentToolCall first so that the slow
    side effect does not hold a SQLite write transaction open.
    """
    try:
        session.commit()
    except OperationalError:
        logger.warning(
            "Agent run %s commit-before-tool failed for %s",
            run_id, tool_name,
        )
        try:
            session.rollback()
        except Exception:
            logger.exception(
                "Agent run %s rollback failed after commit-before-tool",
                run_id,
            )
        raise


def _tool_output_for_llm(
    output: dict | None,
    tool_name: str | None = None,
) -> dict:
    """把 AgentToolCall.output 折叠成给 LLM 看的紧凑结构.

    设计原则:
    - status / summary 透传, 让 LLM 知道工具成功 / 失败.
    - best_candidate 完整保留 candidate_id / provider / provider_id /
      media_type / title / year / confidence (后续 fetch / publish 工具
      依赖这些字段).
    - candidates[] 简化为 provider / provider_id / media_type / title /
      year / confidence / candidate_id 几项, 不灌 overview / payload.
    - candidates[] 列表截到 TOOL_OUTPUT_LIST_MAX_ITEMS 条, 避免 LLM
      重新搜索走老路.
    - data 顶层其余字段按字符串 / 数字透传, 字符串超长截到
      TOOL_OUTPUT_STR_MAX, list 截到 TOOL_OUTPUT_LIST_MAX_ITEMS,
      dict 折叠为 {"keys": [...], "truncated": bool} 防止大 payload
      灌爆上下文.

    runner 把这份结果作为 role=tool 消息的 content 发给 LLM;
    AgentMessage.content 仍是 UI 友好的短 summary, 二者解耦.
    """

    if not isinstance(output, dict):
        return {}

    compact: dict = {}
    status_value = output.get("status")
    if isinstance(status_value, str):
        compact["status"] = status_value
    summary = output.get("summary")
    if isinstance(summary, str):
        compact["summary"] = summary

    data = output.get("data")
    if not isinstance(data, dict):
        return compact

    # 关键字段优先保留. 任何带 candidate_id / provider_id 的字段都
    # 是后续 fetch / publish 工具要消费的, 必须原样透传.
    if "best_candidate" in data and isinstance(data["best_candidate"], dict):
        compact["best_candidate"] = data["best_candidate"]

    if "candidates" in data and isinstance(data["candidates"], list):
        slim: list[dict] = []
        for c in data["candidates"][:TOOL_OUTPUT_LIST_MAX_ITEMS]:
            if not isinstance(c, dict):
                continue
            slim.append({
                k: c[k]
                for k in (
                    "candidate_id", "provider", "provider_id",
                    "external_id", "media_type", "title", "year",
                    "confidence",
                )
                if k in c
            })
        compact["candidates"] = slim
        if len(data["candidates"]) > TOOL_OUTPUT_LIST_MAX_ITEMS:
            compact["candidates_truncated"] = True

    # 其余顶层字段按基础类型透传, 字符串 / 列表长度受限.
    for key, value in data.items():
        if key in {"best_candidate", "candidates"}:
            continue
        if isinstance(value, str):
            compact[key] = (
                value[:TOOL_OUTPUT_STR_MAX] + "..."
                if len(value) > TOOL_OUTPUT_STR_MAX
                else value
            )
        elif isinstance(value, (int, float, bool)) or value is None:
            compact[key] = value
        elif isinstance(value, list):
            compact[key] = value[:TOOL_OUTPUT_LIST_MAX_ITEMS]
            if len(value) > TOOL_OUTPUT_LIST_MAX_ITEMS:
                compact[f"{key}_truncated"] = True
        elif isinstance(value, dict):
            # 折叠 dict 为 keys 列表, 避免大 payload 灌爆.
            try:
                keys = list(value.keys())
            except Exception:
                keys = []
            compact[key] = {
                "keys": keys[:TOOL_OUTPUT_LIST_MAX_ITEMS],
                "size": len(keys),
                "truncated": len(keys) > TOOL_OUTPUT_LIST_MAX_ITEMS,
            }
        # 其它类型 (tuple / set 等) 跳过, LLM 上下文里不需要.

    return compact


def _auto_publish_if_metadata_ready(
    *,
    session: Session,
    config: AppConfig,
    run,
    task,
    task_repo,
    registry,
    mode: str,
) -> bool:
    """auto_ingest final text 安全网: 当任务 metadata detail 已落库但
    LLM 在 final text 收口前没调 publish_*_to_library, 主动调一次.

    恢复条件:
    - mode == "auto_ingest" (default / freeform 不强制 auto-publish)
    - task.status == "agent_running" (工具没把 task 推到 business 终态)
    - 已有 succeeded / warning WriteResult 时只恢复 task 终态, 不重复 publish

    自动发布条件:
    - 满足以上 mode / task.status 条件
    - MetadataDetail 已存在 (fetch_and_save_metadata_detail 成功过)
    - task.media_type 在 movie / show

    返回: True 表示已自动 publish; False 表示条件不满足, runner 走
    原 final text 收口路径.
    """
    from media_pilot.repository.repositories import (
        MetadataDetailRepository,
        WriteResultRepository,
    )

    if mode != "auto_ingest":
        return False
    if task.status != "agent_running":
        return False
    write_result = WriteResultRepository(session).get_for_task(task.id)
    if write_result is not None and write_result.status in ("succeeded", "warning"):
        task_repo.update_status(
            task,
            status="library_import_complete",
            current_step="library_import_complete",
        )
        logger.warning(
            "auto_ingest final-text safety net: restored published task %s "
            "from write_result=%s",
            task.id,
            write_result.status,
        )
        return True
    if task.media_type not in ("movie", "show"):
        return False
    if task.status == "library_import_complete":
        return False

    detail_repo = MetadataDetailRepository(session)
    if detail_repo.get_for_task(task.id) is None:
        return False

    tool_name = (
        "publish_movie_to_library"
        if task.media_type == "movie"
        else "publish_show_to_library"
    )
    try:
        tool_context = ToolContext(
            session=session, config=config,
            task_id=task.id, run_id=run.id,
        )
        result = registry.execute(
            tool_name, tool_context, {"task_id": task.id},
        )
        if result.status == "success":
            logger.info(
                "auto_ingest final-text safety net: %s succeeded for task %s",
                tool_name, task.id,
            )
            return True
        task_repo.update_status(
            task,
            status="agent_failed",
            current_step="agent_failed",
            failure_reason="auto_publish_after_final_text_failed",
        )
        from media_pilot.repository.repositories import AgentRunRepository
        AgentRunRepository(session).update_status(
            run,
            status="failed",
            current_step="agent_failed",
            error_message=(
                f"Auto-publish after final text failed: {result.summary}"
            ),
        )
        logger.info(
            "auto_ingest final-text safety net: %s returned %s for task %s: %s",
            tool_name, result.status, task.id, result.summary,
        )
        return True
    except Exception:
        logger.exception(
            "auto_ingest final-text safety net: %s raised for task %s",
            tool_name, task.id,
        )
    return False


def _check_post_completion_safety_net(
    *,
    session: Session,
    config: AppConfig,
    run,
    task,
    task_repo,
    registry,
    mode: str = "default",
) -> bool:
    """final-text 收口后的 rescue 安全网.

    MP-Lab-02-Matrix-1999-Dominant 现场: LLM 在 final text 收口前没调
    `fetch_and_save_metadata_detail` (可能调了只读 `draft_metadata_replacement`),
    而 `_auto_publish_if_metadata_ready` 只在 MetadataDetail 已落库时才
    主动 publish — 这条路径下 MetadataDetail 是空的, 安全网什么都不做,
    任务永久卡在 `run.status=completed` + `task.status=agent_running` +
    无 metadata + 无 asset 的自相矛盾状态.

    这条安全网是补救:
    - 条件: `task.status="agent_running"` ∧
      `MetadataDetail is None` ∧
      `WriteResultRepository.get_for_task(task.id) is None` (无主写
      记录) ∧ `FileAsset` 表对 task.id 为空 (无 asset).
    - 满足: 取最新 `MediaCandidate` (任意 source) 作为 fetch 依据,
      调 `apply_user_metadata_choice` 走确定性 publish 序列.
      - 成功 → task 进入 `library_import_complete` (或
        `target_conflict` 等待用户).
      - 失败 → task 进入 `agent_failed` + run 改回 `failed` + 显式
        `failure_reason="no_metadata_detail_after_agent_completion"`.
      - 没有候选 → 同样标 `agent_failed`.

    返回: True 表示已处理; False 表示条件不满足 (没矛盾状态) —
    runner 继续走原 completed 收口路径.
    """
    from media_pilot.repository.models import FileAsset
    from media_pilot.repository.repositories import (
        MediaCandidateRepository,
        MetadataDetailRepository,
        WriteResultRepository,
    )
    from sqlalchemy import select as _sa_select
    from media_pilot.services.select_metadata_publish import (
        OUTCOME_AGENT_FAILED,
        OUTCOME_LIBRARY_IMPORT_COMPLETE,
        OUTCOME_TARGET_CONFLICT,
        apply_user_metadata_choice,
    )

    # 只在 auto_ingest 模式下生效. freeform chat / default 模式
    # 是用户和 LLM 聊天, task 不期待被 publish, 不该走这条 rescue.
    if mode != "auto_ingest":
        return False

    if task.status != "agent_running":
        return False

    detail_repo = MetadataDetailRepository(session)
    if detail_repo.get_for_task(task.id) is not None:
        return False  # 已有 metadata, 由 _auto_publish_if_metadata_ready 接管.

    write_repo = WriteResultRepository(session)
    if write_repo.get_for_task(task.id) is not None:
        return False  # 已有写入记录, 任务已成功 publish 完了, 不再补.

    file_assets = session.scalars(
        _sa_select(FileAsset).where(FileAsset.task_id == task.id)
    ).all()
    if file_assets:
        return False  # 已有 asset, 任务已落库.

    # 矛盾状态: task.agent_running + 无 metadata + 无 write + 无 asset.
    # 试图从 MediaCandidate 列表恢复. 选取规则 (按优先级):
    #   1. 优先 ``source == "user_decision"`` 的候选 ——
    #      来自 ``handle_select_metadata_candidate`` 在用户回复时落库
    #      的强事实, 一旦存在就代表用户已经明确选了某条元数据, 安
    #      全网必须用它, 不得 fallback 到 agent 自动搜索产物.
    #   2. 否则按 ``created_at desc`` 取最新一条 ——
    #      ``list_for_task`` 默认 ``created_at asc``, 反转一下取 [0]
    #      即最新. 最新候选代表 agent 最后的搜索 / persist 决定,
    #      安全性优于最早的 (list_for_task[0] 是最早, 可能是 stale).
    #   3. 没有候选 —— return False, 让 runner 走原 completed 收口.
    candidate_repo = MediaCandidateRepository(session)
    candidates = candidate_repo.list_for_task(task.id)
    if not candidates:
        # 没有候选可救 — agent 还没进展到 persist_metadata_selection 阶段,
        # 任务可能是 junk 文件或 LLM 刚开始. 不武断标 failed, 让 runner
        # 走正常 completed 收口 (task 仍 agent_running, 等待人工或新一轮).
        # production "Matrix" bug 场景一定有 candidate, 这里 return False
        # 不会漏掉真实 stuck case.
        return False

    user_decision_candidates = [c for c in candidates if c.source == "user_decision"]
    if user_decision_candidates:
        # 取最新的 user_decision (created_at asc 列表里最后一条即最新).
        latest = user_decision_candidates[-1]
    else:
        # 没有 user_decision, 用最新候选 (created_at desc).
        latest = candidates[-1]

    # 解析真实 provider_name. ``user_decision`` 候选把 source 标成
    # "user_decision" (不是真 provider), 真实 provider 必须从
    # ``payload.source_candidate_id`` 链接到的原候选取. 其它情况
    # (source="tmdb" / "tpdb" / "preselected") 直接用 latest.source,
    # 兜底默认 "tmdb".
    real_provider = latest.source or "tmdb"
    if latest.source == "user_decision":
        ud_payload = latest.payload if isinstance(latest.payload, dict) else {}
        source_candidate_id = ud_payload.get("source_candidate_id")
        if not source_candidate_id:
            # 防御: user_decision 候选没记 source_candidate_id 链接, 无
            # 法反查真 provider. 强行 rescue 会拿 "user_decision" 字面量
            # 调 fetch_metadata_draft, 立刻失败 → 任务被误标 agent_failed.
            # 保守放弃 rescue, 让 runner 走原 completed 收口, 至少任务
            # 状态 (agent_running) 留有现场, 等待人工介入.
            logger.warning(
                "post-completion safety net: user_decision candidate %s "
                "missing source_candidate_id, skipping rescue",
                latest.id,
            )
            return False
        from media_pilot.repository.models import MediaCandidate as _MC
        original = session.get(_MC, source_candidate_id)
        if original is None:
            # 同上: 链接断了, 不强救.
            logger.warning(
                "post-completion safety net: user_decision candidate %s "
                "links to missing source_candidate_id=%s, skipping rescue",
                latest.id, source_candidate_id,
            )
            return False
        if original.source not in ("user_decision", "preselected", None):
            real_provider = original.source
        elif original.source == "preselected":
            original_payload = (
                original.payload if isinstance(original.payload, dict) else {}
            )
            real_provider = (
                original_payload.get("preselected_provider")
                or original.source
                or "tmdb"
            )

    fetch_result = fetch_and_save_metadata_detail(
        session=session, config=config,
        task_id=task.id,
        provider_name=real_provider,
        provider_id=latest.external_id or "",
        media_type=latest.media_type or "movie",
    )
    if fetch_result.status != "success":
        task_repo.update_status(
            task, status="agent_failed",
            current_step="agent_failed",
            failure_reason="no_metadata_detail_after_agent_completion",
        )
        from media_pilot.repository.repositories import AgentRunRepository
        AgentRunRepository(session).update_status(
            run, status="failed",
            current_step="agent_failed",
            error_message=(
                f"Post-completion safety net fetch failed: "
                f"{fetch_result.summary}"
            ),
        )
        return True

    # 调 publish_*_to_library
    from media_pilot.agent.tools.base import ToolContext
    tool_name = (
        "publish_movie_to_library"
        if (latest.media_type or "movie") == "movie"
        else "publish_show_to_library"
    )
    tool_context = ToolContext(
        session=session, config=config,
        task_id=task.id, run_id=run.id,
    )
    try:
        tool_result = registry.execute(
            tool_name, tool_context, {"task_id": task.id},
        )
    except Exception as exc:
        tool_result = None
        publish_error = str(exc)
    else:
        publish_error = None

    if tool_result is not None and tool_result.status == "success":
        # 工具已把 task 推到 library_import_complete.
        task_repo.update_status(
            task, status="library_import_complete",
            current_step="library_import_complete",
        )
        logger.info(
            "post-completion safety net: published task %s via %s",
            task.id, tool_name,
        )
        return True

    # publish 失败 / 抛异常 → agent_failed.
    task_repo.update_status(
        task, status="agent_failed",
        current_step="agent_failed",
        failure_reason="no_metadata_detail_after_agent_completion",
    )
    from media_pilot.repository.repositories import AgentRunRepository
    AgentRunRepository(session).update_status(
        run, status="failed",
        current_step="agent_failed",
        error_message=(
            f"Post-completion safety net publish failed: "
            f"{getattr(tool_result, 'summary', publish_error) or 'unknown'}"
        ),
    )
    logger.warning(
        "post-completion safety net: publish failed for task %s",
        task.id,
    )
    return True


def _has_successful_search_history(tc_repo, run_id: str) -> bool:
    """检查本 run 是否有过成功的 ``search_metadata`` 调用.

    与 ``_handle_prepare_select_metadata_candidate_decision`` 里的历史回
    收逻辑一致: 主契约 ``tc.status == "completed"`` + ``output.status ==
    "success"``; 兼容 ``tc.status == "succeeded"`` (legacy).
    """
    for tc in tc_repo.list_by_run(run_id):
        if tc.tool_name != "search_metadata" or not tc.output:
            continue
        output = tc.output if isinstance(tc.output, dict) else {}
        is_success = (
            (tc.status == "completed" and output.get("status") == "success")
            or tc.status == "succeeded"
        )
        if is_success:
            return True
    return False


def _recover_via_candidate_decision(
    *,
    session: Session,
    config: AppConfig,
    run,
    task,
    registry,
) -> AgentRunResult | None:
    """max_steps 前最后一道安全网: 把成功的 search_history 收敛到
    ``prepare_select_metadata_candidate_decision`` 工具.

    调用方 (max_steps handler) 已确认:
    - ``mode == "auto_ingest"``
    - ``task.status == "agent_running"``
    - 本 run 有成功 search_metadata 历史 (有 ``candidates``)

    关键约束 (与 spec 一致):
    - 不调 LLM, 直接用 registry 调 ``prepare_select_metadata_candidate_decision``.
    - 不传 keyword / provider / media_type — 工具层会自动从本 run 最近一次
      成功 ``search_metadata`` 的 ``tc.output.data`` 恢复 candidates.
    - 若恢复失败 (没有有效 candidates), 必须**不合成**, 直接返回 ``None``
      让上层走标准 max_steps 失败路径, 不创造虚假 candidate.

    返回 ``None`` 表示"无法恢复", 调用方继续走 max_steps 失败路径; 返回
    ``AgentRunResult`` 表示安全网已处理完成.
    """
    from media_pilot.repository.repositories import (
        AgentMessageRepository,
        AgentRunRepository,
    )

    tool_name = "prepare_select_metadata_candidate_decision"
    tool_context = ToolContext(
        session=session, config=config,
        task_id=task.id, run_id=run.id,
    )
    try:
        tool_result = registry.execute(
            tool_name, tool_context, {"task_id": task.id},
        )
    except Exception as exc:
        logger.warning(
            "search-loop safety net: %s raised for task %s: %s",
            tool_name, task.id, exc,
        )
        return None

    data = tool_result.data if isinstance(tool_result.data, dict) else {}

    msg_count = len(AgentMessageRepository(session).list_by_run(run.id))
    run_repo = AgentRunRepository(session)

    # auto_confirm: 工具已确认唯一候选, 把 run 标 completed 收口,
    # 不让任务停留在 max_steps-exceeded 状态. 任务推进由下一轮 Agent
    # 续跑或人工触发接管.
    if data.get("auto_confirm"):
        run_repo.update_status(
            run, status="completed",
            current_step="auto_confirmed_via_safety_net",
        )
        return AgentRunResult(
            run_id=run.id,
            status="completed",
            message_count=msg_count,
            tool_call_count=0,
            error_message=None,
        )

    # decision_requested: 候选决策卡已创建, run 进入 waiting_user.
    # 这条路径上 LLM 错误地把 search_metadata 当成"完成"而忘了
    # prepare_select_metadata_candidate_decision 的死循环被打破,
    # 用户可以正常选候选 / 确认.
    if data.get("decision_requested"):
        if run.status != "waiting_user":
            decision_type = data.get("decision_type") or "select_metadata_candidate"
            run_repo.update_status(
                run, status="waiting_user",
                current_step=str(decision_type),
            )
        return AgentRunResult(
            run_id=run.id,
            status="waiting_user",
            message_count=msg_count,
            tool_call_count=0,
            error_message=None,
        )

    # 工具返回 failure / no candidates / recovered_output_invalid —
    # 安全网无候选可合成, 必须放弃, 退回标准 max_steps 失败路径.
    return None


@dataclass(frozen=True, kw_only=True)
class AgentRunResult:
    run_id: str
    status: str
    message_count: int
    tool_call_count: int
    error_message: str | None = None


def _run_agent_loop(
    *,
    session: Session,
    config: AppConfig,
    run,
    task,
    msg_repo,
    run_repo,
    tc_repo,
    task_repo,
    registry,
    allowed_tools,
    mode: str = "default",
    mock_llm_client: AgentLLMClient | None = None,
    previous_status: str | None = None,
    previous_step: str | None = None,
    llm_context: str | None = None,
    stream_emitter=None,
) -> AgentRunResult:
    """Core LLM tool-calling loop shared by new runs and continued runs."""

    from media_pilot.repository.repositories import AgentMessageCreate, AgentToolCallCreate

    if mode == "auto_ingest":
        from media_pilot.agent.prompts import build_auto_ingest_system_prompt
        system_prompt = build_auto_ingest_system_prompt(config)
    elif mode == "freeform":
        system_prompt = FREEFORM_SYSTEM_PROMPT
    else:
        system_prompt = SYSTEM_PROMPT

    step = 0
    tool_failure_count = 0
    tool_call_count = 0

    try:
        llm = mock_llm_client or AgentLLMClient(config)
        while step < MAX_STEPS:
            step += 1
            # 安全网: 上一轮的工具已经把 run 切到 failed, 上一轮 (step-1)
            # 已经给了 LLM 一次 final text / final tool_call 解释机会. 任何
            # step > 2 的轮次如果 run 仍是 failed, 直接收口, 不再调 LLM —
            # 避免 LLM 持续返回 tool_call 形成循环, 也避免 LLM 试图调用
            # search_metadata / publish_movie_to_library 等写入型工具,
            # 破坏 "failed 是本轮执行终止信号" 的语义.
            if run.status == "failed" and step > 2:
                return AgentRunResult(
                    run_id=run.id,
                    status="failed",
                    message_count=len(msg_repo.list_by_run(run.id)),
                    tool_call_count=tool_call_count,
                    error_message=run.error_message,
                )
            # 已 failed 的 run (例如 prepare_complex_input_decision 命中
            # no_videos / unsafe_path / scan_failed) 不得被下一轮重置为
            # active — 这会让 final text 路径再覆盖一次, 任务从 agent_failed
            # 看似变回成功. 保留 failed 状态直到 final text 收口.
            #
            # 已经是 active 的 run 不再每轮强行写 step_N: 诊断字段不值得
            # 为每次 LLM 调用制造一次 UPDATE/flush 写锁. 从 waiting_user
            # 恢复时仍需切 active, 然后在 LLM 调用前提交释放写事务.
            if run.status not in ("failed", "active"):
                run_repo.update_status(
                    run, status="active", current_step=f"step_{step}",
                )

            _commit_before_llm_call(session, run_id=run.id, step=step)

            # Build messages for LLM
            history = msg_repo.list_by_run(run.id)
            # 一次性加载本 run 全部 tool_call, 按 tool_call_id 建索引,
            # 给后续 role=tool 消息的 content 注入 compact 输出.
            # AgentMessage.content 保留 UI 友好的短 summary, 不会被改;
            # LLM 看到的 content 是基于 AgentToolCall.output 折叠的紧凑
            # JSON, 包含 status / summary / 关键 data 字段.
            tool_call_index: dict[str, dict] = {}
            for tc in tc_repo.list_by_run(run.id):
                if tc.tool_call_id:
                    tool_call_index[tc.tool_call_id] = tc.output or {}
            llm_messages: list[dict] = [{"role": "system", "content": system_prompt}]
            # Inject task-level context for freeform runs (not persisted as user-visible messages)
            if llm_context:
                llm_messages.append({"role": "system", "content": llm_context})
            for m in history:
                msg_dict: dict = {"role": m.role}
                if m.role == "tool" and m.tool_call_id and m.tool_call_id in tool_call_index:
                    compact = _tool_output_for_llm(
                        tool_call_index[m.tool_call_id], m.tool_name,
                    )
                    msg_dict["content"] = json.dumps(
                        compact, ensure_ascii=False,
                    )
                elif m.content is not None:
                    msg_dict["content"] = m.content
                if m.tool_calls is not None:
                    msg_dict["tool_calls"] = m.tool_calls
                if m.tool_call_id is not None:
                    msg_dict["tool_call_id"] = m.tool_call_id
                if m.tool_name is not None:
                    msg_dict["name"] = m.tool_name
                llm_messages.append(msg_dict)

            # ── call LLM ──────────────────────────────────────────
            if stream_emitter is not None:
                # ── streaming path: collect deltas, emit events ──
                from media_pilot.agent.sse import AgentStreamEvent, AgentStreamEventType
                accumulated_content = ""
                final_tool_calls = None
                for delta in llm.chat_stream(llm_messages, tools=allowed_tools):
                    if delta.get("content_delta"):
                        accumulated_content += delta["content_delta"]
                        stream_emitter.emit(AgentStreamEvent(
                            event=AgentStreamEventType.ASSISTANT_DELTA,
                            data={"delta": delta["content_delta"]},
                        ))
                    if delta.get("tool_calls") is not None:
                        final_tool_calls = delta["tool_calls"]

                # Emit final assistant message
                stream_emitter.emit(AgentStreamEvent(
                    event=AgentStreamEventType.ASSISTANT_MESSAGE,
                    data={"content": accumulated_content or None},
                ))

                # ── final text response (no tool calls) ────────────
                if not final_tool_calls:
                    msg_repo.create(AgentMessageCreate(
                        run_id=run.id, role="assistant", content=accumulated_content or "",
                    ))
                    # 工具在本轮把 run 设为 failed (例如 prepare_complex_input_decision
                    # 命中 no_videos / unsafe_path / scan_failed, 或 max_tool_failures
                    # 路径) 时, runner 的 final text 不得覆盖为 completed — 这会让
                    # 任务从 agent_failed 看似变回成功, 与 task.status 不一致. 已 failed
                    # 的 run 直接以 failed 收口, 不动 run.status.
                    if run.status == "failed":
                        if stream_emitter is not None:
                            stream_emitter.emit(AgentStreamEvent(
                                event=AgentStreamEventType.RUN_FINISHED,
                                data={"status": "failed"},
                            ))
                            stream_emitter.close()
                        return AgentRunResult(
                            run_id=run.id,
                            status="failed",
                            message_count=len(msg_repo.list_by_run(run.id)),
                            tool_call_count=tool_call_count,
                            error_message=run.error_message,
                        )
                    # auto_ingest final-text safety net: LLM 在 final text
                    # 收口前没调 publish_*_to_library 但 MetadataDetail 已
                    # 落库, 主动调一次推进 task 到 library_import_complete
                    # / waiting_user 等可见态, 避免 agent_running 卡死.
                    _auto_publish_if_metadata_ready(
                        session=session, config=config, run=run, task=task,
                        task_repo=task_repo, registry=registry, mode=mode,
                    )
                    # post-completion safety net (NEW): 防止
                    # `run=completed` + `task=agent_running` + 无
                    # metadata / write / asset 的自相矛盾状态落地.
                    # 兜底: 取最新 MediaCandidate 走确定性 fetch +
                    # publish; 仍失败 → task=agent_failed / run=failed.
                    _check_post_completion_safety_net(
                        session=session, config=config, run=run, task=task,
                        task_repo=task_repo, registry=registry, mode=mode,
                    )
                    # 如果 safety net 已处理 (并可能把 run 标 failed),
                    # 不要无条件覆盖为 completed.
                    if run.status not in ("failed", "waiting_user"):
                        run_repo.update_status(
                            run, status="completed", current_step="completed",
                        )
                    if mode == "freeform" and previous_status and tool_call_count == 0:
                        task_repo.update_status(
                            task, status=previous_status,
                            current_step=previous_step or "completed",
                        )
                    # tool_call_count > 0: task lifecycle is owned by tools/services;
                    # runner must not overwrite task.status/current_step.
                    stream_emitter.emit(AgentStreamEvent(
                        event=AgentStreamEventType.RUN_FINISHED,
                        data={"status": run.status},
                    ))
                    stream_emitter.close()
                    return AgentRunResult(
                        run_id=run.id,
                        status=run.status,
                        message_count=len(msg_repo.list_by_run(run.id)),
                        tool_call_count=tool_call_count,
                    )

                response = LLMResponse(
                    content=accumulated_content or None,
                    tool_calls=final_tool_calls,
                )
            else:
                response = llm.chat(llm_messages, tools=allowed_tools)

            # ── final text response (no tool calls) ───────────────
            # (non-streaming path only; streaming path handled above)
            if not response.tool_calls and stream_emitter is None:
                assistant_content = response.content or ""
                msg_repo.create(AgentMessageCreate(
                    run_id=run.id, role="assistant", content=assistant_content,
                ))
                # 工具在本轮把 run 设为 failed (例如 prepare_complex_input_decision
                # 命中 no_videos / unsafe_path / scan_failed) 时, runner 的 final
                # text 不得覆盖为 completed. 已 failed 的 run 直接以 failed 收口.
                if run.status == "failed":
                    return AgentRunResult(
                        run_id=run.id,
                        status="failed",
                        message_count=len(msg_repo.list_by_run(run.id)),
                        tool_call_count=tool_call_count,
                        error_message=run.error_message,
                    )
                # auto_ingest final-text safety net: LLM 在 final text
                # 收口前没调 publish_*_to_library 但 MetadataDetail 已
                # 落库, 主动调一次推进 task 到 library_import_complete
                # / waiting_user 等可见态, 避免 agent_running 卡死.
                _auto_publish_if_metadata_ready(
                    session=session, config=config, run=run, task=task,
                    task_repo=task_repo, registry=registry, mode=mode,
                )
                # post-completion safety net (NEW): 防止
                # `run=completed` + `task=agent_running` + 无
                # metadata / write / asset 的自相矛盾状态落地.
                # 兜底: 取最新 MediaCandidate 走确定性 fetch +
                # publish; 仍失败 → task=agent_failed / run=failed.
                _check_post_completion_safety_net(
                    session=session, config=config, run=run, task=task,
                    task_repo=task_repo, registry=registry, mode=mode,
                )
                # 如果 safety net 已处理 (并可能把 run 标 failed),
                # 不要无条件覆盖为 completed.
                if run.status not in ("failed", "waiting_user"):
                    run_repo.update_status(
                        run, status="completed", current_step="completed",
                    )
                # Freeform chat-only: restore previous business status
                if mode == "freeform" and previous_status and tool_call_count == 0:
                    task_repo.update_status(
                        task, status=previous_status,
                        current_step=previous_step or "completed",
                    )
                # tool_call_count > 0: task lifecycle is owned by tools/services;
                # runner must not overwrite task.status/current_step.
                return AgentRunResult(
                    run_id=run.id,
                    status=run.status,
                    message_count=len(msg_repo.list_by_run(run.id)),
                    tool_call_count=tool_call_count,
                )

            # ── 硬失败后的 tool_call 防御 ───────────────────────────
            # run.status 已是 failed (来自上一轮工具), LLM 这一轮仍返回
            # tool_calls. 不得执行任何工具 — search_metadata /
            # persist_metadata_selection / publish_movie_to_library 等写入
            # 工具绝不能在已失败 run 上跑出副作用. 持久化一条 assistant
            # message 保留解释意图, 给每个 tool_call 写一条 tool message
            # 拒绝说明, 然后以 failed 收口. LLM 下一轮 (step > 2) 会被
            # 循环顶部安全网直接拦下, 不会再调 LLM.
            if run.status == "failed" and response.tool_calls:
                from media_pilot.agent.sse import AgentStreamEvent, AgentStreamEventType

                dropped_names = [
                    tc.get("function", {}).get("name", "")
                    for tc in response.tool_calls
                ]
                reason = run.error_message or "AgentRun already in failed state"
                msg_repo.create(AgentMessageCreate(
                    run_id=run.id,
                    role="assistant",
                    content=(
                        f"AgentRun is already in failed state ({reason}). "
                        f"Skipping requested tool calls: {dropped_names}. "
                        "No further tools will be executed for this run."
                    ),
                    tool_calls=response.tool_calls,
                ))
                for tc_data in response.tool_calls:
                    tc_id = tc_data.get("id", "")
                    fn_info = tc_data.get("function", {})
                    tool_name = fn_info.get("name", "")
                    msg_repo.create(AgentMessageCreate(
                        run_id=run.id,
                        role="tool",
                        content=json.dumps({
                            "status": "failure",
                            "summary": (
                                f"Tool '{tool_name}' not executed: "
                                f"AgentRun already in failed state ({reason})."
                            ),
                        }, ensure_ascii=False),
                        tool_call_id=tc_id,
                        tool_name=tool_name,
                    ))
                if stream_emitter is not None:
                    stream_emitter.emit(AgentStreamEvent(
                        event=AgentStreamEventType.RUN_FINISHED,
                        data={"status": "failed"},
                    ))
                    stream_emitter.close()
                return AgentRunResult(
                    run_id=run.id,
                    status="failed",
                    message_count=len(msg_repo.list_by_run(run.id)),
                    tool_call_count=tool_call_count,
                    error_message=reason,
                )

            # ── assistant message with tool_calls ──────────────────
            assistant_msg = msg_repo.create(AgentMessageCreate(
                run_id=run.id,
                role="assistant",
                content=response.content,
                tool_calls=response.tool_calls,
            ))

            # ── execute tool calls ─────────────────────────────────
            decision_requested = False
            pause_step_hint: str | None = None
            tool_context = ToolContext(
                session=session, config=config, task_id=task.id, run_id=run.id,
            )

            for tc_data in response.tool_calls:
                tool_call_count += 1
                fn_info = tc_data.get("function", {})
                tool_name = fn_info.get("name", "")
                tc_id = tc_data.get("id", "")

                # ── 同批次执行终止信号 ───────────────────────────
                # 之前的工具已经触发了本批次的终止条件:
                # - run.status == "failed" (例如 prepare_complex_input_decision
                #   命中 no_videos / unsafe_path / scan_failed /
                #   review_user_note_already_consumed, 或 max_tool_failures)
                # - decision_requested == True (某工具已创建 pending
                #   AgentDecisionRequest)
                # 后续 tool_call 不得执行 — 与 8ba1ef9 的"下一轮 LLM
                # tool_call 丢弃"语义保持一致: failed / decision_requested
                # 都是执行终止信号. 写一条 tool "not executed" 拒绝说明
                # 让 LLM 在下一轮看到原因, 跳出本批次 for 循环.
                if run.status == "failed" or decision_requested:
                    if run.status == "failed":
                        reason = (
                            run.error_message
                            or "AgentRun already in failed state"
                        )
                        skip_msg = (
                            f"Tool '{tool_name}' not executed: "
                            f"AgentRun already in failed state ({reason})."
                        )
                    else:
                        skip_msg = (
                            f"Tool '{tool_name}' not executed: "
                            "another tool in this batch already created a "
                            "pending AgentDecisionRequest; subsequent tools "
                            "are paused."
                        )
                    msg_repo.create(AgentMessageCreate(
                        run_id=run.id,
                        role="tool",
                        content=json.dumps({
                            "status": "failure",
                            "summary": skip_msg,
                        }, ensure_ascii=False),
                        tool_call_id=tc_id,
                        tool_name=tool_name,
                    ))
                    if stream_emitter is not None:
                        stream_emitter.emit(AgentStreamEvent(
                            event=AgentStreamEventType.TOOL_CALL_FINISHED,
                            data={
                                "tool_call_id": tc_id,
                                "tool_name": tool_name,
                                "status": "skipped",
                                "summary": skip_msg,
                            },
                        ))
                    # 继续遍历剩余 tool_call: OpenAI/DeepSeek 协议要求
                    # assistant message 中每一个 tool_call_id 都必须有一条
                    # 对应 tool message. 不能 break, 否则历史消息再次提交
                    # 给 LLM 时会触发 invalid_request_error.
                    continue

                # Parse arguments
                try:
                    args = json.loads(fn_info.get("arguments", "{}"))
                except json.JSONDecodeError:
                    args = {}

                # Create tool call record
                tc = tc_repo.create(AgentToolCallCreate(
                    run_id=run.id,
                    tool_name=tool_name,
                    input=args,
                    message_id=assistant_msg.id,
                    tool_call_id=tc_id or None,
                    status="running",
                ))
                t_start = time.monotonic()
                _commit_before_tool_execution(
                    session, run_id=run.id, tool_name=tool_name,
                )

                # Execute via registry (only if tool is in allowed set for current mode)
                from media_pilot.agent.sse import AgentStreamEvent, AgentStreamEventType
                if stream_emitter is not None:
                    stream_emitter.emit(AgentStreamEvent(
                        event=AgentStreamEventType.TOOL_CALL_STARTED,
                        data={"tool_call_id": tc_id, "tool_name": tool_name},
                    ))

                allowed_names = get_allowed_tool_names(registry, mode=mode)
                try:
                    if tool_name not in allowed_names:
                        raise ValueError(
                            f"Tool '{tool_name}' is not in the allowed tool set for mode '{mode}'"
                        )
                    registry.validate_input(tool_name, args)
                    tool_result = registry.execute(tool_name, tool_context, args)
                except (ValueError, KeyError) as exc:
                    from media_pilot.agent.tools.base import ToolResult
                    tool_result = ToolResult(
                        status="failure",
                        summary=f"Tool execution error: {exc}",
                    )

                duration_ms = int((time.monotonic() - t_start) * 1000)

                if tool_result.status == "failure":
                    tool_failure_count += 1

                tc_repo.update_status(
                    tc,
                    status="completed" if tool_result.status == "success" else "failed",
                    output={
                        "status": tool_result.status,
                        "summary": tool_result.summary,
                        "data": tool_result.data,
                    },
                    error_message=tool_result.summary if tool_result.status == "failure" else None,
                    duration_ms=duration_ms,
                )

                if stream_emitter is not None:
                    stream_emitter.emit(AgentStreamEvent(
                        event=AgentStreamEventType.TOOL_CALL_FINISHED,
                        data={
                            "tool_call_id": tc_id,
                            "tool_name": tool_name,
                            "status": tool_result.status,
                            "summary": tool_result.summary,
                        },
                    ))

                # Persist tool message for LLM context
                msg_repo.create(AgentMessageCreate(
                    run_id=run.id,
                    role="tool",
                    content=json.dumps({
                        "status": tool_result.status,
                        "summary": tool_result.summary,
                    }, ensure_ascii=False),
                    tool_call_id=tc_id,
                    tool_name=tool_name,
                ))

                # Check for decision pause — 三类来源合一处判断：
                # 1) request_user_decision 工具直接调用（旧路径）。
                # 2) 任意 tool_result.data.decision_requested = True（通用标志）。
                #    publish_movie_to_library / manual_research 等都通过该标志暂停 run。
                # 3) post_revoke_action / revoke_publish 工具的 waiting_for_post_revoke_action 标志。
                # 若决策由工具附带 data 决策类型，run.current_step 同步成 decision_type
                # （fallback "waiting_user"），让前端能直接看出"卡在哪个决策上"。
                data_dict = tool_result.data if isinstance(tool_result.data, dict) else {}
                is_decision_request = (
                    tool_result.status == "success"
                    and (
                        tool_name == "request_user_decision"
                        or bool(data_dict.get("decision_requested"))
                        or bool(data_dict.get("waiting_for_post_revoke_action"))
                    )
                )
                if is_decision_request:
                    decision_requested = True
                    # 记录触发 pause 的工具的 decision_type，用于 run.current_step
                    if data_dict.get("decision_type"):
                        pause_step_hint = str(data_dict["decision_type"])
                    elif tool_name == "request_user_decision":
                        pause_step_hint = "request_user_decision"
                    if tool_name == "request_user_decision" and stream_emitter is not None:
                        stream_emitter.emit(AgentStreamEvent(
                            event=AgentStreamEventType.DECISION_CREATED,
                            data={"tool_call_id": tc_id},
                        ))

                # Check tool failure limit
                if tool_failure_count >= MAX_TOOL_FAILURES:
                    run_repo.update_status(
                        run,
                        status="failed",
                        current_step="max_tool_failures",
                        error_message=f"Too many tool failures ({tool_failure_count} >= {MAX_TOOL_FAILURES})",
                    )
                    task_repo.update_status(
                        task,
                        status="agent_failed",
                        current_step="agent_failed",
                        failure_reason=f"Agent run failed: too many tool failures ({tool_failure_count})",
                    )
                    if stream_emitter is not None:
                        stream_emitter.emit(AgentStreamEvent(
                            event=AgentStreamEventType.ERROR,
                            data={"error": f"Max tool failures reached ({MAX_TOOL_FAILURES})"},
                        ))
                        stream_emitter.emit(AgentStreamEvent(
                            event=AgentStreamEventType.RUN_FINISHED,
                            data={"status": "failed"},
                        ))
                        stream_emitter.close()
                    return AgentRunResult(
                        run_id=run.id,
                        status="failed",
                        message_count=len(msg_repo.list_by_run(run.id)),
                        tool_call_count=tool_call_count,
                        error_message=f"Max tool failures reached ({MAX_TOOL_FAILURES})",
                    )

            # ── 工具批次收口: 释放 SQLite 写锁, 让 LLM 网络调用 / 后台
            # qB 同步 / watch 扫描等其他写者有机会推进. commit 把本批次
            # 工具的写入固化, 然后下一轮 LLM 调用在新的隐式事务里跑.
            # SQLite WAL 模式下 commit 几乎不耗 I/O, 不影响性能.
            try:
                session.commit()
            except Exception:
                logger.exception("Agent run session.commit after tool batch failed")
                session.rollback()

            # ── pause after successful decision request ──────────────
            if decision_requested:
                # 从触发 pause 的工具的 decision_type 推断 run.current_step；
                # 没有 data.decision_type 时保留 "waiting_user" 作 fallback。
                pause_step = pause_step_hint or "waiting_user"
                run_repo.update_status(
                    run, status="waiting_user", current_step=pause_step,
                )
                if stream_emitter is not None:
                    stream_emitter.emit(AgentStreamEvent(
                        event=AgentStreamEventType.RUN_FINISHED,
                        data={"status": "waiting_user"},
                    ))
                    stream_emitter.close()
                return AgentRunResult(
                    run_id=run.id,
                    status="waiting_user",
                    message_count=len(msg_repo.list_by_run(run.id)),
                    tool_call_count=tool_call_count,
                )

        # ── search-loop 安全网 (auto_ingest) ──────────────────────────
        # 触发条件: auto_ingest 模式 + task 仍是 agent_running + 本 run 有
        # 至少一次成功的 search_metadata 工具调用 (表明 LLM 搜索成功但忘了
        # 调 prepare_select_metadata_candidate_decision). 这条安全网不
        # 合成候选 — 只复用历史 search_metadata output, 让 LLM 误判导致
        # 的 max_steps 死循环收敛到候选决策卡或自动确认. 详见
        # `agent-metadata-search-loop-guard` spec.
        if (
            mode == "auto_ingest"
            and task.status == "agent_running"
            and tc_repo is not None
            and _has_successful_search_history(tc_repo, run.id)
        ):
            safety_result = _recover_via_candidate_decision(
                session=session,
                config=config,
                run=run,
                task=task,
                task_repo=task_repo,
                registry=registry,
            )
            if safety_result is not None:
                # 已经进入 waiting_user / completed, 不再走 max_steps
                # 失败路径. SSE 收口已由 helper 负责.
                return safety_result

        # ── max steps reached ────────────────────────────────────────
        # 业务态守卫: 业务工具 (publish_*_to_library / handle_source_cleanup)
        # 已经在 max_steps 触发前把 task 推到 business 终态 (e.g.
        # library_import_complete). runner 不得把 task 反向覆写为
        # agent_failed — 业务态是真相, runner 收口是收口. 与 final-text
        # 路径的 "run failed 时不升级 completed" (lines 344-357, 405-412)
        # 是同一原则的两面.
        #
        # 判定: task.status 是 business 终态 **且** current_step 与 status
        # 一致. status 与 current_step 不一致 (e.g. status=library_import_complete,
        # current_step=publishing) 走保守 agent_failed 路径, 避免误判.
        from media_pilot.agent.sse import AgentStreamEvent, AgentStreamEventType

        terminal_business_states = {"library_import_complete"}
        if (
            task.status in terminal_business_states
            and task.current_step == task.status
        ):
            # 业务成功收口: 持久化一条 assistant 短消息说明"未生成最终
            # 总结", 改标 AgentRun 为 completed, 不动 task. SSE 走
            # RUN_FINISHED {completed} 路径, 不发 ERROR.
            msg_repo.create(AgentMessageCreate(
                run_id=run.id, role="assistant",
                content="任务已完成, 但未生成最终总结。",
            ))
            run_repo.update_status(
                run, status="completed",
                current_step="completed_without_final_text",
            )
            if stream_emitter is not None:
                stream_emitter.emit(AgentStreamEvent(
                    event=AgentStreamEventType.RUN_FINISHED,
                    data={"status": "completed"},
                ))
                stream_emitter.close()
            return AgentRunResult(
                run_id=run.id,
                status="completed",
                message_count=len(msg_repo.list_by_run(run.id)),
                tool_call_count=tool_call_count,
            )

        # status 与 current_step 不一致: 保守 agent_failed, 记录原因.
        if task.status in terminal_business_states:
            reason = (
                f"max_steps 与业务终态不一致: "
                f"status={task.status} current_step={task.current_step}"
            )
        else:
            reason = f"Agent run failed: exceeded max steps ({MAX_STEPS})"

        run_repo.update_status(
            run,
            status="failed",
            current_step="max_steps_exceeded",
            error_message=(
                f"Exceeded max_steps ({MAX_STEPS}) without final response"
                if task.status not in terminal_business_states
                else reason
            ),
        )
        task_repo.update_status(
            task,
            status="agent_failed",
            current_step="agent_failed",
            failure_reason=reason,
        )
        if stream_emitter is not None:
            stream_emitter.emit(AgentStreamEvent(
                event=AgentStreamEventType.ERROR,
                data={"error": f"Exceeded max steps ({MAX_STEPS})"},
            ))
            stream_emitter.emit(AgentStreamEvent(
                event=AgentStreamEventType.RUN_FINISHED,
                data={"status": "failed"},
            ))
            stream_emitter.close()
        return AgentRunResult(
            run_id=run.id,
            status="failed",
            message_count=len(msg_repo.list_by_run(run.id)),
            tool_call_count=tool_call_count,
            error_message=(
                f"Exceeded max_steps ({MAX_STEPS})"
                if task.status not in terminal_business_states
                else reason
            ),
        )

    except LLMConfigurationError as exc:
        run_repo.update_status(
            run, status="failed", current_step="config_error",
            error_message=str(exc),
        )
        task_repo.update_status(
            task, status="agent_failed", current_step="agent_failed",
            failure_reason=f"LLM configuration error: {exc}",
        )
        if stream_emitter is not None:
            stream_emitter.emit(AgentStreamEvent(
                event=AgentStreamEventType.ERROR,
                data={"error": str(exc)},
            ))
            stream_emitter.emit(AgentStreamEvent(
                event=AgentStreamEventType.RUN_FINISHED,
                data={"status": "failed"},
            ))
            stream_emitter.close()
        return AgentRunResult(
            run_id=run.id,
            status="failed",
            message_count=len(msg_repo.list_by_run(run.id)),
            tool_call_count=tool_call_count,
            error_message=str(exc),
        )

    except Exception as exc:
        run_repo.update_status(
            run, status="failed", current_step="llm_error",
            error_message=str(exc),
        )
        task_repo.update_status(
            task, status="agent_failed", current_step="agent_failed",
            failure_reason=f"Agent run failed: {exc}",
        )
        if stream_emitter is not None:
            stream_emitter.emit(AgentStreamEvent(
                event=AgentStreamEventType.ERROR,
                data={"error": str(exc)},
            ))
            stream_emitter.emit(AgentStreamEvent(
                event=AgentStreamEventType.RUN_FINISHED,
                data={"status": "failed"},
            ))
            stream_emitter.close()
        return AgentRunResult(
            run_id=run.id,
            status="failed",
            message_count=len(msg_repo.list_by_run(run.id)),
            tool_call_count=tool_call_count,
            error_message=str(exc),
        )


def run_agent_turn(
    *,
    session: Session,
    config: AppConfig,
    task_id: str,
    mode: str = "default",
    mock_llm_client: AgentLLMClient | None = None,
    initial_message: str | None = None,
    user_message_text: str | None = None,
) -> AgentRunResult:
    """Create and execute an AgentRun for the given task.

    ``mode="default"`` exposes only READ_ONLY/DRAFT tools.
    ``mode="auto_ingest"`` additionally exposes whitelisted WRITE tools.

    ``mock_llm_client`` allows tests to inject a controlled LLM without real
    network calls.

    ``initial_message`` overrides the default initial user message. Used for
    retry runs to provide a recovery prompt that references previous history.

    ``user_message_text`` is the raw user input for freeform runs. When
    provided, only this text is persisted as the role=user message, and
    ``initial_message`` (which carries context injection) is passed to the
    LLM as non-persisted context.
    """
    from media_pilot.repository.repositories import (
        AgentMessageCreate,
        AgentMessageRepository,
        AgentRunCreate,
        AgentRunRepository,
        AgentToolCallRepository,
        IngestTaskRepository,
    )

    task_repo = IngestTaskRepository(session)
    run_repo = AgentRunRepository(session)
    msg_repo = AgentMessageRepository(session)
    tc_repo = AgentToolCallRepository(session)
    registry = get_tool_registry()
    register_builtin_tools()

    # ── create AgentRun ──────────────────────────────────────────────
    task = task_repo.get(task_id)
    if task is None:
        raise ValueError(f"Task {task_id} not found")

    run = run_repo.create(AgentRunCreate(task_id=task_id, current_step="agent_start"))
    previous_status = task.status
    previous_step = task.current_step
    task_repo.update_status(task, status="agent_running", current_step="agent_running")

    # ── persist initial user message ─────────────────────────────────
    if user_message_text is not None:
        # Freeform: persist only the raw user input; llm_context holds the
        # full context-injected prompt that is passed to the LLM but not
        # persisted as a user-visible message.
        msg_repo.create(AgentMessageCreate(
            run_id=run.id, role="user", content=user_message_text,
        ))
        llm_context = initial_message
    else:
        initial_content = initial_message if initial_message is not None else make_initial_user_message(task_id)
        msg_repo.create(AgentMessageCreate(
            run_id=run.id, role="user", content=initial_content,
        ))
        llm_context = None

    # ── build allowed tool schemas ───────────────────────────────────
    allowed_tools = get_allowed_tool_schemas(registry, mode=mode)

    return _run_agent_loop(
        session=session,
        config=config,
        run=run,
        task=task,
        msg_repo=msg_repo,
        run_repo=run_repo,
        tc_repo=tc_repo,
        task_repo=task_repo,
        registry=registry,
        allowed_tools=allowed_tools,
        mode=mode,
        mock_llm_client=mock_llm_client,
        previous_status=previous_status,
        previous_step=previous_step,
        llm_context=llm_context,
    )


def continue_agent_run(
    *,
    session: Session,
    config: AppConfig,
    run_id: str,
    mode: str = "default",
    mock_llm_client: AgentLLMClient | None = None,
) -> AgentRunResult:
    """Continue an existing AgentRun without creating a new one.

    Does NOT create a new run or write an initial user message.
    Only allows continuing ``waiting_user`` or ``active`` runs.
    """
    from media_pilot.repository.repositories import (
        AgentMessageRepository,
        AgentRunRepository,
        AgentToolCallRepository,
        IngestTaskRepository,
    )

    task_repo = IngestTaskRepository(session)
    run_repo = AgentRunRepository(session)
    msg_repo = AgentMessageRepository(session)
    tc_repo = AgentToolCallRepository(session)
    registry = get_tool_registry()
    register_builtin_tools()

    run = run_repo.get(run_id)
    if run is None:
        raise ValueError(f"AgentRun {run_id} not found")

    if run.status not in ("waiting_user", "active"):
        raise ValueError(
            f"Cannot continue AgentRun {run_id} with status '{run.status}'. "
            f"Only 'waiting_user' or 'active' runs can be continued."
        )

    task = task_repo.get(run.task_id)
    if task is None:
        raise ValueError(f"Task {run.task_id} not found for AgentRun {run_id}")

    allowed_tools = get_allowed_tool_schemas(registry, mode=mode)

    return _run_agent_loop(
        session=session,
        config=config,
        run=run,
        task=task,
        msg_repo=msg_repo,
        run_repo=run_repo,
        tc_repo=tc_repo,
        task_repo=task_repo,
        registry=registry,
        allowed_tools=allowed_tools,
        mode=mode,
        mock_llm_client=mock_llm_client,
    )


def run_agent_turn_streaming(
    *,
    session_factory,
    config: AppConfig,
    task_id: str,
    initial_message: str,
    user_message_text: str | None = None,
    mode: str = "freeform",
    mock_llm_client: AgentLLMClient | None = None,
):
    """Create an AgentRun and execute it in a background thread, returning a
    stream emitter for SSE consumption.

    The emitter is ready to be iterated immediately.  The Agent loop runs in
    a daemon thread so the SSE connection can be the main driver.

    Returns ``(AgentStreamEmitter, AgentRunResult | None)``.
    The result is populated after the background thread completes.

    ``user_message_text`` is the raw user input. When provided, only this
    text is persisted as the role=user message; ``initial_message`` carries
    the full context injection and is passed to the LLM as non-persisted
    context.
    """
    from media_pilot.agent.sse import AgentStreamEmitter, AgentStreamEvent, AgentStreamEventType
    from media_pilot.repository.repositories import (
        AgentMessageCreate,
        AgentMessageRepository,
        AgentRunCreate,
        AgentRunRepository,
        AgentToolCallRepository,
        IngestTaskRepository,
    )

    emitter = AgentStreamEmitter()
    result_holder: list = [None]

    def _run_in_thread():
        with session_factory() as session:
            task_repo = IngestTaskRepository(session)
            run_repo = AgentRunRepository(session)
            msg_repo = AgentMessageRepository(session)
            tc_repo = AgentToolCallRepository(session)
            registry = get_tool_registry()
            register_builtin_tools()

            task = task_repo.get(task_id)
            if task is None:
                emitter.emit(AgentStreamEvent(
                    event=AgentStreamEventType.ERROR,
                    data={"error": f"Task {task_id} not found"},
                ))
                emitter.close()
                return

            previous_status = task.status
            previous_step = task.current_step
            run = run_repo.create(AgentRunCreate(task_id=task_id, current_step="agent_start"))
            task_repo.update_status(task, status="agent_running", current_step="agent_running")

            # Persist only the raw user input; full context goes to LLM only
            if user_message_text is not None:
                msg_repo.create(AgentMessageCreate(
                    run_id=run.id, role="user", content=user_message_text,
                ))
                llm_context = initial_message
            else:
                msg_repo.create(AgentMessageCreate(
                    run_id=run.id, role="user", content=initial_message,
                ))
                llm_context = None

            # Emit user message event
            emitter.emit(AgentStreamEvent(
                event=AgentStreamEventType.USER_MESSAGE,
                data={"run_id": run.id},
            ))

            allowed_tools = get_allowed_tool_schemas(registry, mode=mode)

            try:
                result = _run_agent_loop(
                    session=session,
                    config=config,
                    run=run,
                    task=task,
                    msg_repo=msg_repo,
                    run_repo=run_repo,
                    tc_repo=tc_repo,
                    task_repo=task_repo,
                    registry=registry,
                    allowed_tools=allowed_tools,
                    mode=mode,
                    mock_llm_client=mock_llm_client,
                    previous_status=previous_status,
                    previous_step=previous_step,
                    llm_context=llm_context,
                    stream_emitter=emitter,
                )
                result_holder[0] = result
                session.commit()
            except Exception as exc:
                session.rollback()
                emitter.emit(AgentStreamEvent(
                    event=AgentStreamEventType.ERROR,
                    data={"error": str(exc)},
                ))
                emitter.emit(AgentStreamEvent(
                    event=AgentStreamEventType.RUN_FINISHED,
                    data={"status": "failed"},
                ))
                emitter.close()

    thread = threading.Thread(target=_run_in_thread, daemon=True)
    thread.start()

    return emitter, result_holder


# ══════════════════════════════════════════════════════════════════════
# Ack-only retry (fix-agent-retry-button-ui-state-semantics)
# ══════════════════════════════════════════════════════════════════════

@dataclass(frozen=True, kw_only=True)
class AgentRunAck:
    """Ack returned immediately by ``run_agent_turn_async``.

    The actual Agent loop runs in a background thread. The ack only carries
    the new run_id and the initial "active" status; final status is observable
    via task-detail / agent-status-summary once the background loop settles.
    """
    run_id: str
    status: str
    # Exposed for tests so they can ``thread.join(timeout=...)`` to wait for
    # the background loop to settle. Production callers MUST NOT use this
    # (the thread is a daemon and intentionally fire-and-forget).
    thread: threading.Thread


def run_agent_turn_async(
    *,
    session_factory,
    config: AppConfig,
    task_id: str,
    mode: str = "auto_ingest",
    initial_message: str | None = None,
    mock_llm_client: AgentLLMClient | None = None,
) -> AgentRunAck:
    """Ack-only retry entry — used by the createAgentRun endpoint on
    ``agent_failed`` tasks so the retry button's loading state is bound to
    the ack POST lifetime, NOT the entire Agent execution.

    Synchronous phase (runs before the POST returns):
    - Create AgentRun (status=active).
    - Persist the initial user message (default: ``make_retry_user_message``).
    - Set task status to ``agent_running`` and current_step accordingly.
    - Commit.

    Background phase (runs in a daemon thread):
    - Call ``continue_agent_run(run_id=...)`` to reuse the run we just
      created; the loop writes assistant messages / tool calls and finally
      settles the run status (completed / waiting_user / failed). The loop
      does NOT create a second AgentRun.

    The function returns once the synchronous phase commits; the background
    thread continues independently. If the synchronous phase fails (e.g. 404),
    no background thread is spawned.

    freeform and default ``run_agent_turn`` paths are unchanged. This function
    is for the failed-retry path only.

    Implementation note: 同步阶段拆为 ``_create_run_in_session`` helper,
    后台启动拆为 ``_start_background_loop`` helper. 拆出来的目的:
    ``recover_stuck_agent_run`` 需要在"标旧 run failed"和"创建新 run"
    之间共享同一事务, 调用 ``_create_run_in_session`` 把新 run 行加入
    caller 的 session, 一起 commit. ``_start_background_loop`` 同理被
    复用为"成功提交后异步启动 Agent loop".
    """
    from media_pilot.orchestration.db_retry import safe_commit

    # ── 同步阶段: 创建 run / 落 message / 切 task 状态 ──────────────
    with session_factory() as session:
        run_id = _create_run_in_session(
            session=session, task_id=task_id, initial_message=initial_message,
        )
        # safe_commit: OperationalError → rollback + 冒泡. 调用方
        # (v1.py::create_agent_run) 负责捕获并返回 _db_locked_response,
        # 不让锁竞争穿透成 500. 与项目内其它写路径一致.
        safe_commit(session)

    # ── 后台阶段: 复用 existing run, 调 continue_agent_run ────────
    thread = _start_background_loop(
        session_factory=session_factory,
        config=config,
        run_id=run_id,
        task_id=task_id,
        mode=mode,
        mock_llm_client=mock_llm_client,
    )

    return AgentRunAck(run_id=run_id, status="active", thread=thread)


def _create_run_in_session(
    *,
    session: Session,
    task_id: str,
    initial_message: str | None = None,
) -> str:
    """同步阶段 helper: 在调用方的 session 内创建新 AgentRun + 初始
    user message + 把 task 切到 ``agent_running``.

    关键契约:
    - **不 commit**. 由调用方决定事务边界. ``run_agent_turn_async`` 单事务
      commit; ``recover_stuck_agent_run`` 把"标旧 run failed"和"创建新
      run"合并到同一事务, 一起 commit, 实现 all-or-nothing 原子性.
    - 已存在 active / waiting run → 抛 ``ValueError``. 调用方负责把
      ``ValueError`` 转译为 409 envelope. 这条 guard 在 recover_stuck
      场景下"自然通过" — 旧 run 已经被该 service 标 failed,
      ``get_active_or_waiting_by_task`` 返回 None.
    - task 不存在 → 抛 ``ValueError("Task ... not found")``. 这是
      race condition 兜底, 调用方应捕获并转译.
    - 成功路径: 3 个写操作 (create run / create message / update task)
      都加入 session 的 pending transaction, 调用方 commit 后落库.

    返回新 run_id (str).
    """
    from media_pilot.agent.prompts import make_retry_user_message
    from media_pilot.repository.repositories import (
        AgentMessageCreate,
        AgentMessageRepository,
        AgentRunCreate,
        AgentRunRepository,
        IngestTaskRepository,
    )

    task_repo = IngestTaskRepository(session)
    run_repo = AgentRunRepository(session)
    msg_repo = AgentMessageRepository(session)

    task = task_repo.get(task_id)
    if task is None:
        raise ValueError(f"Task {task_id} not found")

    active_run = run_repo.get_active_or_waiting_by_task(task_id)
    if active_run is not None:
        raise ValueError(
            f"Task {task_id} already has an active or waiting AgentRun {active_run.id}"
        )

    run = run_repo.create(AgentRunCreate(task_id=task_id, current_step="agent_start"))
    run_id = run.id

    content = (
        initial_message
        if initial_message is not None
        else make_retry_user_message(task_id)
    )
    msg_repo.create(AgentMessageCreate(
        run_id=run.id, role="user", content=content,
    ))
    task_repo.update_status(task, status="agent_running", current_step="agent_running")
    return run_id


def _start_background_loop(
    *,
    session_factory,
    config: AppConfig,
    run_id: str,
    task_id: str,
    mode: str,
    mock_llm_client: AgentLLMClient | None,
) -> threading.Thread:
    """后台阶段 helper: 在 daemon thread 里跑 ``continue_agent_run``.

    失败兜底契约与 ``run_agent_turn_async`` 原内联实现一致:
    - continue_agent_run 自身抛异常 (run 不存在 / status 非法 /
      注册表故障 / ``_run_agent_loop`` 内部非 catch-all 路径) →
      rollback + ``_handle_background_failure`` 主动标记 run/task 为
      failed, 不能让 run 长期卡在 active.
    - 标记自身失败 (OperationalError) → logger.exception 兜底,
      不掩盖错误.
    - 成功路径: continue_agent_run 内部 _run_agent_loop 用 session.flush()
      写 run.status, 没 commit. 这里必须 commit 落库, 否则切到新 session
      读不到最终状态.

    返回已 start 的 Thread, 调用方按需 join (生产 fire-and-forget).
    """
    from media_pilot.orchestration.db_retry import safe_commit

    def _run_loop_in_thread() -> None:
        with session_factory() as session:
            try:
                continue_agent_run(
                    session=session,
                    config=config,
                    run_id=run_id,
                    mode=mode,
                    mock_llm_client=mock_llm_client,
                )
                safe_commit(session)
            except Exception as exc:
                _handle_background_failure(
                    session=session,
                    run_id=run_id,
                    task_id=task_id,
                    exc=exc,
                )

    thread = threading.Thread(target=_run_loop_in_thread, daemon=True)
    thread.start()
    return thread


def _handle_background_failure(
    *,
    session: Session,
    run_id: str,
    task_id: str,
    exc: BaseException,
) -> None:
    """Background loop 异常兜底: rollback + 重新读取并标记 run/task 为 failed.

    关键差异: 与 ``_run_agent_loop`` 内部 exception handler 不同,
    这里的 exc 来源是 ``continue_agent_run`` 自身抛出的异常 (run
    不存在 / status 非法 / 工具注册表故障 / ``_run_agent_loop`` 内部
    非 catch-all 路径抛异常), 那些情况下 ``_run_agent_loop`` 没机会
    进入自己的 try 块写 failed 状态. 当前 session 已有部分事务
    状态 (continuation run 起步的 create / 初始 message), 必须
    rollback 后再写 failed, 否则冲突.

    写入自身失败 (OperationalError / 其它异常) → logger.exception
    兜底, 不掩盖错误. 这是 known worst-case (DB 持续 lock / 磁盘满
    等), 此时 run/task 仍 active, 需要外部 ops 介入.
    """
    from media_pilot.orchestration.db_retry import safe_commit
    from media_pilot.repository.repositories import (
        AgentRunRepository,
        IngestTaskRepository,
    )

    try:
        session.rollback()
    except Exception:
        logger.exception("rollback 失败, session 状态可能不一致")

    error_summary = f"{type(exc).__name__}: {exc}"
    try:
        run_repo = AgentRunRepository(session)
        task_repo = IngestTaskRepository(session)
        run = run_repo.get(run_id)
        task = task_repo.get(task_id)

        # 仅在 run 仍是 active 时改写. 已被 ``_run_agent_loop`` 标 failed
        # 的 run (e.g. 走到内部 catch-all 路径) 不动它的 error_message —
        # 那条路径的 error_message 更具体 (带 step / current_step 上下文).
        if run is not None and run.status == "active":
            run_repo.update_status(
                run,
                status="failed",
                current_step="background_failed",
                error_message=error_summary,
            )
        if task is not None and task.status == "agent_running":
            task_repo.update_status(
                task,
                status="agent_failed",
                current_step="agent_failed",
                failure_reason=f"Background agent run failed: {error_summary}",
            )
        safe_commit(session)
    except OperationalError:
        # safe_commit 自身 rollback + 冒泡. 此时 run/task 仍卡在 active.
        # known worst-case, 至少 logger.exception 留痕, 让 ops 能定位.
        logger.exception(
            "Failed to persist background failure for run %s / task %s "
            "(OperationalError); they may remain in active state",
            run_id, task_id,
        )
    except Exception:
        logger.exception(
            "Unexpected error marking run %s / task %s as failed",
            run_id, task_id,
        )
