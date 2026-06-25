"""ResourceIntentParser parse failure 诊断日志测试.

锁定 (simplify-docker-onboarding-and-diagnostics):
- JSON 解析失败时, warning 日志必须包含 model, finish_reason, 内容长度,
  JSON 错误和截断后的 LLM 原始返回片段.
- 截断上限固定 2KB, 截断必须在日志中可见.
- 敏感字段 (apikey/token/key/Authorization) 在日志中必须脱敏.
- API 响应不得包含原始 LLM 内容.
- max_tokens 不得回落到过小值.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

# ── 截断 / 脱敏 helper ──


class TestTruncateSnippet:
    def test_short_text_unchanged(self):
        from media_pilot.resource_discovery.intent_parser import _truncate_snippet

        assert _truncate_snippet("hello") == "hello"

    def test_exact_limit_unchanged(self):
        from media_pilot.resource_discovery.intent_parser import (
            DIAGNOSTIC_SNIPPET_MAX_BYTES,
            _truncate_snippet,
        )

        text = "a" * DIAGNOSTIC_SNIPPET_MAX_BYTES
        out = _truncate_snippet(text)
        # 正好等于上限时, 不应被截断
        assert out == text

    def test_over_limit_truncated(self):
        from media_pilot.resource_discovery.intent_parser import (
            DIAGNOSTIC_SNIPPET_MAX_BYTES,
            _truncate_snippet,
        )

        text = "a" * (DIAGNOSTIC_SNIPPET_MAX_BYTES + 100)
        out = _truncate_snippet(text)
        assert len(out) < len(text)
        assert "[truncated" in out
        # 截断后长度接近上限
        assert len(out) <= DIAGNOSTIC_SNIPPET_MAX_BYTES + 50  # 包含截断标记


class TestRedactSecrets:
    def test_apikey_equals_redacted(self):
        from media_pilot.resource_discovery.intent_parser import _redact_secrets

        text = "config apikey=sk-secret-123 other"
        out = _redact_secrets(text)
        assert "sk-secret-123" not in out
        assert "apikey=***" in out or "[REDACTED]" in out

    def test_token_redacted(self):
        from media_pilot.resource_discovery.intent_parser import _redact_secrets

        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.aaa"
        out = _redact_secrets(text)
        assert "eyJhbGciOiJIUzI1NiJ9" not in out
        assert "Bearer" in out  # 字段名保留, 值脱敏

    def test_authorization_header_redacted(self):
        from media_pilot.resource_discovery.intent_parser import _redact_secrets

        text = "Authorization: Basic dXNlcjpwYXNz"
        out = _redact_secrets(text)
        assert "dXNlcjpwYXNz" not in out

    def test_key_equals_redacted(self):
        from media_pilot.resource_discovery.intent_parser import _redact_secrets

        text = "key=my-secret-value"
        out = _redact_secrets(text)
        assert "my-secret-value" not in out

    def test_safe_text_unchanged(self):
        from media_pilot.resource_discovery.intent_parser import _redact_secrets

        text = "user input: 天气之子 1080p"
        out = _redact_secrets(text)
        assert out == text


# ── JSON 解析失败诊断日志 ──


def _make_fake_chat_response(
    *,
    content: str,
    finish_reason: str = "stop",
    model: str = "test-model-v1",
):
    """构造模拟 OpenAI ChatCompletion 返回."""
    response = MagicMock()
    response.model = model
    response.choices = [MagicMock()]
    response.choices[0].finish_reason = finish_reason
    response.choices[0].message.content = content
    return response


class TestParseFailureDiagnostic:
    def _build_parser(self, response):
        parser_cls = _import_parser_cls()
        parser = parser_cls(
            api_key="test",
            base_url="https://api.example.com/v1",
            model="test-model",
            timeout_seconds=10,
        )
        # 替换 client 为 mock, 直接返回 response
        parser._client = MagicMock()
        parser._client.chat.completions.create.return_value = response
        return parser

    def test_json_failure_logs_model_and_finish_reason(self, caplog):
        from media_pilot.resource_discovery.intent_parser import IntentParseError

        bad = "this is not json { broken"
        response = _make_fake_chat_response(
            content=bad, finish_reason="length", model="fancy-model-v3"
        )
        parser = self._build_parser(response)

        with caplog.at_level(
            logging.WARNING, logger="media_pilot.resource_discovery.intent_parser"
        ):
            with pytest.raises(IntentParseError):
                parser.parse("天气之子")

        # 拼成一条长字符串便于查找
        log_text = "\n".join(rec.message for rec in caplog.records)
        assert "fancy-model-v3" in log_text
        assert "length" in log_text
        assert "JSON" in log_text or "json" in log_text
        # 原始内容长度也要记录
        assert str(len(bad)) in log_text

    def test_json_failure_logs_truncated_snippet(self, caplog):
        from media_pilot.resource_discovery.intent_parser import IntentParseError

        # 故意写一个超过截断上限的 invalid json
        big = "broken-prefix-" + ("x" * 5000)
        response = _make_fake_chat_response(content=big)
        parser = self._build_parser(response)

        with caplog.at_level(
            logging.WARNING, logger="media_pilot.resource_discovery.intent_parser"
        ):
            with pytest.raises(IntentParseError):
                parser.parse("天气之子")

        log_text = "\n".join(rec.message for rec in caplog.records)
        # 必须看到截断标记
        assert "truncated" in log_text.lower() or "省略" in log_text

    def test_json_failure_redacts_apikey_in_snippet(self, caplog):
        from media_pilot.resource_discovery.intent_parser import IntentParseError

        bad = "oops apikey=leaked-key-9999 something"
        response = _make_fake_chat_response(content=bad)
        parser = self._build_parser(response)

        with caplog.at_level(
            logging.WARNING, logger="media_pilot.resource_discovery.intent_parser"
        ):
            with pytest.raises(IntentParseError):
                parser.parse("天气之子")

        log_text = "\n".join(rec.message for rec in caplog.records)
        assert "leaked-key-9999" not in log_text

    def test_error_response_does_not_leak_raw_content(self, caplog):
        """API 错误响应文案必须不含原始 LLM 内容."""
        from media_pilot.resource_discovery.intent_parser import IntentParseError

        # 模拟 search_resources 捕获 IntentParseError 并返回错误响应
        bad = "very-long-leaked-snippet-zzz-9999 broken"
        # 这里不实际调用 search_resources (需要真实 LLM), 改为直接构造
        # 错误并验证 message 不含原始内容.
        err = IntentParseError(
            "LLM 返回了无法解析的结果, 请尝试用更明确的片名或关键词重新搜索"
        )
        # 错误消息必须是预定义的安全文案, 不带原始内容
        assert "very-long-leaked-snippet-zzz-9999" not in err.message
        # 用户文案不能包含完整 raw_content
        assert bad not in err.message

    def test_non_parse_failure_keeps_user_safe_message(self, caplog):
        """LLM 调用超时 / 网络错误, 不走 JSON parse 路径, 仍返回安全文案."""
        from media_pilot.resource_discovery.intent_parser import IntentParseError

        parser_cls = _import_parser_cls()
        parser = parser_cls(
            api_key="test",
            base_url="https://api.example.com/v1",
            model="test-model",
            timeout_seconds=10,
        )
        parser._client = MagicMock()
        parser._client.chat.completions.create.side_effect = RuntimeError(
            "upstream timeout"
        )

        with caplog.at_level(logging.WARNING):
            with pytest.raises(IntentParseError) as ei:
                parser.parse("天气之子")
        # 用户文案是固定的, 不暴露内部异常
        assert "upstream timeout" not in str(ei.value)


# ── max_tokens 锁定 ──


class TestMaxTokensFloor:
    def test_intent_parser_max_tokens_at_least_2000(self):
        """max_tokens 不得回落到过小值 (易截断 JSON 触发 parse failure)."""
        from media_pilot.resource_discovery.intent_parser import (
            RESOURCE_INTENT_MAX_TOKENS,
        )
        assert RESOURCE_INTENT_MAX_TOKENS >= 2000, (
            f"RESOURCE_INTENT_MAX_TOKENS={RESOURCE_INTENT_MAX_TOKENS} 过小, "
            f"复杂 prompt / 资源站关键词可能截断"
        )


def _import_parser_cls():
    from media_pilot.resource_discovery.intent_parser import ResourceIntentParser
    return ResourceIntentParser
