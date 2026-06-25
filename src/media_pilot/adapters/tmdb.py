from collections.abc import Callable
from dataclasses import dataclass
from difflib import SequenceMatcher
from urllib.parse import urljoin

import httpx

from media_pilot.adapters.metadata import (
    MetadataCandidate,
    MetadataCredits,
    MetadataDetail,
    MetadataExternalIds,
    MetadataImages,
    MetadataPerson,
    MetadataProviderError,
    MetadataProviderResponse,
)
from media_pilot.config import AppConfig


@dataclass
class TmdbMovieProvider:
    api_key: str
    base_url: str = "https://api.themoviedb.org/3"
    language_priority: tuple[str, ...] = ("zh-CN", "en-US")
    timeout_seconds: float = 10.0
    image_base_url: str = "https://image.tmdb.org/t/p"
    poster_size: str = "w780"
    backdrop_size: str = "w1280"
    logo_size: str = "w500"
    profile_size: str = "w185"
    client: httpx.Client | None = None
    call_logger: Callable[[dict], None] | None = None

    provider_name: str = "tmdb"

    @classmethod
    def from_config(
        cls,
        config: AppConfig,
        *,
        client: httpx.Client | None = None,
    ) -> "TmdbMovieProvider":
        if not config.tmdb_api_key:
            raise ValueError("tmdb_api_key is required for TMDB provider")
        return cls(
            api_key=config.tmdb_api_key,
            base_url=config.tmdb_base_url,
            language_priority=config.tmdb_language_priority,
            timeout_seconds=config.tmdb_timeout_seconds,
            image_base_url=config.tmdb_image_base_url,
            poster_size=config.tmdb_poster_size,
            backdrop_size=config.tmdb_backdrop_size,
            logo_size=config.tmdb_logo_size,
            profile_size=config.tmdb_profile_size,
            client=client,
        )

    def search_movie(
        self,
        keyword: str,
        *,
        language_priority: list[str],
    ) -> MetadataProviderResponse[list[MetadataCandidate]]:
        response = self._request_json(
            "/search/movie",
            {
                "query": keyword,
                "language": (
                    language_priority[0] if language_priority else self.language_priority[0]
                ),
            },
        )
        if response.error is not None:
            return response

        keyword_title, keyword_year = _parse_keyword(keyword)
        candidates: list[MetadataCandidate] = []
        for index, item in enumerate(response.value.get("results", [])):
            title = item.get("title") or item.get("original_title") or "Unknown"
            year = _year_from_date(item.get("release_date"))
            confidence = _candidate_confidence(
                keyword_title=keyword_title,
                keyword_year=keyword_year,
                candidate_title=title,
                candidate_original_title=item.get("original_title"),
                candidate_year=year,
                rank=index,
            )
            candidates.append(
                MetadataCandidate(
                    provider=self.provider_name,
                    provider_id=f"movie:{item['id']}",
                    title=title,
                    original_title=item.get("original_title"),
                    year=year,
                    media_type="movie",
                    overview=item.get("overview"),
                    poster_url=self._image_url(item.get("poster_path"), self.poster_size),
                    confidence=confidence,
                    match_reason=_match_reason(
                        keyword_title=keyword_title,
                        keyword_year=keyword_year,
                        candidate_title=title,
                        candidate_original_title=item.get("original_title"),
                        candidate_year=year,
                        rank=index,
                    ),
                    payload={
                        "tmdb_id": item["id"],
                        "request_keyword": keyword,
                        "language": language_priority[0] if language_priority else None,
                        "raw": item,
                    },
                )
            )
        return MetadataProviderResponse(value=candidates)

    def search_show(
        self,
        keyword: str,
        *,
        language_priority: list[str],
    ) -> MetadataProviderResponse[list[MetadataCandidate]]:
        response = self._request_json(
            "/search/tv",
            {
                "query": keyword,
                "language": (
                    language_priority[0] if language_priority else self.language_priority[0]
                ),
            },
        )
        if response.error is not None:
            return response

        keyword_title, keyword_year = _parse_keyword(keyword)
        candidates: list[MetadataCandidate] = []
        for index, item in enumerate(response.value.get("results", [])):
            title = item.get("name") or item.get("original_name") or "Unknown"
            year = _year_from_date(item.get("first_air_date"))
            confidence = _candidate_confidence(
                keyword_title=keyword_title,
                keyword_year=keyword_year,
                candidate_title=title,
                candidate_original_title=item.get("original_name"),
                candidate_year=year,
                rank=index,
            )
            candidates.append(
                MetadataCandidate(
                    provider=self.provider_name,
                    provider_id=f"show:{item['id']}",
                    title=title,
                    original_title=item.get("original_name"),
                    year=year,
                    media_type="show",
                    overview=item.get("overview"),
                    poster_url=self._image_url(item.get("poster_path"), self.poster_size),
                    confidence=confidence,
                    match_reason=_match_reason(
                        keyword_title=keyword_title,
                        keyword_year=keyword_year,
                        candidate_title=title,
                        candidate_original_title=item.get("original_name"),
                        candidate_year=year,
                        rank=index,
                    ),
                    payload={
                        "tmdb_id": item["id"],
                        "request_keyword": keyword,
                        "language": language_priority[0] if language_priority else None,
                        "raw": item,
                    },
                )
            )
        return MetadataProviderResponse(value=candidates)

    def get_movie_details(
        self,
        provider_id: str,
        *,
        language_priority: list[str],
    ) -> MetadataProviderResponse[MetadataDetail]:
        tmdb_id = _tmdb_movie_id(provider_id)
        if tmdb_id is None:
            return _provider_error(
                self.provider_name,
                code="invalid_provider_id",
                message="provider id must look like movie:<tmdb_id>",
                retryable=False,
                payload={"provider_id": provider_id},
            )

        primary_language = language_priority[0] if language_priority else self.language_priority[0]
        primary_response = self._request_json(
            f"/movie/{tmdb_id}",
            {"language": primary_language},
        )
        if primary_response.error is not None:
            return primary_response

        primary_data = primary_response.value
        merged = dict(primary_data)
        field_sources = {
            "title": primary_language,
            "overview": primary_language,
        }
        fallback_language = _fallback_language(language_priority, primary_language)
        if fallback_language is not None and _needs_detail_fallback(primary_data):
            fallback_response = self._request_json(
                f"/movie/{tmdb_id}",
                {"language": fallback_language},
            )
            if fallback_response.error is None:
                fallback_data = fallback_response.value
                if not merged.get("title") and fallback_data.get("title"):
                    merged["title"] = fallback_data["title"]
                    field_sources["title"] = fallback_language
                if not merged.get("overview") and fallback_data.get("overview"):
                    merged["overview"] = fallback_data["overview"]
                    field_sources["overview"] = fallback_language
                if not merged.get("original_title") and fallback_data.get("original_title"):
                    merged["original_title"] = fallback_data["original_title"]

        credits_response = self.get_movie_credits(provider_id)
        if credits_response.error is not None:
            return credits_response
        external_ids_response = self.get_movie_external_ids(provider_id)
        if external_ids_response.error is not None:
            return external_ids_response
        images_response = self.get_movie_images(provider_id, language_priority=language_priority)
        if images_response.error is not None:
            return images_response

        return MetadataProviderResponse(
            value=MetadataDetail(
                provider=self.provider_name,
                provider_id=provider_id,
                media_type="movie",
                title=merged.get("title") or merged.get("original_title") or "Unknown",
                original_title=merged.get("original_title"),
                year=_year_from_date(merged.get("release_date")),
                plot=merged.get("overview"),
                runtime_minutes=merged.get("runtime"),
                premiered=merged.get("release_date"),
                rating=merged.get("vote_average"),
                genres=[genre["name"] for genre in merged.get("genres", []) if genre.get("name")],
                countries=[
                    country["iso_3166_1"]
                    for country in merged.get("production_countries", [])
                    if country.get("iso_3166_1")
                ],
                studios=[
                    company["name"]
                    for company in merged.get("production_companies", [])
                    if company.get("name")
                ],
                credits=credits_response.value or MetadataCredits(),
                external_ids=external_ids_response.value or MetadataExternalIds(None),
                images=images_response.value or MetadataImages(None, None, None),
                payload={
                    "tmdb_id": tmdb_id,
                    "field_sources": field_sources,
                    "raw": merged,
                },
            )
        )

    def get_movie_credits(self, provider_id: str) -> MetadataProviderResponse[MetadataCredits]:
        tmdb_id = _tmdb_movie_id(provider_id)
        if tmdb_id is None:
            return _provider_error(
                self.provider_name,
                code="invalid_provider_id",
                message="provider id must look like movie:<tmdb_id>",
                retryable=False,
                payload={"provider_id": provider_id},
            )

        response = self._request_json(f"/movie/{tmdb_id}/credits", {})
        if response.error is not None:
            return response

        cast = response.value.get("cast", [])
        crew = response.value.get("crew", [])
        return MetadataProviderResponse(
            value=MetadataCredits(
                directors=[
                    self._person_from_tmdb(item, role=item.get("job"))
                    for item in crew
                    if item.get("job") == "Director"
                ],
                actors=[
                    self._person_from_tmdb(item, role=item.get("character"))
                    for item in cast
                ],
                payload={"tmdb_id": tmdb_id, "raw": response.value},
            )
        )

    def get_movie_external_ids(
        self,
        provider_id: str,
    ) -> MetadataProviderResponse[MetadataExternalIds]:
        tmdb_id = _tmdb_movie_id(provider_id)
        if tmdb_id is None:
            return _provider_error(
                self.provider_name,
                code="invalid_provider_id",
                message="provider id must look like movie:<tmdb_id>",
                retryable=False,
                payload={"provider_id": provider_id},
            )

        response = self._request_json(f"/movie/{tmdb_id}/external_ids", {})
        if response.error is not None:
            return response

        return MetadataProviderResponse(
            value=MetadataExternalIds(
                imdb_id=response.value.get("imdb_id"),
                payload={"tmdb_id": tmdb_id, "raw": response.value},
            )
        )

    def get_movie_images(
        self,
        provider_id: str,
        *,
        language_priority: list[str],
    ) -> MetadataProviderResponse[MetadataImages]:
        tmdb_id = _tmdb_movie_id(provider_id)
        if tmdb_id is None:
            return _provider_error(
                self.provider_name,
                code="invalid_provider_id",
                message="provider id must look like movie:<tmdb_id>",
                retryable=False,
                payload={"provider_id": provider_id},
            )

        include_languages = _image_language_query(language_priority or list(self.language_priority))
        response = self._request_json(
            f"/movie/{tmdb_id}/images",
            {"include_image_language": include_languages},
        )
        if response.error is not None:
            return response

        posters = response.value.get("posters", [])
        backdrops = response.value.get("backdrops", [])
        logos = response.value.get("logos", [])
        selected_poster = _select_image(posters, language_priority)
        selected_backdrop = _select_image(backdrops, language_priority)
        selected_logo = _select_image(logos, language_priority)

        warnings: list[str] = []
        if selected_backdrop is None:
            warnings.append("missing_backdrop")
        if selected_logo is None:
            warnings.append("missing_logo")

        return MetadataProviderResponse(
            value=MetadataImages(
                poster_url=self._image_url(
                    None if selected_poster is None else selected_poster.get("file_path"),
                    self.poster_size,
                ),
                backdrop_url=self._image_url(
                    None if selected_backdrop is None else selected_backdrop.get("file_path"),
                    self.backdrop_size,
                ),
                logo_url=self._image_url(
                    None if selected_logo is None else selected_logo.get("file_path"),
                    self.logo_size,
                ),
                payload={
                    "tmdb_id": tmdb_id,
                    "warnings": warnings,
                    "selected_languages": {
                        "poster": None
                        if selected_poster is None
                        else selected_poster.get("iso_639_1"),
                        "backdrop": None
                        if selected_backdrop is None
                        else selected_backdrop.get("iso_639_1"),
                        "logo": None if selected_logo is None else selected_logo.get("iso_639_1"),
                    },
                    "raw": response.value,
                },
            )
        )

    def get_show_details(
        self,
        provider_id: str,
        *,
        language_priority: list[str],
    ) -> MetadataProviderResponse[MetadataDetail]:
        tmdb_id = _tmdb_show_id(provider_id)
        if tmdb_id is None:
            return _provider_error(
                self.provider_name,
                code="invalid_provider_id",
                message="provider id must look like show:<tmdb_id>",
                retryable=False,
                payload={"provider_id": provider_id},
            )

        primary_language = language_priority[0] if language_priority else self.language_priority[0]
        primary_response = self._request_json(
            f"/tv/{tmdb_id}",
            {"language": primary_language},
        )
        if primary_response.error is not None:
            return primary_response

        primary_data = primary_response.value
        merged = dict(primary_data)
        field_sources = {
            "name": primary_language,
            "overview": primary_language,
        }
        fallback_language = _fallback_language(language_priority, primary_language)
        if fallback_language is not None and _needs_show_fallback(primary_data):
            fallback_response = self._request_json(
                f"/tv/{tmdb_id}",
                {"language": fallback_language},
            )
            if fallback_response.error is None:
                fallback_data = fallback_response.value
                if not merged.get("name") and fallback_data.get("name"):
                    merged["name"] = fallback_data["name"]
                    field_sources["name"] = fallback_language
                if not merged.get("overview") and fallback_data.get("overview"):
                    merged["overview"] = fallback_data["overview"]
                    field_sources["overview"] = fallback_language
                if not merged.get("original_name") and fallback_data.get("original_name"):
                    merged["original_name"] = fallback_data["original_name"]

        credits_response = self.get_show_credits(provider_id)
        if credits_response.error is not None:
            return credits_response
        external_ids_response = self.get_show_external_ids(provider_id)
        if external_ids_response.error is not None:
            return external_ids_response
        images_response = self.get_show_images(provider_id, language_priority=language_priority)
        if images_response.error is not None:
            return images_response

        return MetadataProviderResponse(
            value=MetadataDetail(
                provider=self.provider_name,
                provider_id=provider_id,
                media_type="show",
                title=merged.get("name") or merged.get("original_name") or "Unknown",
                original_title=merged.get("original_name"),
                year=_year_from_date(merged.get("first_air_date")),
                plot=merged.get("overview"),
                runtime_minutes=None,
                premiered=merged.get("first_air_date"),
                rating=merged.get("vote_average"),
                genres=[genre["name"] for genre in merged.get("genres", []) if genre.get("name")],
                countries=_show_origin_countries(merged.get("origin_country", [])),
                studios=[
                    company["name"]
                    for company in merged.get("production_companies", [])
                    if company.get("name")
                ],
                credits=credits_response.value or MetadataCredits(),
                external_ids=external_ids_response.value or MetadataExternalIds(None),
                images=images_response.value or MetadataImages(None, None, None),
                payload={
                    "tmdb_id": tmdb_id,
                    "field_sources": field_sources,
                    "raw": merged,
                },
            )
        )

    def get_show_credits(self, provider_id: str) -> MetadataProviderResponse[MetadataCredits]:
        tmdb_id = _tmdb_show_id(provider_id)
        if tmdb_id is None:
            return _provider_error(
                self.provider_name,
                code="invalid_provider_id",
                message="provider id must look like show:<tmdb_id>",
                retryable=False,
                payload={"provider_id": provider_id},
            )

        response = self._request_json(f"/tv/{tmdb_id}/credits", {})
        if response.error is not None:
            return response

        cast = response.value.get("cast", [])
        crew = response.value.get("crew", [])
        return MetadataProviderResponse(
            value=MetadataCredits(
                directors=[
                    self._person_from_tmdb(item, role=item.get("job"))
                    for item in crew
                    if item.get("job") == "Director"
                ],
                actors=[
                    self._person_from_tmdb(item, role=item.get("character"))
                    for item in cast
                ],
                payload={"tmdb_id": tmdb_id, "raw": response.value},
            )
        )

    def get_show_external_ids(
        self,
        provider_id: str,
    ) -> MetadataProviderResponse[MetadataExternalIds]:
        tmdb_id = _tmdb_show_id(provider_id)
        if tmdb_id is None:
            return _provider_error(
                self.provider_name,
                code="invalid_provider_id",
                message="provider id must look like show:<tmdb_id>",
                retryable=False,
                payload={"provider_id": provider_id},
            )

        response = self._request_json(f"/tv/{tmdb_id}/external_ids", {})
        if response.error is not None:
            return response

        return MetadataProviderResponse(
            value=MetadataExternalIds(
                imdb_id=response.value.get("imdb_id"),
                payload={"tmdb_id": tmdb_id, "raw": response.value},
            )
        )

    def get_show_images(
        self,
        provider_id: str,
        *,
        language_priority: list[str],
    ) -> MetadataProviderResponse[MetadataImages]:
        tmdb_id = _tmdb_show_id(provider_id)
        if tmdb_id is None:
            return _provider_error(
                self.provider_name,
                code="invalid_provider_id",
                message="provider id must look like show:<tmdb_id>",
                retryable=False,
                payload={"provider_id": provider_id},
            )

        include_languages = _image_language_query(language_priority or list(self.language_priority))
        response = self._request_json(
            f"/tv/{tmdb_id}/images",
            {"include_image_language": include_languages},
        )
        if response.error is not None:
            return response

        posters = response.value.get("posters", [])
        backdrops = response.value.get("backdrops", [])
        logos = response.value.get("logos", [])
        selected_poster = _select_image(posters, language_priority)
        selected_backdrop = _select_image(backdrops, language_priority)
        selected_logo = _select_image(logos, language_priority)

        warnings: list[str] = []
        if selected_backdrop is None:
            warnings.append("missing_backdrop")
        if selected_logo is None:
            warnings.append("missing_logo")

        return MetadataProviderResponse(
            value=MetadataImages(
                poster_url=self._image_url(
                    None if selected_poster is None else selected_poster.get("file_path"),
                    self.poster_size,
                ),
                backdrop_url=self._image_url(
                    None if selected_backdrop is None else selected_backdrop.get("file_path"),
                    self.backdrop_size,
                ),
                logo_url=self._image_url(
                    None if selected_logo is None else selected_logo.get("file_path"),
                    self.logo_size,
                ),
                payload={
                    "tmdb_id": tmdb_id,
                    "warnings": warnings,
                    "selected_languages": {
                        "poster": None
                        if selected_poster is None
                        else selected_poster.get("iso_639_1"),
                        "backdrop": None
                        if selected_backdrop is None
                        else selected_backdrop.get("iso_639_1"),
                        "logo": None if selected_logo is None else selected_logo.get("iso_639_1"),
                    },
                    "raw": response.value,
                },
            )
        )

    def _request_json(
        self,
        path: str,
        params: dict,
    ) -> MetadataProviderResponse[dict]:
        request_params = {"api_key": self.api_key, **params}
        try:
            if self.client is None:
                with httpx.Client(base_url=self.base_url, timeout=self.timeout_seconds) as client:
                    response = client.get(path, params=request_params)
            else:
                response = self.client.get(
                    urljoin(f"{self.base_url}/", path.lstrip("/")),
                    params=request_params,
                )
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            status_code = error.response.status_code
            self._log_call(
                path=path,
                params=params,
                status="failed",
                status_code=status_code,
                response_summary=_safe_json(error.response),
                error_message=f"tmdb request failed with status {status_code}",
            )
            return _provider_error(
                self.provider_name,
                code=f"http_{status_code}",
                message=f"tmdb request failed with status {status_code}",
                retryable=status_code >= 500 or status_code == 429,
                payload={
                    "path": path,
                    "params": params,
                    "status_code": status_code,
                    "response": _safe_json(error.response),
                },
            )
        except httpx.HTTPError as error:
            self._log_call(
                path=path,
                params=params,
                status="failed",
                status_code=None,
                response_summary=None,
                error_message=str(error) or error.__class__.__name__,
            )
            return _provider_error(
                self.provider_name,
                code="transport_error",
                message=str(error) or error.__class__.__name__,
                retryable=True,
                payload={"path": path, "params": params},
            )

        response_json = response.json()
        self._log_call(
            path=path,
            params=params,
            status="succeeded",
            status_code=response.status_code,
            response_summary=_response_summary(response_json),
            error_message=None,
        )
        return MetadataProviderResponse(value=response_json)

    def _person_from_tmdb(self, payload: dict, *, role: str | None) -> MetadataPerson:
        tmdb_id = payload.get("id")
        return MetadataPerson(
            provider=self.provider_name,
            provider_id=None if tmdb_id is None else f"person:{tmdb_id}",
            name=payload.get("name") or "Unknown",
            role=role,
            profile_url=None
            if tmdb_id is None
            else f"https://www.themoviedb.org/person/{tmdb_id}",
            image_url=self._image_url(payload.get("profile_path"), self.profile_size),
            payload=payload,
        )

    def _image_url(self, file_path: str | None, size: str) -> str | None:
        if not file_path:
            return None
        return f"{self.image_base_url.rstrip('/')}/{size}/{file_path.lstrip('/')}"

    def _log_call(
        self,
        *,
        path: str,
        params: dict,
        status: str,
        status_code: int | None,
        response_summary: dict | None,
        error_message: str | None,
    ) -> None:
        if self.call_logger is None:
            return
        self.call_logger(
            {
                "provider": self.provider_name,
                "path": path,
                "params": params,
                "status": status,
                "status_code": status_code,
                "response_summary": response_summary,
                "error_message": error_message,
            }
        )


