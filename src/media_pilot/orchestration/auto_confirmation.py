"""自动确认策略模块。

按照 provider-first 语义的明确候选阈值、安全门槛和最佳候选选择逻辑。
本模块不得依赖数据库 session、文件系统写入或 workflow 状态推进。
"""

from media_pilot.adapters.ai import AiParseResult
from media_pilot.config import AdapterMode, AppConfig


def auto_confirm_blocked_reason(
    config: AppConfig,
    ai_result: AiParseResult,
    *,
    source_selection_confidence: float,
    keyword_result,
    provider_search_result,
    metadata_provider_enabled: bool,
    auto_confirm_confidence: float,
    has_llm_keyword_generator: bool = False,
) -> str | None:
    ai_is_fake = config.ai_adapter == AdapterMode.FAKE
    # 有 LLM 关键词顾问时，parse_filename 结果不作为阻塞依据
    skip_ai_parse_checks = ai_is_fake or has_llm_keyword_generator

    # ---- Phase 1: 快速门禁（来源置信度、AI 解析） ----
    if source_selection_confidence < auto_confirm_confidence:
        return "low_source_confidence"
    if not skip_ai_parse_checks and ai_result.confidence < auto_confirm_confidence:
        return "low_ai_confidence"
    if not skip_ai_parse_checks and ai_result.title is None:
        return "missing_title"

    # ---- Phase 2: Provider / 元数据候选质量 ----
    if metadata_provider_enabled and provider_search_result.error_message is not None:
        return "metadata_provider_failed"

    provider_candidates = provider_search_result.candidates
    if metadata_provider_enabled:
        if not provider_candidates:
            return "no_metadata_candidates"
        if not has_clear_winner(
            provider_candidates,
            confidence_threshold=config.metadata_auto_confirm_confidence,
            margin=config.metadata_auto_confirm_margin,
        ):
            return "multiple_metadata_candidates"

        # 存在明确候选 → 安全检查
        # 关键词置信度此时仅用于审计记录，不作为阻塞条件
        candidate, _ = pick_best_candidate(provider_candidates)
        if (
            ai_result.year is not None
            and candidate.year is not None
            and ai_result.year != candidate.year
        ):
            return "year_conflict"
        if not candidate.poster_url:
            return "missing_poster"

    # ---- Phase 3: 目标目录冲突（延后到写入计划阶段检查） ----
    # 目标目录存在性不再在自动确认阶段通过 AiParseResult 提前判断，
    # 改为在获取 provider detail + 生成 MovieWritePlan 后、复制主影片前检查。
    return None


def has_clear_winner(
    candidates: list,
    *,
    confidence_threshold: float,
    margin: float,
) -> bool:
    """判定候选列表是否有明确赢家。

    - 至少 1 个候选
    - top1 置信度 >= 阈值
    - 若存在 top2，则 top1 - top2 >= margin
    """
    if not candidates:
        return False
    sorted_candidates = sorted(
        candidates, key=lambda c: c.confidence or 0, reverse=True
    )
    top1 = sorted_candidates[0]
    if (top1.confidence or 0) < confidence_threshold:
        return False
    if len(sorted_candidates) > 1:
        top2 = sorted_candidates[1]
        if (top1.confidence or 0) - (top2.confidence or 0) < margin:
            return False
    return True


def pick_best_candidate(
    candidates: list,
) -> tuple:
    """按置信度降序排序，返回 (最佳, 次佳或None)"""
    sorted_candidates = sorted(
        candidates,
        key=lambda c: c.confidence or 0,
        reverse=True,
    )
    return sorted_candidates[0], (sorted_candidates[1] if len(sorted_candidates) > 1 else None)
