"""LLM 回复语言偏好 — 最小提示注入测试.

背景: Auto Ingest Agent 在中文用户环境下仍以英文回复. 修复后
`build_auto_ingest_system_prompt(config)` 会在既有 `AUTO_INGEST_SYSTEM_PROMPT`
末尾追加一段最小语言指令 (zh / en / auto / 未知), 不重写主提示词既有
规则, 也不影响元数据 / 库产物语言 (那是 tmdb_language_priority 的事).
"""

from __future__ import annotations

from pathlib import Path

import pytest


def _make_config(**overrides) -> "AppConfig":
    from media_pilot.config.settings import AppConfig

    base: dict = dict(
        downloads_dir=Path("/tmp/d"),
        watch_dir=Path("/tmp/w"),
        workspace_dir=Path("/tmp/ws"),
        movies_dir=Path("/tmp/m"),
        shows_dir=Path("/tmp/s"),
        database_dir=Path("/tmp/db"),
    )
    base.update(overrides)
    return AppConfig(**base)


class TestResolveReplyLanguage:
    def test_auto_uses_tmdb_priority_zh(self):
        from media_pilot.agent.prompts import _resolve_reply_language

        config = _make_config(
            llm_reply_language="auto",
            tmdb_language_priority=("zh-CN", "en-US"),
        )
        assert _resolve_reply_language(config) == "zh-CN"

    def test_auto_uses_tmdb_priority_en(self):
        from media_pilot.agent.prompts import _resolve_reply_language

        config = _make_config(
            llm_reply_language="auto",
            tmdb_language_priority=("en-US", "zh-CN"),
        )
        assert _resolve_reply_language(config) == "en-US"

    def test_auto_zh_prefix_variants(self):
        from media_pilot.agent.prompts import _resolve_reply_language

        for variant in ("zh-CN", "zh-TW", "zh-HK"):
            config = _make_config(
                llm_reply_language="auto",
                tmdb_language_priority=(variant, "en-US"),
            )
            assert _resolve_reply_language(config) == "zh-CN", variant

    def test_auto_en_prefix_variants(self):
        from media_pilot.agent.prompts import _resolve_reply_language

        for variant in ("en-US", "en-GB", "en-AU"):
            config = _make_config(
                llm_reply_language="auto",
                tmdb_language_priority=(variant, "zh-CN"),
            )
            assert _resolve_reply_language(config) == "en-US", variant

    def test_auto_unknown_prefix_falls_back_to_english(self):
        from media_pilot.agent.prompts import _resolve_reply_language

        config = _make_config(
            llm_reply_language="auto",
            tmdb_language_priority=("ja-JP",),
        )
        assert _resolve_reply_language(config) == "en-US"

    def test_explicit_known_code_returned_as_is(self):
        from media_pilot.agent.prompts import _resolve_reply_language

        config = _make_config(llm_reply_language="zh-CN")
        assert _resolve_reply_language(config) == "zh-CN"

    def test_unknown_code_falls_back_to_english_with_warning(self, caplog):
        import logging

        from media_pilot.agent.prompts import _resolve_reply_language

        config = _make_config(llm_reply_language="klingon-KL")
        with caplog.at_level(logging.WARNING, logger="media_pilot.agent.prompts"):
            result = _resolve_reply_language(config)
        assert result == "en-US"
        assert any("未知 llm_reply_language" in r.message for r in caplog.records)

    def test_empty_string_treated_as_auto(self):
        from media_pilot.agent.prompts import _resolve_reply_language

        config = _make_config(llm_reply_language="", tmdb_language_priority=("en-US",))
        assert _resolve_reply_language(config) == "en-US"


