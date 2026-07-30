"""
citations/explorer.py — Citation Explorer orchestrator.

Ties together reference extraction, OpenAlex lookup, and LLM
purpose generation into a single ``explore_citations()`` entry point
for the dashboard.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from citations.cache import JsonFileCache
from citations.extractor import extract_references
from citations.models import (
    CitationPurpose,
    EnrichedCitation,
    PaperMetadata,
    RawReference,
)
from citations.purpose import generate_citation_purposes
from citations.openalex import OpenAlexClient

logger = logging.getLogger(__name__)


async def explore_citations(
    parsed_doc: dict,
    paper_context: str = "",
    cache: JsonFileCache | None = None,
) -> tuple[list[EnrichedCitation], dict]:
    """
    Main orchestrator: extract → resolve → explain → enrich.

    Parameters
    ----------
    parsed_doc : dict
        Structured JSON from the PDF parser.
    paper_context : str
        Optional text from the paper body mentioning references
        (improves LLM citation-purpose quality).
    cache : JsonFileCache | None
        Optional pre-configured cache; a default is created if omitted.

    Returns
    -------
    tuple[list[EnrichedCitation], dict]
        - List of enriched citations ready for the UI.
        - Timing/stats dict for metrics reporting.
    """
    t_start = time.perf_counter()
    stats: dict = {}

    # 1. Extract references ---------------------------------------------------
    raw_refs = extract_references(parsed_doc)
    stats["total_raw_references"] = len(raw_refs)
    stats["parsed_references"] = sum(1 for r in raw_refs if r.title)

    if not raw_refs:
        logger.warning("No references extracted — returning empty results.")
        return [], stats

    # 2. Resolve metadata via OpenAlex (concurrently) -----------------
    client = OpenAlexClient(cache=cache)
    metadata_map: dict[str, Optional[PaperMetadata]] = {}

    async def _resolve(ref: RawReference) -> tuple[str, Optional[PaperMetadata]]:
        """Look up a single reference by title."""
        if ref.title:
            meta = await client.lookup_by_title(ref.title)
            if meta:
                return ref.ref_id, meta

        # Fallback: try raw text (first 150 chars)
        if ref.raw_text:
            query = ref.raw_text[:150].strip()
            meta = await client.lookup_by_title(query)
            if meta:
                return ref.ref_id, meta

        return ref.ref_id, None

    resolve_tasks = [_resolve(ref) for ref in raw_refs]
    resolve_results = await asyncio.gather(*resolve_tasks, return_exceptions=True)

    for result in resolve_results:
        if isinstance(result, Exception):
            logger.warning("Resolution task failed: %s", result)
            continue
        ref_id, meta = result
        metadata_map[ref_id] = meta

    stats["api_calls"] = client.api_calls
    stats["api_successes"] = client.api_successes
    stats["api_errors"] = client.api_errors
    stats["cache_hits"] = client.cache_hits
    stats["cache_misses"] = client.cache_misses
    stats["latencies_ms"] = client.latencies_ms

    # 3. Generate citation purposes via LLM ----------------------------------
    purposes: list[CitationPurpose] = generate_citation_purposes(
        raw_refs, metadata_map, paper_context=paper_context
    )
    purpose_map: dict[str, CitationPurpose] = {p.ref_id: p for p in purposes}

    stats["purposes_generated"] = len(purposes)
    stats["purposes_non_empty"] = sum(
        1 for p in purposes if "unavailable" not in p.purpose.lower()
    )

    # 4. Build enriched citations --------------------------------------------
    enriched: list[EnrichedCitation] = []
    for ref in raw_refs:
        meta = metadata_map.get(ref.ref_id)
        purpose = purpose_map.get(ref.ref_id)
        enriched.append(
            EnrichedCitation(
                ref_id=ref.ref_id,
                raw_text=ref.raw_text,
                metadata=meta,
                purpose=purpose,
                resolved=meta is not None,
            )
        )

    stats["resolved_count"] = sum(1 for c in enriched if c.resolved)
    stats["end_to_end_latency_s"] = time.perf_counter() - t_start

    logger.info(
        "Citation exploration complete: %d/%d resolved in %.1fs",
        stats["resolved_count"],
        len(enriched),
        stats["end_to_end_latency_s"],
    )

    return enriched, stats
