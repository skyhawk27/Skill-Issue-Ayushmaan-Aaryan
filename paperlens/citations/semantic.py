"""
citations/semantic.py — Async Semantic Scholar API client using HTTPX.

Uses ``httpx.AsyncClient`` for all HTTP calls with connection pooling,
timeouts, and exponential-backoff retry on 429 (rate-limit) responses.
All calls are wrapped in try/except so a failure never crashes the app.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

import httpx

from citations.cache import JsonFileCache
from citations.config import (
    S2_API_BASE,
    S2_API_KEY,
    S2_CONCURRENCY_LIMIT,
    S2_MAX_RETRIES,
    S2_PAPER_FIELDS,
    S2_RETRY_BACKOFF_S,
    S2_TIMEOUT_S,
)
from citations.models import PaperMetadata

logger = logging.getLogger(__name__)


class SemanticScholarClient:
    """
    Async client for the Semantic Scholar Academic Graph API.

    Features
    --------
    - Cache-first lookups via ``JsonFileCache``
    - Exponential backoff on HTTP 429
    - Per-call latency tracking for evaluation metrics
    - Semaphore-controlled concurrency
    """

    def __init__(self, cache: JsonFileCache | None = None) -> None:
        self._cache = cache or JsonFileCache()
        self._semaphore = asyncio.Semaphore(S2_CONCURRENCY_LIMIT)

        # Metrics accumulators
        self.api_calls: int = 0
        self.api_successes: int = 0
        self.api_errors: int = 0
        self.cache_hits: int = 0
        self.cache_misses: int = 0
        self.latencies_ms: list[float] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def lookup_by_title(self, title: str) -> Optional[PaperMetadata]:
        """
        Look up a paper by its title using the ``/paper/search/match``
        endpoint (single best-match result).

        Returns ``None`` if the lookup fails or no match is found.
        """
        if not title or not title.strip():
            return None

        # Cache check
        cache_key = f"title:{title}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            self.cache_hits += 1
            return PaperMetadata.model_validate(cached)

        self.cache_misses += 1

        url = f"{S2_API_BASE}/paper/search/match"
        params = {"query": title, "fields": S2_PAPER_FIELDS}
        data = await self._get(url, params=params)

        if data is None:
            return None

        # /search/match wraps the result in a "data" array with one element
        paper_data = data.get("data", [data])
        if isinstance(paper_data, list) and paper_data:
            paper_data = paper_data[0]
        elif isinstance(paper_data, list):
            return None

        self._cache.set(cache_key, paper_data)
        return PaperMetadata.model_validate(paper_data)

    async def get_paper(self, paper_id: str) -> Optional[PaperMetadata]:
        """
        Fetch full paper metadata by Semantic Scholar paper ID.
        """
        if not paper_id:
            return None

        cache_key = f"paper:{paper_id}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            self.cache_hits += 1
            return PaperMetadata.model_validate(cached)

        self.cache_misses += 1

        url = f"{S2_API_BASE}/paper/{paper_id}"
        params = {"fields": S2_PAPER_FIELDS}
        data = await self._get(url, params=params)

        if data is None:
            return None

        self._cache.set(cache_key, data)
        return PaperMetadata.model_validate(data)

    async def get_references(
        self, paper_id: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """
        Fetch the reference list for a paper (used by Family Tree).

        Returns a list of reference dicts, each containing at least
        ``citedPaper`` with ``paperId`` and ``title``.
        """
        url = f"{S2_API_BASE}/paper/{paper_id}/references"
        params = {
            "fields": "title,year,citationCount,authors",
            "limit": str(limit),
        }
        data = await self._get(url, params=params)
        if data is None:
            return []
        return data.get("data", [])

    async def get_citations(
        self, paper_id: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """
        Fetch papers that cite a given paper (used by Family Tree).
        """
        url = f"{S2_API_BASE}/paper/{paper_id}/citations"
        params = {
            "fields": "title,year,citationCount,authors",
            "limit": str(limit),
        }
        data = await self._get(url, params=params)
        if data is None:
            return []
        return data.get("data", [])

    # ------------------------------------------------------------------
    # Internal HTTP helper with retry + backoff
    # ------------------------------------------------------------------

    async def _get(
        self, url: str, params: dict[str, str] | None = None
    ) -> Optional[dict]:
        """
        Execute a GET request with retry, backoff, and metrics tracking.
        """
        headers: dict[str, str] = {}
        if S2_API_KEY:
            headers["x-api-key"] = S2_API_KEY

        async with self._semaphore:
            for attempt in range(S2_MAX_RETRIES):
                self.api_calls += 1
                t0 = time.perf_counter()
                try:
                    async with httpx.AsyncClient(
                        timeout=S2_TIMEOUT_S, headers=headers
                    ) as client:
                        resp = await client.get(url, params=params)

                    elapsed_ms = (time.perf_counter() - t0) * 1000
                    self.latencies_ms.append(elapsed_ms)

                    if resp.status_code == 200:
                        self.api_successes += 1
                        return resp.json()

                    if resp.status_code == 404:
                        # Paper not found — not an error, just no match
                        self.api_successes += 1
                        return None

                    if resp.status_code == 429:
                        # Rate limited — backoff and retry
                        wait = S2_RETRY_BACKOFF_S * (2**attempt)
                        logger.warning(
                            "S2 rate limited (429). Waiting %.1fs (attempt %d/%d).",
                            wait,
                            attempt + 1,
                            S2_MAX_RETRIES,
                        )
                        await asyncio.sleep(wait)
                        continue

                    # Other error codes
                    logger.warning(
                        "S2 returned %d for %s", resp.status_code, url
                    )
                    self.api_errors += 1
                    return None

                except (httpx.HTTPError, Exception) as exc:
                    elapsed_ms = (time.perf_counter() - t0) * 1000
                    self.latencies_ms.append(elapsed_ms)
                    self.api_errors += 1
                    logger.warning(
                        "S2 request failed (%s): %s", type(exc).__name__, exc
                    )
                    if attempt < S2_MAX_RETRIES - 1:
                        await asyncio.sleep(S2_RETRY_BACKOFF_S * (2**attempt))
                    continue

        return None
