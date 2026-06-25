"""连通性探测服务 — TMDB / TPDB / LLM 配置状态和网络连通性"""

from __future__ import annotations

import time
from datetime import UTC, datetime

import httpx

from media_pilot.config import AppConfig
from media_pilot.services.resource_discovery import probe_prowlarr, probe_qbittorrent

_PROBE_TIMEOUT = 5.0  # 每个探测独立超时


def _dict_to_tuple(d: dict) -> tuple[str, str, int | None]:
    return (d["status"], d["message"], d.get("latency_ms"))


def probe_tmdb(config: AppConfig) -> tuple[str, str, int | None]:
    """探测 TMDB API 连通性"""
    if not config.tmdb_api_key:
        return ("not_configured", "未配置 TMDB API Key", None)

    start = time.monotonic()
    try:
        url = f"{config.tmdb_base_url}/configuration"
        params = {"api_key": config.tmdb_api_key}
        resp = httpx.get(url, params=params, timeout=_PROBE_TIMEOUT)
        resp.raise_for_status()
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return ("ok", "TMDB API 连接正常", elapsed_ms)
    except httpx.HTTPStatusError as exc:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return ("failed", f"TMDB API 返回错误: {exc.response.status_code}", elapsed_ms)
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return ("failed", f"TMDB 连接失败: {exc}", elapsed_ms)


def probe_tpdb(config: AppConfig) -> tuple[str, str, int | None]:
    """探测 TPDB API 连通性"""
    if not config.tpdb_api_key:
        return ("not_configured", "未配置 TPDB API Key", None)
    try:
        from media_pilot.adapters.tpdb import TpdbAdultProvider
        provider = TpdbAdultProvider.from_config(config)
        ok, message, latency_ms = provider.ping()
        return ("ok" if ok else "failed", message, latency_ms)
    except Exception as exc:
        return ("failed", f"TPDB 探测异常: {type(exc).__name__}", None)


def probe_llm(config: AppConfig) -> tuple[str, str, int | None]:
    """探测 LLM API 连通性 — 不泄露密钥"""
    if not config.llm_api_key:
        return ("not_configured", "未配置 LLM API Key", None)
    if not config.llm_base_url:
        return ("not_configured", "未配置 LLM Base URL", None)
    if not config.llm_model:
        return ("not_configured", "未配置 LLM Model", None)

    start = time.monotonic()
    try:
        from openai import OpenAI
        client = OpenAI(
            api_key=config.llm_api_key,
            base_url=config.llm_base_url,
            timeout=config.llm_timeout_seconds,
        )
        # 使用最小 tokens 的请求验证连通性
        client.chat.completions.create(
            model=config.llm_model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
        )
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return ("ok", "LLM API 连接正常", elapsed_ms)
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        # 不返回完整错误信息，避免泄露密钥或 URL
        return ("failed", f"LLM 连接失败: {type(exc).__name__}", elapsed_ms)


# ── 连通性探测 ──


def run_all_probes(config: AppConfig) -> list[dict]:
    """执行全部探测，返回 ProbeResultDto 字典列表"""
    now = datetime.now(UTC).isoformat()

    results: list[dict] = []

    for provider, probe_fn in [
        ("tmdb", lambda: probe_tmdb(config)),
        ("tpdb", lambda: probe_tpdb(config)),
        ("llm", lambda: probe_llm(config)),
        ("prowlarr", lambda: _dict_to_tuple(probe_prowlarr(config))),
        ("qbittorrent", lambda: _dict_to_tuple(probe_qbittorrent(config))),
    ]:
        status, message, latency_ms = probe_fn()
        results.append({
            "provider": provider,
            "status": status,
            "message": message,
            "checked_at": now,
            "latency_ms": latency_ms,
        })

    return results
