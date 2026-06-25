"""``decision_reply._format_decision_action_message`` 单元测试.

锁定: 用户回复人工决策请求后, 后端写入 AgentMessage.content 是
``[SystemAction] ...`` 系统动作摘要, 不得是 ``role="user"`` + 内部审计
文本. 前端已经支持 ``[SystemAction]`` 前缀识别 + 系统动作样式渲染.

红色: 在改造之前, `_format_decision_action_message` 还不存在, 既有
``_format_user_message`` 返回 ``[User selected option: candidate_xxx]
Question: ...`` 内部审计文本 — 这些测试必失败.
"""

from __future__ import annotations

from pathlib import Path

import pytest


# ── helpers: 直接 import 内部 helper, 跳过 DB / session 启动 ─────────


def _format(reply_input_dict: dict, decision_dict: dict) -> str:
    from media_pilot.services.decision_reply import (
        ReplyInput,
        _format_decision_action_message,
    )
    reply = ReplyInput(
        decision_id=reply_input_dict["decision_id"],
        option_id=reply_input_dict.get("option_id"),
        free_text=reply_input_dict.get("free_text"),
    )
    return _format_decision_action_message(reply, decision_dict)


def _make_decision(
    *, decision_type: str, options: list[dict] | None = None,
    question: str = "dummy question 原文",
) -> dict:
    """镜像 AgentDecisionRequestRepository.list_pending 返回的 dict 形状."""
    return {
        "id": "dr-test",
        "decision_type": decision_type,
        "options": options or [],
        "question": question,
        "free_text_allowed": False,
    }


# ── tests ──


