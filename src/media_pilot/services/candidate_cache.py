"""
进程内资源候选缓存 — TTL + 容量上限

首版不持久化，使用内存 dict + token 机制。
"""

from __future__ import annotations

import time
from typing import Any

# 结构: {token: {"candidate": ResourceCandidate, "created_at": float}}
_CANDIDATE_CACHE: dict[str, dict[str, Any]] = {}
_CANDIDATE_TTL_SECONDS = 30 * 60  # 30 分钟
_CANDIDATE_MAX_SIZE = 1000  # 容量上限


def _prune_cache() -> None:
    """清理过期和超量缓存项"""
    now = time.time()
    expired = [t for t, e in _CANDIDATE_CACHE.items()
               if now - e["created_at"] > _CANDIDATE_TTL_SECONDS]
    for t in expired:
        _CANDIDATE_CACHE.pop(t, None)
    if len(_CANDIDATE_CACHE) > _CANDIDATE_MAX_SIZE:
        sorted_tokens = sorted(
            _CANDIDATE_CACHE.items(), key=lambda kv: kv[1]["created_at"]
        )
        excess = len(_CANDIDATE_CACHE) - _CANDIDATE_MAX_SIZE
        for t, _ in sorted_tokens[:excess]:
            _CANDIDATE_CACHE.pop(t, None)


def store_candidate(
    candidate,
    intent_context: dict | None = None,
    *,
    owner_user_id: str | None = None,
    is_adult: bool = False,
) -> str:
    """将候选存入缓存，返回不可预测 token"""
    _prune_cache()
    import secrets as _secrets
    token = _secrets.token_urlsafe(24)
    _CANDIDATE_CACHE[token] = {
        "candidate": candidate,
        "intent_context": intent_context or {},
        "owner_user_id": owner_user_id,
        "is_adult": is_adult,
        "created_at": time.time(),
    }
    return token


def lookup_candidate(
    token: str,
    *,
    owner_user_id: str | None = None,
    can_access_adult: bool = True,
):
    """从缓存取候选及其意图上下文，过期返回 None"""
    entry = _CANDIDATE_CACHE.get(token)
    if entry is None:
        return None, {}
    if time.time() - entry["created_at"] > _CANDIDATE_TTL_SECONDS:
        _CANDIDATE_CACHE.pop(token, None)
        return None, {}
    if owner_user_id is not None and entry.get("owner_user_id") != owner_user_id:
        return None, {}
    if entry.get("is_adult", False) and not can_access_adult:
        return None, {}
    return entry["candidate"], entry.get("intent_context", {})