def _provider_error(
    provider: str,
    *,
    code: str,
    message: str,
    retryable: bool,
    payload: dict,
) -> MetadataProviderResponse:
    return MetadataProviderResponse(
        error=MetadataProviderError(
            provider=provider,
            code=code,
            message=message,
            retryable=retryable,
            payload=payload,
        )
    )


def _safe_json(response: httpx.Response) -> dict | None:
    try:
        return response.json()
    except ValueError:
        return None


def _response_summary(payload: dict) -> dict:
    if "results" in payload:
        return {
            "result_count": len(payload.get("results", [])),
        }
    if "posters" in payload or "backdrops" in payload or "logos" in payload:
        return {
            "poster_count": len(payload.get("posters", [])),
            "backdrop_count": len(payload.get("backdrops", [])),
            "logo_count": len(payload.get("logos", [])),
        }
    if "cast" in payload or "crew" in payload:
        return {
            "cast_count": len(payload.get("cast", [])),
            "crew_count": len(payload.get("crew", [])),
        }
    if "imdb_id" in payload:
        return {
            "imdb_id": payload.get("imdb_id"),
        }
    return {
        "keys": sorted(payload.keys()),
    }


def _tmdb_movie_id(provider_id: str) -> int | None:
    """Parse TMDB movie provider_id.

    接受两种形式: ``"movie:68735"`` (带前缀) 或 ``"68735"`` (裸数字).
    其它形式 (字母 / 浮点 / 错误前缀 "show:123" / "tv:123" / 多余
    冒号 "movie:foo:bar") 仍返回 None, 走 invalid_provider_id 错误码.
    """
    parts = provider_id.split(":")
    if not parts:
        return None
    if len(parts) == 1:
        # 裸数字形式
        candidate = parts[0]
        if not candidate.isdigit():
            return None
        return int(candidate)
    if len(parts) == 2 and parts[0] == "movie":
        candidate = parts[1]
        if not candidate.isdigit():
            return None
        return int(candidate)
    # 错误前缀 / 多余冒号 一律 None
    return None


