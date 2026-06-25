"""Prowlarr 公共 indexer 初始化测试.

锁定 (simplify-docker-onboarding-and-diagnostics):
- 默认公共 indexer 集合固定: YTS, The Pirate Bay, LimeTorrents, Nyaa.si,
  Mikan, dmhy, ACG.RIP.
- 不包含成人源 (sukebei.nyaa.si) 或需要账号的私有/认证源.
- 幂等: 已存在同名 indexer 跳过, 不覆盖用户已修改项.
- 单个 indexer 失败只 warning, 不阻塞其它项.
- 全部完成后即便有失败也不阻塞 media-pilot 主服务 (Prowlarr API 完全
  不可用时才报错).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest


# ── 默认集合锁定 ──


class TestDefaultIndexerSet:
    def test_default_set_contains_expected_public_indexers(self):
        from media_pilot.deployment.prowlarr_indexers import DEFAULT_PUBLIC_INDEXERS

        names = {entry.name for entry in DEFAULT_PUBLIC_INDEXERS}
        expected = {
            "YTS",
            "The Pirate Bay",
            "LimeTorrents",
            "Nyaa.si",
            "Mikan",
            "dmhy",
            "ACG.RIP",
        }
        assert expected.issubset(names), (
            f"默认公共 indexer 集合缺: {expected - names}"
        )

    def test_default_set_excludes_adult_sources(self):
        from media_pilot.deployment.prowlarr_indexers import DEFAULT_PUBLIC_INDEXERS

        names = {entry.name for entry in DEFAULT_PUBLIC_INDEXERS}
        assert "sukebei.nyaa.si" not in names, "默认集合不得包含成人源 sukebei.nyaa.si"
        for forbidden in ("Sukebei", "sukebei"):
            assert forbidden not in " ".join(names), (
                f"默认集合含可疑成人源关键词: {forbidden}"
            )

    def test_default_set_excludes_private_or_invite_only(self):
        from media_pilot.deployment.prowlarr_indexers import DEFAULT_PUBLIC_INDEXERS

        # 这些是已知需要账号 / 邀请 / 付费的私有站, 不得默认启用
        private_markers = (
            "HDChina",
            "OurBits",
            "CHDBits",
            "MTeam",
            "TTG",
            "HDSky",
            "Spring Sunday",
            "HaresClub",
            "Audiences",
            "HDB",
        )
        names = " ".join(e.name for e in DEFAULT_PUBLIC_INDEXERS)
        for marker in private_markers:
            assert marker not in names, f"默认集合不得包含私有站: {marker}"


# ── 内部 API 客户端 ──


class _FakeProwlarrAPI:
    """测试用 Prowlarr API stub, 模拟 schema + 已装 indexer + 创建."""

    def __init__(
        self,
        *,
        schema: list[dict] | None = None,
        installed: list[dict] | None = None,
        app_profiles: list[dict] | None = None,
        fail_on_create: set[str] | None = None,
    ) -> None:
        self.schema = list(schema or [])
        self.installed = list(installed or [])
        self.app_profiles = (
            list(app_profiles)
            if app_profiles is not None
            else [{"name": "Standard", "id": 1}]
        )
        self.fail_on_create = set(fail_on_create or set())
        self.created: list[dict] = []
        self._list_schema_calls = 0
        self._list_indexer_calls = 0
        self._list_app_profile_calls = 0

    def list_indexer_schema(self) -> list[dict]:
        self._list_schema_calls += 1
        return list(self.schema)

    def list_indexers(self) -> list[dict]:
        self._list_indexer_calls += 1
        return list(self.installed)

    def list_app_profiles(self) -> list[dict]:
        self._list_app_profile_calls += 1
        return list(self.app_profiles)

    def add_indexer(self, definition: dict) -> dict:
        name = definition.get("name", "")
        if name in self.fail_on_create:
            raise RuntimeError(f"upstream error for {name}")
        self.created.append(definition)
        # 模拟真实行为: 装上后出现在 installed 列表
        self.installed.append(dict(definition))
        return dict(definition)


# ── 单个 indexer 行为 ──


class TestFindIndexerDefinition:
    def test_finds_by_name(self):
        from media_pilot.deployment.prowlarr_indexers import (
            find_indexer_definition,
        )

        schema = [
            {"name": "YTS", "implementation": "Cardigann", "id": 1},
            {"name": "Other", "implementation": "Cardigann", "id": 2},
        ]
        entry = find_indexer_definition(schema, "YTS")
        assert entry is not None
        assert entry["name"] == "YTS"

    def test_returns_none_when_not_found(self):
        from media_pilot.deployment.prowlarr_indexers import (
            find_indexer_definition,
        )

        schema = [{"name": "YTS", "implementation": "Cardigann"}]
        assert find_indexer_definition(schema, "Nope") is None

    def test_case_insensitive_match(self):
        from media_pilot.deployment.prowlarr_indexers import (
            find_indexer_definition,
        )

        schema = [{"name": "The Pirate Bay", "implementation": "Cardigann"}]
        entry = find_indexer_definition(schema, "the pirate bay")
        assert entry is not None
        assert entry["name"] == "The Pirate Bay"


class TestIsIndexerInstalled:
    def test_returns_true_when_name_present(self):
        from media_pilot.deployment.prowlarr_indexers import is_indexer_installed

        installed = [{"name": "YTS", "id": 7}, {"name": "Other", "id": 8}]
        assert is_indexer_installed(installed, "YTS") is True

    def test_returns_false_when_absent(self):
        from media_pilot.deployment.prowlarr_indexers import is_indexer_installed

        installed = [{"name": "Other", "id": 8}]
        assert is_indexer_installed(installed, "YTS") is False


# ── 端到端 bootstrap ──


class TestBootstrapPublicIndexers:
    def _make_schema(self) -> list[dict]:
        """模拟 Prowlarr 返回的 indexer schema (公共 + 私有/成人)."""
        public_names = [
            "YTS", "The Pirate Bay", "LimeTorrents", "Nyaa.si", "Mikan",
            "dmhy", "ACG.RIP",
        ]
        schema = [
            {"name": name, "implementation": "Cardigann", "id": idx, "enable": True}
            for idx, name in enumerate(public_names, start=1)
        ]
        # 模拟 Prowlarr 也会返回成人 / 私有源
        schema.append(
            {"name": "sukebei.nyaa.si", "implementation": "Cardigann", "id": 99}
        )
        return schema

    def test_creates_all_missing_public_indexers(self):
        from media_pilot.deployment.prowlarr_indexers import (
            bootstrap_public_indexers,
        )

        api = _FakeProwlarrAPI(schema=self._make_schema(), installed=[])
        result = bootstrap_public_indexers(api)
        assert result.created_count == 7
        assert result.skipped_count == 0
        assert result.failed_count == 0
        created_names = {c["name"] for c in api.created}
        assert "YTS" in created_names
        assert "sukebei.nyaa.si" not in created_names, (
            "默认集合不应该把 sukebei.nyaa.si 加进去"
        )

    def test_skips_existing_indexers(self):
        from media_pilot.deployment.prowlarr_indexers import (
            bootstrap_public_indexers,
        )

        api = _FakeProwlarrAPI(
            schema=self._make_schema(),
            installed=[{"name": "YTS", "id": 5}],  # 用户已装 YTS
        )
        result = bootstrap_public_indexers(api)
        assert result.created_count == 6  # 7 - 1 已存在
        assert result.skipped_count == 1
        assert "YTS" not in {c["name"] for c in api.created}
        # 已存在的 YTS 不得被覆盖 (没出现在 created 列表)
        assert all(c.get("name") != "YTS" for c in api.created)

    def test_replaces_schema_app_profile_zero_with_default_profile_id(self):
        from media_pilot.deployment.prowlarr_indexers import (
            bootstrap_public_indexers,
        )

        api = _FakeProwlarrAPI(
            schema=[
                {
                    "name": "YTS",
                    "implementation": "Cardigann",
                    "id": 1,
                    "appProfileId": 0,
                },
            ],
            installed=[],
            app_profiles=[{"name": "Standard", "id": 7}],
        )
        result = bootstrap_public_indexers(api)
        assert result.created_count == 1
        assert api.created[0]["appProfileId"] == 7
        assert "id" not in api.created[0]

    def test_missing_app_profile_aborts(self):
        from media_pilot.deployment.prowlarr_indexers import (
            ProwlarrInitError,
            bootstrap_public_indexers,
        )

        api = _FakeProwlarrAPI(
            schema=self._make_schema(),
            installed=[],
            app_profiles=[],
        )
        with pytest.raises(ProwlarrInitError):
            bootstrap_public_indexers(api)

    def test_does_not_overwrite_user_modified_indexer(self):
        """用户改过 YTS 的字段, bootstrap 不得重新 POST 覆盖."""
        from media_pilot.deployment.prowlarr_indexers import (
            bootstrap_public_indexers,
        )

        user_yts = {
            "name": "YTS",
            "id": 5,
            "enable": True,
            "fields": [{"name": "baseUrl", "value": "https://user-custom.example/"}],
        }
        api = _FakeProwlarrAPI(
            schema=self._make_schema(),
            installed=[user_yts],
        )
        bootstrap_public_indexers(api)
        # YTS 没被重新创建
        yts_created = [c for c in api.created if c.get("name") == "YTS"]
        assert yts_created == []

    def test_single_failure_does_not_block_others(self):
        from media_pilot.deployment.prowlarr_indexers import (
            bootstrap_public_indexers,
        )

        api = _FakeProwlarrAPI(
            schema=self._make_schema(),
            installed=[],
            fail_on_create={"YTS"},  # 模拟 YTS 创建失败
        )
        result = bootstrap_public_indexers(api)
        # YTS 失败, 其余 6 个成功
        assert result.failed_count == 1
        assert result.created_count == 6
        failed_names = {f["name"] for f in result.failed}
        assert "YTS" in failed_names
        # YTS 没出现在 created 列表
        assert "YTS" not in {c["name"] for c in api.created}

    def test_no_failure_blocks_main_service(self):
        """即便有失败, 也必须返回 ok (主服务可继续启动)."""
        from media_pilot.deployment.prowlarr_indexers import (
            bootstrap_public_indexers,
        )

        api = _FakeProwlarrAPI(
            schema=self._make_schema(),
            installed=[],
            fail_on_create={"YTS", "dmhy"},
        )
        result = bootstrap_public_indexers(api)
        # 状态字段: 只有全部失败或 API 完全不可用才 abort
        assert result.status == "ok"
        assert result.api_unreachable is False

    def test_unable_to_reach_prowlarr_api_aborts(self):
        from media_pilot.deployment.prowlarr_indexers import (
            bootstrap_public_indexers,
            ProwlarrInitError,
        )
        import httpx

        api = MagicMock()
        # 模拟 Prowlarr 一直不可达: 每次调用都抛 ConnectError
        api.list_indexer_schema.side_effect = httpx.ConnectError("connection refused")
        api.list_indexers.side_effect = httpx.ConnectError("connection refused")

        # 使用很短的 timeout 加速测试
        with pytest.raises(ProwlarrInitError) as ei:
            bootstrap_public_indexers(
                api,
                ready_timeout_seconds=0.5,
                ready_retry_delay=0.1,
            )
        # 错误信息必须说明 API 在等待时间内未就绪
        assert "未就绪" in str(ei.value) or "不可达" in str(ei.value) or "refused" in str(ei.value)

    def test_schema_lookup_skips_missing_default(self):
        """schema 中找不到的默认 indexer 应当被 warning 跳过, 不阻塞."""
        from media_pilot.deployment.prowlarr_indexers import (
            bootstrap_public_indexers,
        )

        # 只放一半的 schema, YTS 不在
        partial_schema = [
            {"name": "The Pirate Bay", "implementation": "Cardigann", "id": 1},
            {"name": "LimeTorrents", "implementation": "Cardigann", "id": 2},
        ]
        api = _FakeProwlarrAPI(schema=partial_schema, installed=[])
        result = bootstrap_public_indexers(api)
        # 至少这 2 个被创建, YTS 等被列为 "not in schema"
        assert result.created_count == 2
        missing_names = {m["name"] for m in result.missing_from_schema}
        assert "YTS" in missing_names
        assert "sukebei.nyaa.si" not in missing_names  # 默认集合本身不该有


# ── Prowlarr API 客户端 (httpx) ──


class TestHttpxProwlarrClient:
    def test_list_indexer_schema_calls_endpoint(self, httpx_mock):
        from media_pilot.deployment.prowlarr_indexers import HttpxProwlarrClient

        httpx_mock.add_response(
            url="http://prowlarr:9696/api/v1/indexer/schema",
            json=[{"name": "YTS", "implementation": "Cardigann", "id": 1}],
        )
        client = HttpxProwlarrClient("http://prowlarr:9696", api_key="key-abc")
        schema = client.list_indexer_schema()
        assert schema == [{"name": "YTS", "implementation": "Cardigann", "id": 1}]

    def test_list_indexers_calls_endpoint(self, httpx_mock):
        from media_pilot.deployment.prowlarr_indexers import HttpxProwlarrClient

        httpx_mock.add_response(
            url="http://prowlarr:9696/api/v1/indexer",
            json=[{"name": "YTS", "id": 7}],
        )
        client = HttpxProwlarrClient("http://prowlarr:9696", api_key="key-abc")
        installed = client.list_indexers()
        assert installed == [{"name": "YTS", "id": 7}]

    def test_list_app_profiles_calls_endpoint(self, httpx_mock):
        from media_pilot.deployment.prowlarr_indexers import HttpxProwlarrClient

        httpx_mock.add_response(
            url="http://prowlarr:9696/api/v1/appProfile",
            json=[{"name": "Standard", "id": 1}],
        )
        client = HttpxProwlarrClient("http://prowlarr:9696", api_key="key-abc")
        profiles = client.list_app_profiles()
        assert profiles == [{"name": "Standard", "id": 1}]

    def test_add_indexer_posts_payload(self, httpx_mock):
        from media_pilot.deployment.prowlarr_indexers import HttpxProwlarrClient

        httpx_mock.add_response(
            url="http://prowlarr:9696/api/v1/indexer",
            method="POST",
            json={"name": "YTS", "id": 1, "enable": True},
            status_code=201,
        )
        client = HttpxProwlarrClient("http://prowlarr:9696", api_key="key-abc")
        result = client.add_indexer({"name": "YTS", "implementation": "Cardigann"})
        assert result["name"] == "YTS"
        assert result["id"] == 1

    def test_unauthorized_raises(self, httpx_mock):
        import httpx as _httpx

        from media_pilot.deployment.prowlarr_indexers import HttpxProwlarrClient

        httpx_mock.add_response(
            url="http://prowlarr:9696/api/v1/indexer",
            status_code=401,
        )
        client = HttpxProwlarrClient("http://prowlarr:9696", api_key="bad")
        with pytest.raises(_httpx.HTTPStatusError):
            client.list_indexers()


# ── CLI 入口 ──


class TestCliEntry:
    def test_cli_aborts_when_api_unreachable(self, monkeypatch, capsys):
        import httpx

        from media_pilot.deployment.prowlarr_indexers import main

        # 模拟 Prowlarr 一直不可达: 每次调用都抛 ConnectError, 重试耗尽后 abort
        class _Boom:
            def list_indexer_schema(self):
                raise httpx.ConnectError("refused")

            def list_app_profiles(self):
                raise httpx.ConnectError("refused")

            def list_indexers(self):
                raise httpx.ConnectError("refused")

        monkeypatch.setattr(
            "media_pilot.deployment.prowlarr_indexers.build_default_client",
            lambda: _Boom(),
        )
        # 缩短短路常量加速测试
        monkeypatch.setattr(
            "media_pilot.deployment.prowlarr_indexers.PROWLARR_API_READY_TIMEOUT_SECONDS",
            0.5,
        )
        monkeypatch.setattr(
            "media_pilot.deployment.prowlarr_indexers.PROWLARR_API_READY_RETRY_DELAY",
            0.1,
        )
        rc = main([])
        assert rc == 1
        captured = capsys.readouterr()
        assert "ERROR" in captured.err or "unreachable" in captured.err.lower()

    def test_cli_returns_0_on_full_success(self, monkeypatch, capsys):
        from media_pilot.deployment.prowlarr_indexers import main

        class _OK:
            def list_indexer_schema(self):
                return [
                    {"name": "YTS", "implementation": "Cardigann", "id": 1},
                ]

            def list_indexers(self):
                return []

            def list_app_profiles(self):
                return [{"name": "Standard", "id": 1}]

            def add_indexer(self, definition):
                return {**definition, "id": 100}

        monkeypatch.setattr(
            "media_pilot.deployment.prowlarr_indexers.build_default_client",
            lambda: _OK(),
        )
        rc = main([])
        # 至少有 YTS 成功, 缺其它公共 indexer (作为 not in schema 跳过)
        assert rc == 0

    def test_cli_returns_0_when_single_indexer_create_fails(
        self, monkeypatch, capsys
    ):
        """单个 indexer 创建失败仍 exit 0.

        这是 docker-compose 依赖门禁的关键语义: media-pilot 主服务
        depends_on media-pilot-prowlarr-init service_completed_successfully,
        因此 init 容器在 Prowlarr API 可达的前提下, 单项失败必须返回 0
        而不是 1, 否则主服务会被一条 indexer 反复失败拖死.
        """
        from media_pilot.deployment.prowlarr_indexers import main

        # 强制 schema 只包含 YTS, 其它默认 indexer 都作为 schema-missing
        # 跳过, 失败场景单一可控.
        api = _FakeProwlarrAPI(
            schema=[{"name": "YTS", "implementation": "Cardigann", "id": 1}],
            installed=[],
            fail_on_create={"YTS"},
        )
        monkeypatch.setattr(
            "media_pilot.deployment.prowlarr_indexers.build_default_client",
            lambda: api,
        )
        rc = main([])
        # 单项失败 (YTS 上游错误) 不能让 init 容器 exit 1
        assert rc == 0
        captured = capsys.readouterr()
        # 警告信息必须进入 stderr, 让用户能在 docker compose logs 里看到
        assert "WARN" in captured.err
        assert "YTS" in captured.err


# ── API ready bounded retry ──


class TestWithApiRetry:
    def test_transient_failure_then_success(self):
        """前几次抛 ConnectError, 后面成功 → 最终返回成功值."""
        from media_pilot.deployment.prowlarr_indexers import with_api_retry
        import httpx

        attempts = {"n": 0}

        def flaky():
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise httpx.ConnectError(f"refused attempt {attempts['n']}")
            return "ok"

        result = with_api_retry(
            flaky,
            label="flaky",
            timeout_seconds=5.0,
            delay_seconds=0.01,
        )
        assert result == "ok"
        assert attempts["n"] == 3  # 2 次失败 + 1 次成功

    def test_persistent_connection_failure_raises_init_error(self):
        """持续 ConnectError 超出 timeout → ProwlarrInitError, 错误信息清晰."""
        from media_pilot.deployment.prowlarr_indexers import (
            ProwlarrInitError,
            with_api_retry,
        )
        import httpx

        def always_fails():
            raise httpx.ConnectError("connection refused")

        with pytest.raises(ProwlarrInitError) as ei:
            with_api_retry(
                always_fails,
                label="test-call",
                timeout_seconds=0.3,
                delay_seconds=0.1,
            )
        msg = str(ei.value)
        # 必须提示用户 API 在等待时间内未就绪
        assert "未就绪" in msg
        assert "test-call" in msg
        # 错误必须带原始异常 (chain)
        assert "connection refused" in msg

    def test_http_status_error_not_retried(self):
        """HTTP 4xx/5xx 错误 (Prowlarr 已就绪但拒绝请求) 立即抛, 不重试."""
        from media_pilot.deployment.prowlarr_indexers import with_api_retry
        import httpx

        attempts = {"n": 0}

        def http_401():
            attempts["n"] += 1
            # 构造一个 401 响应并 raise
            request = httpx.Request("GET", "http://prowlarr/api")
            response = httpx.Response(401, request=request)
            raise httpx.HTTPStatusError(
                "unauthorized", request=request, response=response
            )

        with pytest.raises(httpx.HTTPStatusError):
            with_api_retry(
                http_401,
                label="http-call",
                timeout_seconds=5.0,
                delay_seconds=0.01,
            )
        # 4xx 错误不应重试, 只能调一次
        assert attempts["n"] == 1

    def test_timeout_exception_retried(self):
        """httpx.TimeoutException 也属于网络瞬时错误, 应被重试."""
        from media_pilot.deployment.prowlarr_indexers import with_api_retry
        import httpx

        attempts = {"n": 0}

        def slow():
            attempts["n"] += 1
            if attempts["n"] < 2:
                raise httpx.ConnectTimeout("timeout")
            return "ok"

        result = with_api_retry(
            slow,
            label="slow",
            timeout_seconds=5.0,
            delay_seconds=0.01,
        )
        assert result == "ok"
        assert attempts["n"] == 2


class TestBootstrapRetryEndToEnd:
    def test_transient_connect_error_then_success(self, monkeypatch):
        """端到端: 第一次 list_indexer_schema 失败, 第二次成功, 整体仍 ok."""
        import httpx

        from media_pilot.deployment.prowlarr_indexers import (
            bootstrap_public_indexers,
        )

        # 缩短短路常量
        monkeypatch.setattr(
            "media_pilot.deployment.prowlarr_indexers.PROWLARR_API_READY_TIMEOUT_SECONDS",
            5.0,
        )
        monkeypatch.setattr(
            "media_pilot.deployment.prowlarr_indexers.PROWLARR_API_READY_RETRY_DELAY",
            0.01,
        )

        class _FlakyAPI:
            def __init__(self):
                self._n = 0

            def list_indexer_schema(self):
                self._n += 1
                if self._n == 1:
                    raise httpx.ConnectError("first attempt refused")
                return [
                    {"name": "YTS", "implementation": "Cardigann", "id": 1},
                ]

            def list_indexers(self):
                return []

            def list_app_profiles(self):
                return [{"name": "Standard", "id": 1}]

            def add_indexer(self, definition):
                return {**definition, "id": 100}

        result = bootstrap_public_indexers(_FlakyAPI())
        assert result.status == "ok"
        assert result.created_count == 1
        assert result.api_unreachable is False

    def test_single_indexer_create_failure_still_warns_only(self, monkeypatch):
        """单个 indexer POST 失败 → warning + 继续, 不阻塞整体."""
        import httpx

        from media_pilot.deployment.prowlarr_indexers import (
            bootstrap_public_indexers,
        )

        monkeypatch.setattr(
            "media_pilot.deployment.prowlarr_indexers.PROWLARR_API_READY_TIMEOUT_SECONDS",
            5.0,
        )
        monkeypatch.setattr(
            "media_pilot.deployment.prowlarr_indexers.PROWLARR_API_READY_RETRY_DELAY",
            0.01,
        )

        class _OKSchemaThenFailOnYTSCreate:
            def list_indexer_schema(self):
                return [
                    {"name": "YTS", "implementation": "Cardigann", "id": 1},
                    {"name": "The Pirate Bay", "implementation": "Cardigann", "id": 2},
                ]

            def list_indexers(self):
                return []

            def list_app_profiles(self):
                return [{"name": "Standard", "id": 1}]

            def add_indexer(self, definition):
                if definition.get("name") == "YTS":
                    raise RuntimeError("upstream says no")
                return {**definition, "id": 100}

        result = bootstrap_public_indexers(_OKSchemaThenFailOnYTSCreate())
        assert result.status == "ok"
        assert result.failed_count == 1
        assert result.created_count == 1
        assert {f["name"] for f in result.failed} == {"YTS"}
        assert {c["name"] for c in result.created} == {"The Pirate Bay"}
