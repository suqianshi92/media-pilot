"""Shared metadata detail draft service -- fetch full detail + supplementary data.

Returns a typed ``MetadataDraft`` without persisting anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from media_pilot.adapters.metadata import MetadataDetail
from media_pilot.config import AppConfig


@dataclass(frozen=True, kw_only=True)
class MetadataDraft:
    detail: MetadataDetail
    directors: list[dict] = field(default_factory=list)
    actors: list[dict] = field(default_factory=list)
    imdb_id: str | None = None
    poster_url: str | None = None
    backdrop_url: str | None = None
    logo_url: str | None = None


def _normalize_tmdb_provider_id(provider_id: str, media_type: str) -> str:
    """把裸数字 provider_id 归一化为前缀形式.

    - ``"68735"`` (movie) → ``"movie:68735"``
    - ``"123"`` (show) → ``"show:123"``
    - ``"movie:68735"`` / ``"show:123"`` 原样返回 (不双前缀)
    - 其它形式 (字母 / 浮点 / 多余冒号 / 错误前缀) 原样透传, 由 provider
      adapter parser 走 invalid_provider_id 错误码.
    """
    if not provider_id:
        return provider_id
    expected_prefix = "movie" if media_type == "movie" else "show"
    # 已有正确前缀, 原样返回
    if provider_id.startswith(f"{expected_prefix}:"):
        return provider_id
    # 裸数字 → 拼前缀
    if provider_id.isdigit():
        return f"{expected_prefix}:{provider_id}"
    return provider_id


def fetch_metadata_draft(
    *,
    config: AppConfig,
    provider_name: str,
    provider_id: str,
    media_type: str,
    language_priority: list[str],
) -> MetadataDraft:
    """Fetch metadata detail and best-effort supplementary data from a provider.

    Raises ``ValueError`` if the provider name is unknown.
    Raises the underlying provider exception if the main detail call fails.
    """
    from media_pilot.adapters.factory import create_metadata_provider_by_name

    provider = create_metadata_provider_by_name(config, provider_name)

    # TMDB provider_id 归一化: 接受裸数字 ("68735" / "123") 与带前缀
    # ("movie:68735" / "show:123") 两种形式. 内部统一用前缀形式派发
    # 与持久化, 避免 LLM 在不同调用里拼出两种形式导致下游判定漂移.
    # TPDB 不动 (走不同 protocol, 不会撞这条 parser).
    if provider_name == "tmdb":
        provider_id = _normalize_tmdb_provider_id(provider_id, media_type)

    if media_type == "movie":
        detail_resp = provider.get_movie_details(provider_id, language_priority=language_priority)
    else:
        detail_resp = provider.get_show_details(provider_id, language_priority=language_priority)

    if detail_resp.error is not None:
        raise ProviderError(
            f"{detail_resp.error.code}: {detail_resp.error.message}",
            code=detail_resp.error.code,
            provider_message=detail_resp.error.message,
        )

    detail = detail_resp.value
    if detail is None:
        raise ProviderError("Provider returned empty detail")

    # Supplementary data — best-effort
    directors: list[dict] = []
    actors: list[dict] = []
    imdb_id: str | None = None
    poster_url: str | None = None
    backdrop_url: str | None = None
    logo_url: str | None = None

    try:
        if media_type == "movie":
            credits_resp = provider.get_movie_credits(provider_id)
            ext_resp = provider.get_movie_external_ids(provider_id)
            img_resp = provider.get_movie_images(provider_id, language_priority=language_priority)
        else:
            credits_resp = provider.get_show_credits(provider_id)
            ext_resp = provider.get_show_external_ids(provider_id)
            img_resp = provider.get_show_images(provider_id, language_priority=language_priority)

        if credits_resp and credits_resp.value:
            directors = [{"name": d.name, "role": d.role} for d in credits_resp.value.directors]
            actors = [{"name": a.name, "role": a.role} for a in credits_resp.value.actors]
        if ext_resp and ext_resp.value:
            imdb_id = ext_resp.value.imdb_id
        if img_resp and img_resp.value:
            poster_url = img_resp.value.poster_url
            backdrop_url = img_resp.value.backdrop_url
            logo_url = img_resp.value.logo_url
    except Exception:
        pass

    return MetadataDraft(
        detail=detail,
        directors=directors,
        actors=actors,
        imdb_id=imdb_id,
        poster_url=poster_url,
        backdrop_url=backdrop_url,
        logo_url=logo_url,
    )


class ProviderError(Exception):
    """Structured error from a metadata provider."""

    def __init__(self, msg: str, *, code: str = "", provider_message: str = "") -> None:
        super().__init__(msg)
        self.code = code
        self.provider_message = provider_message