def _tmdb_show_id(provider_id: str) -> int | None:
    """Parse TMDB show provider_id. 接受 ``"show:123"`` 或 ``"123"``.

    错误前缀 (包括 ``"movie:123"``) / 多余冒号 / 字母 / 浮点 一律 None.
    """
    parts = provider_id.split(":")
    if not parts:
        return None
    if len(parts) == 1:
        candidate = parts[0]
        if not candidate.isdigit():
            return None
        return int(candidate)
    if len(parts) == 2 and parts[0] == "show":
        candidate = parts[1]
        if not candidate.isdigit():
            return None
        return int(candidate)
    return None


def _parse_keyword(keyword: str) -> tuple[str, int | None]:
    tokens = keyword.strip().split()
    if not tokens:
        return "", None
    if tokens[-1].isdigit() and len(tokens[-1]) == 4:
        return " ".join(tokens[:-1]).strip(), int(tokens[-1])
    return keyword.strip(), None


def _year_from_date(value: str | None) -> int | None:
    if not value or len(value) < 4:
        return None
    try:
        return int(value[:4])
    except ValueError:
        return None


def _normalized_title(value: str | None) -> str:
    if not value:
        return ""
    normalized = "".join(
        character.lower() if character.isalnum() else " " for character in value
    )
    return " ".join(normalized.split())