class TestBuildLanguageInstruction:
    def test_zh_instruction_in_chinese(self):
        from media_pilot.agent.prompts import _build_language_instruction

        text = _build_language_instruction("zh-CN")
        assert "Reply language" in text
        assert "简体中文" in text
        # 不要翻译元数据提示
        assert "不要翻译" in text or "不得翻译" in text or "保留 provider" in text

    def test_en_instruction_in_english(self):
        from media_pilot.agent.prompts import _build_language_instruction

        text = _build_language_instruction("en-US")
        assert "Reply language" in text
        assert "English" in text
        assert "Do NOT translate" in text

    def test_unknown_code_falls_back_to_english_text(self):
        from media_pilot.agent.prompts import _build_language_instruction

        text = _build_language_instruction("klingon-KL")
        assert "English" in text
        assert "Do NOT translate" in text


class TestBuildAutoIngestSystemPrompt:
    def test_zh_prompt_appends_chinese_section(self):
        from media_pilot.agent.prompts import (
            AUTO_INGEST_SYSTEM_PROMPT,
            build_auto_ingest_system_prompt,
        )

        config = _make_config(llm_reply_language="zh-CN")
        full = build_auto_ingest_system_prompt(config)
        # 主提示词既有内容必须原样保留 (不能被改写)
        assert "Safety Hard Gates" in full
        assert "Workflow" in full
        assert "Metadata Auto-Confirm Rules" in full
        # 末尾追加语言指令
        assert "Reply language" in full
        assert "简体中文" in full
        # 主提示词字符串本身没被污染 (既有 import 不能变)
        assert "Reply language" not in AUTO_INGEST_SYSTEM_PROMPT

    def test_en_prompt_appends_english_section(self):
        from media_pilot.agent.prompts import (
            AUTO_INGEST_SYSTEM_PROMPT,
            build_auto_ingest_system_prompt,
        )

        config = _make_config(llm_reply_language="en-US")
        full = build_auto_ingest_system_prompt(config)
        assert "Safety Hard Gates" in full
        assert "Reply language" in full
        assert "English" in full
        assert "Reply language" not in AUTO_INGEST_SYSTEM_PROMPT

    def test_auto_prompt_uses_tmdb_priority(self):
        from media_pilot.agent.prompts import build_auto_ingest_system_prompt

        config = _make_config(
            llm_reply_language="auto",
            tmdb_language_priority=("zh-CN", "en-US"),
        )
        full = build_auto_ingest_system_prompt(config)
        assert "简体中文" in full

    def test_unknown_lang_does_not_raise(self):
        from media_pilot.agent.prompts import build_auto_ingest_system_prompt

        config = _make_config(llm_reply_language="klingon-KL")
        full = build_auto_ingest_system_prompt(config)
        assert "English" in full

    def test_prompt_does_not_translate_metadata_field_names(self):
        """指令必须明确不翻译结构化字段 — 防止 LLM 把元数据标题也'翻译'了."""
        from media_pilot.agent.prompts import build_auto_ingest_system_prompt

        config = _make_config(llm_reply_language="zh-CN")
        full = build_auto_ingest_system_prompt(config)
        # 指令里必须显式提到 metadata 字段保留 provider 原始输出
        assert "标题" in full or "元数据" in full
        assert "provider" in full or "原始" in full

    def test_zh_prompt_does_not_contain_literal_name_placeholder(self):
        """回归: 之前中文 reply language 指令里 ``{name}`` 被错写成普通字符串
        (没有 f-string 前缀), 导致实际 prompt 出现字面 ``{name}``, LLM 看到
        会困惑. 此处断言拼接后的 prompt 中不能含字面 ``{name}``."""
        from media_pilot.agent.prompts import build_auto_ingest_system_prompt

        config = _make_config(llm_reply_language="zh-CN")
        full = build_auto_ingest_system_prompt(config)
        assert "简体中文" in full
        assert "{name}" not in full, (
            "中文 reply language 指令内不应出现字面 ``{name}`` 占位符"
        )

    def test_en_prompt_does_not_contain_literal_name_placeholder(self):
        """英文版本同样的回归: 不应出现字面 ``{name}``."""
        from media_pilot.agent.prompts import build_auto_ingest_system_prompt

        config = _make_config(llm_reply_language="en-US")
        full = build_auto_ingest_system_prompt(config)
        assert "English" in full
        assert "{name}" not in full
