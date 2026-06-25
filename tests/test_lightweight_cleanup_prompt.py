"""轻量清洗 prompt 测试 — Phase 4: TMDB/TPDB profile-aware cleanup"""

from pathlib import Path
from unittest.mock import MagicMock, patch


class TestLightweightCleanKeyword:
    """测试 _lightweight_clean_keyword 的 profile-aware 行为"""

    def test_tmdb_returns_cleaned_title(self):
        """TMDB 路径清洗：去掉技术噪声，保留标题+年份"""
        from media_pilot.api.resource_discovery_routes import _lightweight_clean_keyword
        from media_pilot.config import AppConfig

        config = AppConfig(
            llm_api_key="test-key",
            llm_base_url="http://test",
            llm_model="test-model",
            downloads_dir=Path("/tmp/test-downloads"),
            watch_dir=Path("/tmp/test-watch"),
            workspace_dir=Path("/tmp/test-workspace"),
            movies_dir=Path("/tmp/test-movies"),
            shows_dir=Path("/tmp/test-shows"),
            database_dir=Path("/tmp/test-db"),
        )

        with patch("openai.OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_openai.return_value = mock_client
            mock_completion = MagicMock()
            mock_completion.choices = [MagicMock()]
            mock_completion.choices[0].message.content = "Weathering With You 2019"
            mock_client.chat.completions.create.return_value = mock_completion

            result = _lightweight_clean_keyword(
                config,
                "[TGx] Weathering With You 2019 1080p BluRay x264",
                profile="tmdb_movie",
            )
            assert result == "Weathering With You 2019"

    def test_tmdb_preserves_chinese_title(self):
        """TMDB 路径：中文片名优先保留"""
        from media_pilot.api.resource_discovery_routes import _lightweight_clean_keyword
        from media_pilot.config import AppConfig

        config = AppConfig(
            llm_api_key="test-key",
            llm_base_url="http://test",
            llm_model="test-model",
            downloads_dir=Path("/tmp/test-downloads"),
            watch_dir=Path("/tmp/test-watch"),
            workspace_dir=Path("/tmp/test-workspace"),
            movies_dir=Path("/tmp/test-movies"),
            shows_dir=Path("/tmp/test-shows"),
            database_dir=Path("/tmp/test-db"),
        )

        with patch("openai.OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_openai.return_value = mock_client

            # Simulate: LLM returns cleaned Chinese title
            mock_completion = MagicMock()
            mock_completion.choices = [MagicMock()]
            mock_completion.choices[0].message.content = "天气之子 2019"
            mock_client.chat.completions.create.return_value = mock_completion

            result = _lightweight_clean_keyword(
                config,
                "天气之子 2019 1080p WEB-DL x264",
                profile="tmdb_movie",
                intent_context={
                    "user_input": "天气之子",
                    "intent": {
                        "preferred_title_candidates": ["天气之子"],
                        "reason": "中文动画电影",
                    },
                },
            )
            assert "天气之子" in result

    def test_tpdb_extracts_only_catalog_id(self):
        """TPDB 路径：只输出完整番号，不输出题材词"""
        from media_pilot.api.resource_discovery_routes import _lightweight_clean_keyword
        from media_pilot.config import AppConfig

        config = AppConfig(
            llm_api_key="test-key",
            llm_base_url="http://test",
            llm_model="test-model",
            downloads_dir=Path("/tmp/test-downloads"),
            watch_dir=Path("/tmp/test-watch"),
            workspace_dir=Path("/tmp/test-workspace"),
            movies_dir=Path("/tmp/test-movies"),
            shows_dir=Path("/tmp/test-shows"),
            database_dir=Path("/tmp/test-db"),
        )

        with patch("openai.OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_openai.return_value = mock_client
            mock_completion = MagicMock()
            mock_completion.choices = [MagicMock()]
            mock_completion.choices[0].message.content = "ABP-123"
            mock_client.chat.completions.create.return_value = mock_completion

            result = _lightweight_clean_keyword(
                config,
                "ABP-123 1080p FHD",
                profile="tpdb_adult_movie",
            )
            assert result == "ABP-123"

    def test_tpdb_returns_empty_when_unsure(self):
        """TPDB 路径：无把握返回空，不猜测"""
        from media_pilot.api.resource_discovery_routes import _lightweight_clean_keyword
        from media_pilot.config import AppConfig

        config = AppConfig(
            llm_api_key="test-key",
            llm_base_url="http://test",
            llm_model="test-model",
            downloads_dir=Path("/tmp/test-downloads"),
            watch_dir=Path("/tmp/test-watch"),
            workspace_dir=Path("/tmp/test-workspace"),
            movies_dir=Path("/tmp/test-movies"),
            shows_dir=Path("/tmp/test-shows"),
            database_dir=Path("/tmp/test-db"),
        )

        with patch("openai.OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_openai.return_value = mock_client
            mock_completion = MagicMock()
            mock_completion.choices = [MagicMock()]
            mock_completion.choices[0].message.content = ""
            mock_client.chat.completions.create.return_value = mock_completion

            result = _lightweight_clean_keyword(
                config,
                "some-vague-description",
                profile="tpdb_adult_movie",
            )
            # TPDB: empty is valid (no guess)
            assert result == ""

    def test_falls_back_when_llm_unavailable(self):
        """LLM 不可用时安全回退为原始关键词"""
        from media_pilot.api.resource_discovery_routes import _lightweight_clean_keyword
        from media_pilot.config import AppConfig

        config = AppConfig(
            downloads_dir=Path("/tmp/test-downloads"),
            watch_dir=Path("/tmp/test-watch"),
            workspace_dir=Path("/tmp/test-workspace"),
            movies_dir=Path("/tmp/test-movies"),
            shows_dir=Path("/tmp/test-shows"),
            database_dir=Path("/tmp/test-db"),
        )  # No LLM configured

        result = _lightweight_clean_keyword(
            config,
            "Some Title 2024 1080p",
            profile="tmdb_movie",
        )
        assert result == "Some Title 2024 1080p"

    def test_tmdb_prompt_does_not_contain_tpdb_terms(self):
        """TMDB 清洗 prompt 不包含 TPDB/番号术语"""
        from media_pilot.api.resource_discovery_routes import _lightweight_clean_keyword
        from media_pilot.config import AppConfig

        config = AppConfig(
            llm_api_key="test-key",
            llm_base_url="http://test",
            llm_model="test-model",
            downloads_dir=Path("/tmp/test-downloads"),
            watch_dir=Path("/tmp/test-watch"),
            workspace_dir=Path("/tmp/test-workspace"),
            movies_dir=Path("/tmp/test-movies"),
            shows_dir=Path("/tmp/test-shows"),
            database_dir=Path("/tmp/test-db"),
        )

        with patch("openai.OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_openai.return_value = mock_client
            mock_completion = MagicMock()
            mock_completion.choices = [MagicMock()]
            mock_completion.choices[0].message.content = "Test Movie"
            mock_client.chat.completions.create.return_value = mock_completion

            _lightweight_clean_keyword(
                config, "Test Movie", profile="tmdb_movie",
            )

            # Check the system prompt sent to LLM
            call_args = mock_client.chat.completions.create.call_args
            system_msg = call_args[1]["messages"][0]["content"]
            assert "番号" not in system_msg
            assert "TPDB" not in system_msg.upper()

    def test_tpdb_prompt_does_not_contain_tmdb_terms(self):
        """TPDB 清洗 prompt 不包含 TMDB 术语"""
        from media_pilot.api.resource_discovery_routes import _lightweight_clean_keyword
        from media_pilot.config import AppConfig

        config = AppConfig(
            llm_api_key="test-key",
            llm_base_url="http://test",
            llm_model="test-model",
            downloads_dir=Path("/tmp/test-downloads"),
            watch_dir=Path("/tmp/test-watch"),
            workspace_dir=Path("/tmp/test-workspace"),
            movies_dir=Path("/tmp/test-movies"),
            shows_dir=Path("/tmp/test-shows"),
            database_dir=Path("/tmp/test-db"),
        )

        with patch("openai.OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_openai.return_value = mock_client
            mock_completion = MagicMock()
            mock_completion.choices = [MagicMock()]
            mock_completion.choices[0].message.content = "ABP-123"
            mock_client.chat.completions.create.return_value = mock_completion

            _lightweight_clean_keyword(
                config, "ABP-123", profile="tpdb_adult_movie",
            )

            call_args = mock_client.chat.completions.create.call_args
            system_msg = call_args[1]["messages"][0]["content"]
            assert "TMDB" not in system_msg.upper()