def _candidate_confidence(
    *,
    keyword_title: str,
    keyword_year: int | None,
    candidate_title: str,
    candidate_original_title: str | None,
    candidate_year: int | None,
    rank: int,
) -> float:
    normalized_keyword = _normalized_title(keyword_title)
    title_options = [
        _normalized_title(candidate_title),
        _normalized_title(candidate_original_title),
    ]
    best_ratio = max(
        SequenceMatcher(None, normalized_keyword, option).ratio() for option in title_options
    )
    confidence = 0.45 + (best_ratio * 0.4)
    if keyword_year is not None and candidate_year is not None:
        confidence += 0.1 if keyword_year == candidate_year else -0.08
    if rank == 0:
        confidence += 0.05
    elif rank == 1:
        confidence += 0.02
    return max(0.0, min(confidence, 0.99))


def _match_reason(
    *,
    keyword_title: str,
    keyword_year: int | None,
    candidate_title: str,
    candidate_original_title: str | None,
    candidate_year: int | None,
    rank: int,
) -> str:
    normalized_keyword = _normalized_title(keyword_title)
    title_options = [
        _normalized_title(candidate_title),
        _normalized_title(candidate_original_title),
    ]
    best_title = max(
        title_options,
        key=lambda item: SequenceMatcher(None, normalized_keyword, item).ratio(),
    )
    if best_title == normalized_keyword:
        title_reason = "title_exact"
    else:
        title_reason = "title_similar"
    if keyword_year is None or candidate_year is None:
        year_reason = "year_unknown"
    elif keyword_year == candidate_year:
        year_reason = "year_match"
    else:
        year_reason = "year_mismatch"
    return f"{title_reason},{year_reason},rank_{rank + 1}"