class TestFormatDecisionActionMessage:
    """`_format_decision_action_message` 生成的 system action 摘要规则."""

    def test_select_metadata_candidate_uses_candidate_label(self) -> None:
        """候选选择 → 优先展示候选 label / title, 不得暴露 option id."""
        decision = _make_decision(
            decision_type="select_metadata_candidate",
            options=[{
                "id": "candidate_abc",
                "label": "天气之子 (2019)",
                "payload": {"title": "天气之子", "year": 2019},
            }],
        )
        out = _format(
            {"decision_id": "dr-test", "option_id": "candidate_abc"},
            decision,
        )
        assert out.startswith("[SystemAction] "), (
            f"系统动作消息必须以 [SystemAction] 前缀开头, got: {out!r}"
        )
        assert "candidate_abc" not in out, (
            f"消息不得暴露内部 option id 'candidate_abc' 作为主体, got: {out!r}"
        )
        assert "天气之子" in out, (
            f"select_metadata_candidate 消息必须包含候选可读标题, got: {out!r}"
        )
        assert "dummy question 原文" not in out, (
            f"消息不得把 decision.question 原文拼到聊天内容, got: {out!r}"
        )
        assert "Question:" not in out, (
            f"消息不得含 'Question:' 原文块, got: {out!r}"
        )

    def test_select_primary_video_uses_filename(self) -> None:
        """主视频选择 → 展示文件名摘要, 不得暴露 option id."""
        decision = _make_decision(
            decision_type="select_primary_video",
            options=[{
                "id": "file_opt_1",
                "label": "The.Matrix.1999.mkv",
                "payload": {"path": "/dl/movies/The.Matrix.1999.mkv"},
            }],
        )
        out = _format(
            {"decision_id": "dr-test", "option_id": "file_opt_1"},
            decision,
        )
        assert out.startswith("[SystemAction] ")
        assert "file_opt_1" not in out
        assert "The.Matrix.1999.mkv" in out
        assert "Question:" not in out

    def test_select_subtitles_uses_label(self) -> None:
        """字幕选择 → 展示字幕 label 摘要."""
        decision = _make_decision(
            decision_type="select_subtitles",
            options=[{
                "id": "sub_opt_xyz",
                "label": "简体外挂字幕",
                "payload": {},
            }],
        )
        out = _format(
            {"decision_id": "dr-test", "option_id": "sub_opt_xyz"},
            decision,
        )
        assert out.startswith("[SystemAction] ")
        assert "sub_opt_xyz" not in out
        assert "简体外挂字幕" in out
        assert "Question:" not in out

    def test_review_complex_input_uses_option_label(self) -> None:
        """复杂输入复核 → 展示选择摘要."""
        decision = _make_decision(
            decision_type="review_complex_input",
            options=[{
                "id": "review_opt_1",
                "label": "仅保留主视频",
                "payload": {},
            }],
        )
        out = _format(
            {"decision_id": "dr-test", "option_id": "review_opt_1"},
            decision,
        )
        assert out.startswith("[SystemAction] ")
        assert "review_opt_1" not in out
        assert "仅保留主视频" in out

    def test_target_conflict_overwrite_action(self) -> None:
        """target_conflict 选 overwrite_target → 系统动作摘要描述覆盖意图."""
        decision = _make_decision(
            decision_type="target_conflict",
            options=[
                {"id": "overwrite_target", "label": "覆盖目标", "payload": {}},
                {"id": "cancel_publish", "label": "取消发布", "payload": {}},
            ],
        )
        out = _format(
            {"decision_id": "dr-test", "option_id": "overwrite_target"},
            decision,
        )
        assert out.startswith("[SystemAction] ")
        assert "overwrite_target" not in out
        assert "Question:" not in out

    def test_target_conflict_cancel_action(self) -> None:
        """target_conflict 选 cancel_publish → 取消发布系统动作."""
        decision = _make_decision(
            decision_type="target_conflict",
            options=[
                {"id": "overwrite_target", "label": "覆盖目标", "payload": {}},
                {"id": "cancel_publish", "label": "取消发布", "payload": {}},
            ],
        )
        out = _format(
            {"decision_id": "dr-test", "option_id": "cancel_publish"},
            decision,
        )
        assert out.startswith("[SystemAction] ")
        assert "cancel_publish" not in out

    def test_source_cleanup_keep_action(self) -> None:
        """source_cleanup_action keep_input → 系统动作摘要描述保留意图."""
        decision = _make_decision(
            decision_type="source_cleanup_action",
            options=[
                {"id": "keep_input", "label": "保留源文件", "payload": {}},
                {"id": "trash_input", "label": "移入回收区", "payload": {}},
                {"id": "delete_input", "label": "进入删除预检", "payload": {}},
            ],
        )
        out = _format(
            {"decision_id": "dr-test", "option_id": "keep_input"},
            decision,
        )
        assert out.startswith("[SystemAction] ")
        assert "keep_input" not in out
        assert "Question:" not in out

    def test_source_cleanup_trash_action(self) -> None:
        decision = _make_decision(
            decision_type="source_cleanup_action",
            options=[{"id": "trash_input", "label": "移入回收区", "payload": {}}],
        )
        out = _format(
            {"decision_id": "dr-test", "option_id": "trash_input"},
            decision,
        )
        assert out.startswith("[SystemAction] ")
        assert "trash_input" not in out

    def test_source_cleanup_delete_action(self) -> None:
        decision = _make_decision(
            decision_type="source_cleanup_action",
            options=[{"id": "delete_input", "label": "进入删除预检", "payload": {}}],
        )
        out = _format(
            {"decision_id": "dr-test", "option_id": "delete_input"},
            decision,
        )
        assert out.startswith("[SystemAction] ")
        assert "delete_input" not in out

    def test_manual_selection_blocked_cancel_action(self) -> None:
        decision = _make_decision(
            decision_type="manual_selection_blocked",
            options=[{"id": "cancel", "label": "取消", "payload": {}}],
        )
        out = _format(
            {"decision_id": "dr-test", "option_id": "cancel"},
            decision,
        )
        assert out.startswith("[SystemAction] ")
        assert "cancel" not in out.lower() or "已取消" in out

    def test_post_revoke_action_reingest_new_search(self) -> None:
        decision = _make_decision(
            decision_type="post_revoke_action",
            options=[
                {"id": "reingest_with_new_search", "label": "重新搜索并入库", "payload": {}},
                {"id": "reingest_with_existing_metadata", "label": "沿用现有元数据", "payload": {}},
                {"id": "delete_task_input", "label": "撤回并删除任务输入", "payload": {}},
            ],
        )
        out = _format(
            {"decision_id": "dr-test", "option_id": "reingest_with_new_search"},
            decision,
        )
        assert out.startswith("[SystemAction] ")
        assert "reingest_with_new_search" not in out

    def test_post_revoke_action_delete_task_input(self) -> None:
        decision = _make_decision(
            decision_type="post_revoke_action",
            options=[{"id": "delete_task_input", "label": "撤回并删除任务输入", "payload": {}}],
        )
        out = _format(
            {"decision_id": "dr-test", "option_id": "delete_task_input"},
            decision,
        )
        assert out.startswith("[SystemAction] ")
        assert "delete_task_input" not in out

    def test_free_text_renders_as_supplementary_note(self) -> None:
        """自由文本回复 → 系统动作消息描述"已提交补充说明" + 摘要."""
        decision = _make_decision(
            decision_type="review_complex_input", question="dummy",
        )
        out = _format(
            {
                "decision_id": "dr-test",
                "free_text": "请保留原文件, 不要重命名",
            },
            decision,
        )
        assert out.startswith("[SystemAction] ")
        # 自由文本必须以系统动作语义展示, 含可读摘要
        assert "补充说明" in out or "已提交" in out
        # 用户原文应保留 (摘要形式)
        assert "请保留原文件" in out

    def test_unknown_decision_type_falls_back_to_generic_submit(self) -> None:
        """未知 decision_type 仍应返回 [SystemAction] + 通用提交摘要."""
        decision = _make_decision(
            decision_type="future_unknown_type",
            options=[{"id": "opt_1", "label": "选项 1", "payload": {}}],
        )
        out = _format(
            {"decision_id": "dr-test", "option_id": "opt_1"},
            decision,
        )
        assert out.startswith("[SystemAction] ")
        assert "opt_1" not in out
        # fallback 也不应暴露 Question 原文
        assert "Question:" not in out
        assert "dummy question 原文" not in out

    def test_option_id_not_in_options_falls_back_without_id(self) -> None:
        """option_id 找不到匹配 option 时, 仍 [SystemAction] 不得暴露 id."""
        decision = _make_decision(
            decision_type="select_metadata_candidate",
            options=[{"id": "opt_real", "label": "真选项", "payload": {}}],
        )
        out = _format(
            {"decision_id": "dr-test", "option_id": "opt_not_in_options"},
            decision,
        )
        assert out.startswith("[SystemAction] ")
        # 任意 option id 都不应作为主体出现
        assert "opt_not_in_options" not in out
        assert "opt_real" not in out

    def test_no_legacy_user_selected_option_prefix(self) -> None:
        """旧 `role=\"user\"` + `[User selected option: ...]` 审计格式必须消失."""
        decision = _make_decision(
            decision_type="select_metadata_candidate",
            options=[{"id": "candidate_x", "label": "X", "payload": {}}],
        )
        out = _format(
            {"decision_id": "dr-test", "option_id": "candidate_x"},
            decision,
        )
        assert "[User selected option:" not in out, (
            f"旧内部审计格式 '[User selected option: ...]' 必须替换为 [SystemAction], "
            f"got: {out!r}"
        )
        assert "[User reply]:" not in out
