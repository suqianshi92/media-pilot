"""资源发现 API 合同测试。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from media_pilot.app import create_app
from media_pilot.config import AppConfig
from media_pilot.resource_discovery.types import ResourceCandidate, ResourceSearchResult


def _make_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        downloads_dir=tmp_path / "downloads",
        watch_dir=tmp_path / "watch",
        workspace_dir=tmp_path / "workspace",
        movies_dir=tmp_path / "movies",
        shows_dir=tmp_path / "shows",
        database_dir=tmp_path / "db",
        prowlarr_url="http://prowlarr.example.test",
        prowlarr_api_key="prowlarr-key",
        llm_api_key=None,
        llm_base_url=None,
        llm_model=None,
    )


def test_resource_search_defaults_to_direct_keyword_search(tmp_path: Path, monkeypatch) -> None:
    adapter = MagicMock()
    adapter.search.return_value = ResourceSearchResult(
        candidates=[
            ResourceCandidate(
                title="Hell or High Water 2016 1080p",
                indexer="TestIndexer",
                source="prowlarr",
                download_url="https://example.test/torrent",
                seeders=10,
            )
        ],
        source="prowlarr",
        query_used="modern western",
        search_type="all",
        message="找到 1 个候选",
    )
    monkeypatch.setattr(
        "media_pilot.services.resource_discovery.ProwlarrAdapter",
        lambda _config: adapter,
    )

    client = TestClient(create_app(config=_make_config(tmp_path)))
    response = client.post(
        "/api/v1/resource-discovery/search",
        json={"input_text": "modern western"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["data"]["query_used"] == "modern western"
    assert body["data"]["intent"]["resource_search_keywords"] == ["modern western"]
    req = adapter.search.call_args.args[0]
    assert req.query == "modern western"
    assert req.search_type == "all"
