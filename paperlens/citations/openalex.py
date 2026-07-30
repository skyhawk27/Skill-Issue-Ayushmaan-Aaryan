"""
citations/openalex.py — Async OpenAlex API client using HTTPX.

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
    OPENALEX_API_BASE,
    OPENALEX_API_KEY,
    OPENALEX_CONCURRENCY_LIMIT,
    OPENALEX_MAX_RETRIES,
    OPENALEX_RETRY_BACKOFF_S,
    OPENALEX_TIMEOUT_S,
)
from citations.models import PaperMetadata, Author, OpenAccessPdf

logger = logging.getLogger(__name__)


class OpenAlexClient:
    """
    Async client for the OpenAlex Academic Graph API.

    Features
    --------
    - Cache-first lookups via ``JsonFileCache``
    - Exponential backoff on HTTP 429
    - Per-call latency tracking for evaluation metrics
    - Semaphore-controlled concurrency
    """

    def __init__(self, cache: JsonFileCache | None = None) -> None:
        self._cache = cache or JsonFileCache()
        self._semaphore = asyncio.Semaphore(OPENALEX_CONCURRENCY_LIMIT)

        # Metrics accumulators
        self.api_calls: int = 0
        self.api_successes: int = 0
        self.api_errors: int = 0
        self.cache_hits: int = 0
        self.cache_misses: int = 0
        self.latencies_ms: list[float] = []

    def _parse_paper(self, data: dict[str, Any]) -> PaperMetadata:
        """Parse raw OpenAlex work JSON into PaperMetadata."""
        authors = []
        for authorship in data.get("authorships", []):
            author_data = authorship.get("author", {})
            authors.append(
                Author(
                    authorId=author_data.get("id"),
                    name=author_data.get("display_name", "")
                )
            )
            
        oa_data = data.get("open_access", {})
        oa_pdf = OpenAccessPdf(
            url=oa_data.get("oa_url"),
            status=oa_data.get("oa_status")
        ) if oa_data.get("is_oa") else None

        primary_location = data.get("primary_location", {}) or {}
        source = primary_location.get("source", {}) or {}
        venue = source.get("display_name")

        abstract = None
        abstract_inverted_index = data.get("abstract_inverted_index")
        if abstract_inverted_index:
            # Reconstruct abstract from inverted index
            words = []
            for word, positions in abstract_inverted_index.items():
                for pos in positions:
                    words.append((pos, word))
            words.sort()
            abstract = " ".join([word for _, word in words])

        topics = []
        for topic in data.get("topics", []):
            topics.append(topic.get("display_name", ""))

        return PaperMetadata(
            paperId=data.get("id"),
            title=data.get("title", ""),
            authors=authors,
            year=data.get("publication_year"),
            abstract=abstract,
            citationCount=data.get("cited_by_count"),
            url=data.get("id"),
            venue=venue,
            fieldsOfStudy=topics,
            openAccessPdf=oa_pdf,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def lookup_by_title(self, title: str) -> Optional[PaperMetadata]:
        """Look up a paper by its title."""
        if not title or not title.strip():
            return None

        cache_key = f"title:{title}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            self.cache_hits += 1
            return self._parse_paper(cached)

        self.cache_misses += 1

        url = f"{OPENALEX_API_BASE}/works"
        params = {"filter": f"title.search:{title}"}
        data = await self._get(url, params=params)

        if data is None:
            return None

        results = data.get("results", [])
        if not results:
            return None

        paper_data = results[0]
        self._cache.set(cache_key, paper_data)
        return self._parse_paper(paper_data)

    async def get_paper(self, paper_id: str) -> Optional[PaperMetadata]:
        """Fetch full paper metadata by OpenAlex paper ID."""
        if not paper_id:
            return None

        # Handle raw IDs vs full URLs from OpenAlex
        clean_id = paper_id.split("/")[-1]

        cache_key = f"paper:{clean_id}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            self.cache_hits += 1
            return self._parse_paper(cached)

        self.cache_misses += 1

        url = f"{OPENALEX_API_BASE}/works/{clean_id}"
        data = await self._get(url)

        if data is None:
            return None

        self._cache.set(cache_key, data)
        return self._parse_paper(data)

    async def get_references(
        self, paper_id: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Fetch the reference list for a paper."""
        clean_id = paper_id.split("/")[-1]
        
        url = f"{OPENALEX_API_BASE}/works"
        params = {
            "filter": f"cited_by:{clean_id}",
            "per-page": str(limit),
            "select": "id,title,publication_year,cited_by_count,authorships"
        }
        data = await self._get(url, params=params)
        if data is None:
            return []
            
        results = []
        for w in data.get("results", []):
            authors = [{"name": a.get("author", {}).get("display_name", "")} for a in w.get("authorships", [])]
            results.append({
                "paperId": w.get("id"),
                "title": w.get("title", ""),
                "year": w.get("publication_year"),
                "citationCount": w.get("cited_by_count"),
                "authors": authors
            })
        return results

    async def get_citations(
        self, paper_id: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Fetch papers that cite a given paper."""
        clean_id = paper_id.split("/")[-1]
        
        url = f"{OPENALEX_API_BASE}/works"
        params = {
            "filter": f"cites:{clean_id}",
            "per-page": str(limit),
            "select": "id,title,publication_year,cited_by_count,authorships"
        }
        data = await self._get(url, params=params)
        if data is None:
            return []
            
        results = []
        for w in data.get("results", []):
            authors = [{"name": a.get("author", {}).get("display_name", "")} for a in w.get("authorships", [])]
            results.append({
                "paperId": w.get("id"),
                "title": w.get("title", ""),
                "year": w.get("publication_year"),
                "citationCount": w.get("cited_by_count"),
                "authors": authors
            })
        return results

    # ------------------------------------------------------------------
    # Internal HTTP helper with retry + backoff
    # ------------------------------------------------------------------

    async def _get(
        self, url: str, params: dict[str, str] | None = None
    ) -> Optional[dict]:
        """Execute a GET request with retry, backoff, and metrics tracking."""
        headers: dict[str, str] = {}
        
        req_params = dict(params) if params else {}
        if OPENALEX_API_KEY:
            req_params["api_key"] = OPENALEX_API_KEY

        async with self._semaphore:
            for attempt in range(OPENALEX_MAX_RETRIES):
                self.api_calls += 1
                t0 = time.perf_counter()
                try:
                    async with httpx.AsyncClient(
                        timeout=OPENALEX_TIMEOUT_S, headers=headers
                    ) as client:
                        resp = await client.get(url, params=req_params)

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
                        wait = OPENALEX_RETRY_BACKOFF_S * (2**attempt)
                        logger.warning(
                            "OpenAlex rate limited (429). Waiting %.1fs (attempt %d/%d).",
                            wait,
                            attempt + 1,
                            OPENALEX_MAX_RETRIES,
                        )
                        await asyncio.sleep(wait)
                        continue

                    # Other error codes
                    logger.warning(
                        "OpenAlex returned %d for %s", resp.status_code, url
                    )
                    self.api_errors += 1
                    return None

                except (httpx.HTTPError, Exception) as exc:
                    elapsed_ms = (time.perf_counter() - t0) * 1000
                    self.latencies_ms.append(elapsed_ms)
                    self.api_errors += 1
                    logger.warning(
                        "OpenAlex request failed (%s): %s", type(exc).__name__, exc
                    )
                    if attempt < OPENALEX_MAX_RETRIES - 1:
                        await asyncio.sleep(OPENALEX_RETRY_BACKOFF_S * (2**attempt))
                    continue

        return None
