"""
tests/test_openalex.py — Unit tests for OpenAlex API client.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from citations.openalex import OpenAlexClient
from citations.cache import JsonFileCache


@pytest.fixture
def mock_cache():
    return JsonFileCache(cache_dir="dummy")


@pytest.fixture
def client(mock_cache):
    # Disable cache persistence for tests
    mock_cache._write = lambda k, v: None
    return OpenAlexClient(cache=mock_cache)


class TestOpenAlexClient:
    @pytest.mark.asyncio
    async def test_lookup_by_title_empty(self, client: OpenAlexClient):
        result = await client.lookup_by_title("")
        assert result is None

    @pytest.mark.asyncio
    @patch("httpx.AsyncClient.get")
    async def test_lookup_by_title_success(self, mock_get, client: OpenAlexClient):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "results": [
                {
                    "id": "https://openalex.org/W123",
                    "title": "Test Paper",
                    "publication_year": 2023,
                }
            ]
        }
        mock_get.return_value = mock_response

        result = await client.lookup_by_title("Test Paper")
        assert result is not None
        assert result.paper_id == "https://openalex.org/W123"
        assert result.title == "Test Paper"
        assert result.year == 2023
        assert client.api_successes == 1

    @pytest.mark.asyncio
    @patch("httpx.AsyncClient.get")
    async def test_lookup_by_title_not_found(self, mock_get, client: OpenAlexClient):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"results": []}
        mock_get.return_value = mock_response

        result = await client.lookup_by_title("Does not exist")
        assert result is None
        assert client.api_successes == 1

    @pytest.mark.asyncio
    @patch("httpx.AsyncClient.get")
    async def test_get_paper_success(self, mock_get, client: OpenAlexClient):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "WXYZ",
            "title": "Specific Paper",
        }
        mock_get.return_value = mock_response

        result = await client.get_paper("WXYZ")
        assert result is not None
        assert result.paper_id == "WXYZ"
        assert result.title == "Specific Paper"

    @pytest.mark.asyncio
    async def test_cache_hit_avoids_api_call(self, client: OpenAlexClient):
        # Seed the cache
        client._cache.set("title:cached title", {"id": "cached123", "title": "Cached Title"})
        
        # This shouldn't call the API
        result = await client.lookup_by_title("cached title")
        assert result is not None
        assert result.paper_id == "cached123"
        assert client.api_calls == 0
        assert client.cache_hits == 1

    @pytest.mark.asyncio
    @patch("httpx.AsyncClient.get")
    async def test_get_references_success(self, mock_get, client: OpenAlexClient):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "results": [
                {"id": "Wref1", "title": "Ref 1"}
            ]
        }
        mock_get.return_value = mock_response

        refs = await client.get_references("paper1")
        assert len(refs) == 1
        assert refs[0]["paperId"] == "Wref1"
