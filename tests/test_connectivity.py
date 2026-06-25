"""连通性探测测试"""

from pathlib import Path

from media_pilot.config import AppConfig
from media_pilot.services.connectivity import probe_llm, probe_tmdb, probe_tpdb


def test_probe_tmdb_not_configured() -> None:
    config = AppConfig(
        downloads_dir=Path("/tmp"),
        watch_dir=Path("/tmp/watch"),
        workspace_dir=Path("/tmp"),
        movies_dir=Path("/tmp"),
        shows_dir=Path("/tmp"),
        database_dir=Path("/tmp"),
        tmdb_api_key=None,
    )
    status, message, latency = probe_tmdb(config)
    assert status == "not_configured"
    assert "未配置" in message
    assert latency is None


def test_probe_tpdb_not_configured() -> None:
    config = AppConfig(
        downloads_dir=Path("/tmp"),
        watch_dir=Path("/tmp/watch"),
        workspace_dir=Path("/tmp"),
        movies_dir=Path("/tmp"),
        shows_dir=Path("/tmp"),
        database_dir=Path("/tmp"),
        tpdb_api_key=None,
    )
    status, message, latency = probe_tpdb(config)
    assert status == "not_configured"
    assert latency is None


def test_probe_tpdb_key_set_but_not_reachable() -> None:
    config = AppConfig(
        downloads_dir=Path("/tmp"),
        watch_dir=Path("/tmp/watch"),
        workspace_dir=Path("/tmp"),
        movies_dir=Path("/tmp"),
        shows_dir=Path("/tmp"),
        database_dir=Path("/tmp"),
        tpdb_api_key="test-key",
    )
    status, message, latency = probe_tpdb(config)
    # 用测试 key 无法真正连接，预期 connection failure
    assert status in ("failed", "ok")


def test_probe_llm_not_configured() -> None:
    config = AppConfig(
        downloads_dir=Path("/tmp"),
        watch_dir=Path("/tmp/watch"),
        workspace_dir=Path("/tmp"),
        movies_dir=Path("/tmp"),
        shows_dir=Path("/tmp"),
        database_dir=Path("/tmp"),
        llm_api_key=None,
    )
    status, message, latency = probe_llm(config)
    assert status == "not_configured"
    assert latency is None


def test_probe_llm_missing_base_url() -> None:
    config = AppConfig(
        downloads_dir=Path("/tmp"),
        watch_dir=Path("/tmp/watch"),
        workspace_dir=Path("/tmp"),
        movies_dir=Path("/tmp"),
        shows_dir=Path("/tmp"),
        database_dir=Path("/tmp"),
        llm_api_key="test-key",
        llm_base_url=None,
    )
    status, message, latency = probe_llm(config)
    assert status == "not_configured"


def test_run_all_probes_returns_three_results() -> None:
    from media_pilot.services.connectivity import run_all_probes

    config = AppConfig(
        downloads_dir=Path("/tmp"),
        watch_dir=Path("/tmp/watch"),
        workspace_dir=Path("/tmp"),
        movies_dir=Path("/tmp"),
        shows_dir=Path("/tmp"),
        database_dir=Path("/tmp"),
    )
    results = run_all_probes(config)
    assert len(results) == 5
    providers = {r["provider"] for r in results}
    assert providers == {"tmdb", "tpdb", "llm", "prowlarr", "qbittorrent"}
    for r in results:
        assert "provider" in r
        assert "status" in r
        assert "message" in r
        assert "checked_at" in r
        assert "latency_ms" in r
