"""TPDB (ThePornDB) adult movie metadata provider adapter — 官方 OpenAPI /jav"""

from __future__ import annotations

import logging
import re
from urllib.parse import quote

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

logger = logging.getLogger(__name__)

TPDB_IMAGE_BASE = "https://img.theporndb.net"
TPDB_CONTENT_TYPE_JAV = "jav"
TPDB_SEARCH_PER_PAGE = 10


class TpdbAdultProvider:
    """TPDB 成人影片 metadata provider — 使用官方 OpenAPI /jav 端点"""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.theporndb.net",
        timeout_seconds: float = 10.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._client = httpx.Client(
            base_url=self._base_url,
            timeout=self._timeout,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Accept": "application/json",
            },
        )

    @classmethod
    def from_config(cls, config: AppConfig) -> TpdbAdultProvider:
        if not config.tpdb_api_key:
            raise ValueError("tpdb_api_key is required for TPDB provider")
        return cls(
            api_key=config.tpdb_api_key,
            base_url=config.tpdb_base_url,
            timeout_seconds=10.0,
        )

    # ------------------------------------------------------------------
    # 搜索
    # ------------------------------------------------------------------

    def search(
        self, query: str, *, language_priority: list[str] | None = None
    ) -> MetadataProviderResponse[list[MetadataCandidate]]:
        """按番号搜索 — GET /jav?q= 主路径，空结果降级 parse="""
        try:
            resp = self._client.get(
                "/jav",
                params={"q": query, "per_page": TPDB_SEARCH_PER_PAGE},
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            return MetadataProviderResponse(
                value=None,
                error=_tpdb_error(exc),
            )

        items = data.get("data") or []

        # q= 返回空则降级到 parse=
        if not items:
            try:
                resp = self._client.get(
                    "/jav",
                    params={
                        "parse": query,
                        "hash": "",
                        "year": "",
                        "per_page": TPDB_SEARCH_PER_PAGE,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                items = data.get("data") or []
            except Exception as exc:
                return MetadataProviderResponse(
                    value=None,
                    error=_tpdb_error(exc),
                )

        candidates = _jav_items_to_candidates(items, query)
        return MetadataProviderResponse(value=candidates, error=None)

    # ------------------------------------------------------------------
    # 详情
    # ------------------------------------------------------------------

    def get_details(
        self, provider_id: str, *, language_priority: list[str] | None = None
    ) -> MetadataProviderResponse[MetadataDetail]:
        """获取 TPDB 详情 — provider_id 格式为 jav/<uuid>"""
        parsed = _parse_provider_id(provider_id)
        if parsed is None:
            return _unsupported_provider_id_error(provider_id)

        content_type, uuid_val = parsed
        if content_type != TPDB_CONTENT_TYPE_JAV:
            return _unsupported_provider_id_error(provider_id)

        try:
            encoded_id = quote(str(uuid_val), safe="")
            resp = self._client.get(f"/jav/{encoded_id}")
            resp.raise_for_status()
            data = resp.json().get("data") or {}
        except Exception as exc:
            return MetadataProviderResponse(
                value=None,
                error=_tpdb_error(exc),
            )

        return MetadataProviderResponse(
            value=_jav_data_to_detail(data),
            error=None,
        )

    # ------------------------------------------------------------------
    # MetadataProvider 协议兼容方法
    # ------------------------------------------------------------------

    def search_movie(
        self, keyword: str, *, language_priority: list[str] | None = None
    ) -> MetadataProviderResponse[list[MetadataCandidate]]:
        return self.search(keyword, language_priority=language_priority)

    def get_movie_details(
        self, provider_id: str, *, language_priority: list[str] | None = None
    ) -> MetadataProviderResponse[MetadataDetail]:
        detail_resp = self.get_details(
            provider_id, language_priority=language_priority
        )
        if detail_resp.error is not None:
            return detail_resp
        return detail_resp

    def get_movie_credits(
        self, provider_id: str
    ) -> MetadataProviderResponse[MetadataCredits]:
        parsed = _parse_provider_id(provider_id)
        if parsed is None or parsed[0] != TPDB_CONTENT_TYPE_JAV:
            return MetadataProviderResponse(
                value=MetadataCredits(),
                error=None,
            )
        _, uuid_val = parsed
        try:
            encoded_id = quote(str(uuid_val), safe="")
            resp = self._client.get(f"/jav/{encoded_id}")
            resp.raise_for_status()
            data = resp.json().get("data") or {}
        except Exception as exc:
            return MetadataProviderResponse(
                value=None,
                error=_tpdb_error(exc),
            )

        performers = data.get("performers") or []
        actors: list[MetadataPerson] = []
        for p in performers:
            name = p.get("name") if isinstance(p, dict) else str(p)
            actors.append(MetadataPerson(
                provider="tpdb",
                provider_id=None,
                name=name,
                role=None,
                profile_url=None,
                image_url=_performer_image(p),
            ))

        directors_data = data.get("directors") or []
        directors: list[MetadataPerson] = []
        for d in directors_data:
            name = d.get("name") if isinstance(d, dict) else str(d)
            directors.append(MetadataPerson(
                provider="tpdb",
                provider_id=None,
                name=name,
                role="director",
                profile_url=None,
                image_url=None,
            ))

        return MetadataProviderResponse(
            value=MetadataCredits(actors=actors, directors=directors),
            error=None,
        )

    def get_movie_external_ids(
        self, provider_id: str
    ) -> MetadataProviderResponse[MetadataExternalIds]:
        parsed = _parse_provider_id(provider_id)
        if parsed is None or parsed[0] != TPDB_CONTENT_TYPE_JAV:
            return MetadataProviderResponse(
                value=MetadataExternalIds(imdb_id=None),
                error=None,
            )
        _, uuid_val = parsed
        try:
            encoded_id = quote(str(uuid_val), safe="")
            resp = self._client.get(f"/jav/{encoded_id}")
            resp.raise_for_status()
            data = resp.json().get("data") or {}
        except Exception as exc:
            return MetadataProviderResponse(
                value=None,
                error=_tpdb_error(exc),
            )

        external_id = data.get("external_id") or None
        return MetadataProviderResponse(
            value=MetadataExternalIds(
                imdb_id=None,
                payload={
                    "tpdb_id": str(uuid_val),
                    "tpdb_type": "jav",
                    "external_id": external_id,
                },
            ),
            error=None,
        )

    def get_movie_images(
        self, provider_id: str, *, language_priority: list[str] | None = None
    ) -> MetadataProviderResponse[MetadataImages]:
        parsed = _parse_provider_id(provider_id)
        if parsed is None or parsed[0] != TPDB_CONTENT_TYPE_JAV:
            return MetadataProviderResponse(
                value=MetadataImages(poster_url=None, backdrop_url=None, logo_url=None),
                error=None,
            )
        _, uuid_val = parsed
        try:
            encoded_id = quote(str(uuid_val), safe="")
            resp = self._client.get(f"/jav/{encoded_id}")
            resp.raise_for_status()
            data = resp.json().get("data") or {}
        except Exception as exc:
            return MetadataProviderResponse(
                value=None,
                error=_tpdb_error(exc),
            )

        site = data.get("site") or {}
        return MetadataProviderResponse(
            value=MetadataImages(
                poster_url=_extract_image_field(data, "poster"),
                backdrop_url=(
                    _extract_image_field(data, "background")
                    or _extract_image_field(data, "background_back")
                    or _extract_image_field(data, "back_image")
                ),
                logo_url=_safe_image_url(
                    site.get("logo") if isinstance(site, dict) else None
                ),
            ),
            error=None,
        )

    # ------------------------------------------------------------------
    # 连通性探测
    # ------------------------------------------------------------------

    def ping(self) -> tuple[bool, str, int | None]:
        """连通性探测 — GET /auth/user"""
        import time
        start = time.monotonic()
        try:
            resp = self._client.get("/auth/user")
            resp.raise_for_status()
            elapsed_ms = int((time.monotonic() - start) * 1000)
            return True, "TPDB API 连接正常", elapsed_ms
        except httpx.HTTPStatusError as exc:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            if exc.response.status_code in (401, 403):
                return False, "TPDB token 无效或权限不足", elapsed_ms
            return False, f"TPDB 连接失败: HTTP {exc.response.status_code}", elapsed_ms
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            return False, f"TPDB 连接失败: {type(exc).__name__}", elapsed_ms


# ======================================================================
# provider_id 解析
# ======================================================================

def _parse_provider_id(provider_id: str) -> tuple[str, str] | None:
    """解析 provider_id，返回 (content_type, uuid) 或 None"""
    if not provider_id:
        return None
    if "/" in provider_id:
        parts = provider_id.split("/", 1)
        return parts[0], parts[1]
    # 裸 UUID — 兼容旧数据，默认按 jav 处理
    return TPDB_CONTENT_TYPE_JAV, provider_id


def _unsupported_provider_id_error(
    provider_id: str,
) -> MetadataProviderResponse:
    return MetadataProviderResponse(
        value=None,
        error=MetadataProviderError(
            provider="tpdb",
            code="unsupported_type",
            message=f"不支持的 TPDB provider_id: {provider_id}，本轮仅支持 jav/<uuid>",
            retryable=False,
        ),
    )


# ======================================================================
# 搜索响应 → 候选列表
# ======================================================================

def _jav_items_to_candidates(
    items: list[dict], query: str
) -> list[MetadataCandidate]:
    norm_query = _normalize_code(query)
    candidates: list[MetadataCandidate] = []
    for item in items:
        item_id = item.get("id") or ""
        external_id = item.get("external_id") or ""
        title = item.get("title") or ""
        date_val = item.get("date") or None
        year = _extract_year(date_val)

        confidence, reason = _jav_confidence(norm_query, external_id, title)

        site = item.get("site") or {}
        candidates.append(MetadataCandidate(
            provider="tpdb",
            provider_id=f"jav/{item_id}" if item_id else "",
            title=title,
            original_title=external_id or None,
            year=year,
            media_type="movie",
            overview=item.get("description"),
            poster_url=_candidate_poster_url(item),
            confidence=confidence,
            match_reason=reason or "",
            payload={
                "type": item.get("type"),
                "external_id": external_id,
                "sku": item.get("sku"),
                "date": date_val,
                "site_name": site.get("name") if isinstance(site, dict) else None,
                "url": item.get("url"),
            },
        ))
    return candidates


def _candidate_poster_url(item: dict) -> str | None:
    """海报优先级: top-level poster > poster_image > posters > images.poster"""
    poster = _extract_image_field(item, "poster")
    if poster:
        return poster

    pi = _extract_image_field(item, "poster_image")
    if pi:
        return pi

    # posters 可能在顶层或 images 内
    posters = item.get("posters") or (item.get("images") or {}).get("posters") or {}
    if isinstance(posters, dict):
        for key in ("large", "full", "medium"):
            url = posters.get(key)
            if isinstance(url, dict):
                url = url.get("url")
            if isinstance(url, str) and url:
                return url if url.startswith("http") else (
                    f"{TPDB_IMAGE_BASE}/{url.lstrip('/')}"
                )

    # images.poster (嵌套格式兜底)
    images = item.get("images") or {}
    ip = images.get("poster")
    if isinstance(ip, dict):
        ip = ip.get("large") or ip.get("full") or ip.get("url")
    if isinstance(ip, str) and ip:
        return ip if ip.startswith("http") else (
            f"{TPDB_IMAGE_BASE}/{ip.lstrip('/')}"
        )

    return None


def _extract_image_field(item: dict, key: str) -> str | None:
    """从顶层或 images 内提取图片字段"""
    val = item.get(key)
    # 顶层 dict（含 large/full/url）
    if isinstance(val, dict):
        val = val.get("large") or val.get("full") or val.get("url")
    # 顶层字符串
    if isinstance(val, str) and val:
        return val if val.startswith("http") else (
            f"{TPDB_IMAGE_BASE}/{val.lstrip('/')}"
        )
    # 在 images 内查找
    images = item.get("images") or {}
    val2 = images.get(key)
    if isinstance(val2, dict):
        val2 = val2.get("large") or val2.get("full") or val2.get("url")
    if isinstance(val2, str) and val2:
        return val2 if val2.startswith("http") else (
            f"{TPDB_IMAGE_BASE}/{val2.lstrip('/')}"
        )
    return None


# ======================================================================
# 详情响应 → MetadataDetail
# ======================================================================

def _jav_data_to_detail(data: dict) -> MetadataDetail:
    item_id = data.get("id") or ""
    external_id = data.get("external_id") or ""
    title = data.get("title") or ""
    date_val = data.get("date") or None
    year = _extract_year(date_val)

    # studios ← site.name
    site = data.get("site") or {}
    studios = []
    if isinstance(site, dict) and site.get("name"):
        studios.append(site["name"])

    # genres ← tags[].name
    tags = data.get("tags") or []
    genres = [
        t.get("name") for t in tags
        if isinstance(t, dict) and t.get("name")
    ]

    return MetadataDetail(
        provider="tpdb",
        provider_id=f"jav/{item_id}",
        title=title,
        original_title=external_id or None,
        year=year,
        media_type="movie",
        plot=data.get("description"),
        premiered=date_val,
        runtime_minutes=None,
        rating=data.get("rating"),
        genres=genres,
        countries=[],
        studios=studios,
        images=MetadataImages(
            poster_url=_extract_image_field(data, "poster"),
            backdrop_url=(
                _extract_image_field(data, "background")
                or _extract_image_field(data, "background_back")
                or _extract_image_field(data, "back_image")
            ),
            logo_url=_safe_image_url(
                site.get("logo") if isinstance(site, dict) else None
            ),
        ),
        external_ids=MetadataExternalIds(
            imdb_id=None,
            payload={
                "tpdb_id": str(item_id),
                "tpdb_type": "jav",
                "external_id": external_id,
            },
        ),
        payload=data,
    )


# ======================================================================
# JAV 置信度模型
# ======================================================================

def _normalize_code(code: str) -> str:
    """番号归一化：大写、去空白、统一横杠字符"""
    if not code:
        return ""
    n = code.upper().strip()
    n = re.sub(r"\s+", "", n)
    n = re.sub(r"[–—−‐]", "-", n)
    return n


def _strip_dashes(code: str) -> str:
    return code.replace("-", "")


def _code_prefix(code: str) -> str | None:
    m = re.match(r"^([A-Z]+)", code)
    return m.group(1) if m else None


def _code_number(code: str) -> str | None:
    m = re.search(r"(\d+)$", code)
    return m.group(1) if m else None


def _jav_confidence(
    norm_query: str, external_id: str, title: str
) -> tuple[float, str | None]:
    """返回 (confidence, match_reason)"""
    norm_ext = _normalize_code(external_id)
    norm_title = _normalize_code(title)

    # 1. external_id 完全匹配 → 0.95
    if norm_ext and norm_ext == norm_query:
        return 0.95, "番号精确匹配"

    # 2. title 包含完全匹配 → 0.90
    if norm_title and norm_query in norm_title:
        return 0.90, "标题包含番号"

    # 3. 去横杠后匹配 → 0.88
    q_nodash = _strip_dashes(norm_query)
    ext_nodash = _strip_dashes(norm_ext)
    title_nodash = _strip_dashes(norm_title)

    if ext_nodash and ext_nodash == q_nodash:
        return 0.88, "番号匹配（忽略横杠）"
    if title_nodash and q_nodash in title_nodash:
        return 0.85, "标题包含番号（忽略横杠）"

    # 4. 前缀相同但编号不同 → 0.45
    q_prefix = _code_prefix(norm_query)
    e_prefix = _code_prefix(norm_ext) if norm_ext else _code_prefix(norm_title)
    q_num = _code_number(norm_query)
    e_num = _code_number(norm_ext) if norm_ext else _code_number(norm_title)

    if (
        q_prefix and e_prefix and q_prefix == e_prefix
        and q_num and e_num and q_num != e_num
    ):
        return 0.45, "番号前缀相同但编号不同"

    # 5. 普通 fuzzy 匹配 → 0.30
    if norm_ext and (norm_query in norm_ext or norm_ext in norm_query):
        return 0.30, "部分匹配"
    if norm_title and (norm_query in norm_title or norm_title in norm_query):
        return 0.25, "标题部分匹配"

    return 0.20, None


# ======================================================================
# 错误处理
# ======================================================================

def _tpdb_error(exc: Exception) -> MetadataProviderError:
    payload: dict = {}
    retryable = False
    code = "request_error"

    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        payload["status_code"] = status_code
        code = "http_error"
        if status_code in (401, 403):
            retryable = False
        elif status_code >= 500:
            retryable = True
    elif isinstance(exc, httpx.RequestError):
        retryable = True

    return MetadataProviderError(
        provider="tpdb",
        code=code,
        message=str(exc),
        retryable=retryable,
        payload=payload,
    )


# ======================================================================
# 辅助函数
# ======================================================================

def _extract_year(date_val) -> int | None:
    if not date_val:
        return None
    if isinstance(date_val, str):
        try:
            return int(date_val[:4])
        except ValueError:
            return None
    if isinstance(date_val, int):
        return date_val if 1900 <= date_val <= 2100 else None
    return None


def _safe_image_url(url) -> str | None:
    if isinstance(url, str) and url:
        return url if url.startswith("http") else (
            f"{TPDB_IMAGE_BASE}/{url.lstrip('/')}"
        )
    return None


def _performer_image(performer) -> str | None:
    """演员图片优先级: face > image > thumbnail > thumb"""
    if not isinstance(performer, dict):
        return None
    images = performer.get("images") or {}
    for key in ("face", "image", "thumbnail", "thumb"):
        img = images.get(key)
        if isinstance(img, dict):
            img = img.get("url") or img.get("full") or img.get("large")
        if isinstance(img, str) and img:
            return img if img.startswith("http") else (
                f"{TPDB_IMAGE_BASE}/{img.lstrip('/')}"
            )
    return None