def _fallback_language(language_priority: list[str], primary_language: str) -> str | None:
    for language in language_priority:
        if language != primary_language:
            return language
    return None


def _needs_detail_fallback(payload: dict) -> bool:
    return not payload.get("title") or not payload.get("overview")


def _needs_show_fallback(payload: dict) -> bool:
    return not payload.get("name") or not payload.get("overview")


def _show_origin_countries(values: list | None) -> list[str]:
    """Normalize TMDB TV ``origin_country``.

    TMDB movie details use ``production_countries`` as dictionaries, but TV
    details expose ``origin_country`` as a list of ISO country strings such as
    ``["JP"]``.  Keep a small dict fallback for defensive parsing of cached or
    mocked payloads, but do not assume the movie shape here.
    """
    countries: list[str] = []
    for value in values or []:
        if isinstance(value, str) and value:
            countries.append(value)
        elif isinstance(value, dict):
            country = value.get("iso_3166_1")
            if isinstance(country, str) and country:
                countries.append(country)
    return countries


def _image_language_query(language_priority: list[str]) -> str:
    values = [language for language in language_priority if language]
    values.extend(["null", "en"])
    unique: list[str] = []
    for value in values:
        if value not in unique:
            unique.append(value)
    return ",".join(unique)


def _select_image(items: list[dict], language_priority: list[str]) -> dict | None:
    if not items:
        return None

    preference = [language for language in language_priority if language] + [None, "en"]
    for language in preference:
        for item in items:
            if item.get("iso_639_1") == language:
                return item
    return items[0]
