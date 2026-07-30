"""
citations/purpose.py — LLM-powered citation purpose generation.

Uses the OpenAI Python SDK (>=1.0.0) to explain *why* each reference
was cited in the uploaded paper.  Batches references to minimize API
calls and falls back gracefully when the API is unavailable.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from citations.config import (
    OPENAI_MAX_TOKENS,
    OPENAI_MODEL,
    OPENAI_TEMPERATURE,
    PURPOSE_BATCH_SIZE,
)
from citations.models import CitationPurpose, PaperMetadata, RawReference

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy OpenAI client — avoids import-time crash if key is not set
# ---------------------------------------------------------------------------

_client: Optional[object] = None


def _get_openai_client():
    """Return a singleton ``OpenAI`` client, creating it on first use."""
    global _client
    if _client is None:
        try:
            from openai import OpenAI

            _client = OpenAI()  # reads OPENAI_API_KEY from env
        except Exception as exc:
            logger.warning("Could not initialise OpenAI client: %s", exc)
            return None
    return _client


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_citation_purposes(
    references: list[RawReference],
    metadata_map: dict[str, Optional[PaperMetadata]],
    paper_context: str = "",
) -> list[CitationPurpose]:
    """
    Generate LLM explanations for why each reference was cited.

    Parameters
    ----------
    references : list[RawReference]
        The extracted references from the PDF.
    metadata_map : dict[str, Optional[PaperMetadata]]
        Mapping from ``ref_id`` to enriched metadata (may be ``None``
        for unresolved references).
    paper_context : str
        Optional: text from the uploaded paper that mentions these
        references (improves LLM quality).

    Returns
    -------
    list[CitationPurpose]
        One purpose per reference.  Unresolvable references receive
        a placeholder "Purpose unavailable." message.
    """
    client = _get_openai_client()
    if client is None:
        logger.warning("OpenAI unavailable — returning placeholder purposes.")
        return [
            CitationPurpose(ref_id=r.ref_id, purpose="Purpose unavailable (LLM not configured).", relationship="unknown")
            for r in references
        ]

    results: list[CitationPurpose] = []

    # Process in batches
    for i in range(0, len(references), PURPOSE_BATCH_SIZE):
        batch = references[i : i + PURPOSE_BATCH_SIZE]
        batch_purposes = _generate_batch(client, batch, metadata_map, paper_context)
        results.extend(batch_purposes)

    return results


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _generate_batch(
    client: object,
    batch: list[RawReference],
    metadata_map: dict[str, Optional[PaperMetadata]],
    paper_context: str,
) -> list[CitationPurpose]:
    """Call OpenAI for a batch of references and parse the response."""
    ref_descriptions: list[str] = []
    for ref in batch:
        meta = metadata_map.get(ref.ref_id)
        desc = f"[{ref.ref_id}] "
        if meta and meta.title:
            desc += f'"{meta.title}"'
            if meta.year:
                desc += f" ({meta.year})"
            if meta.abstract:
                desc += f"\n  Abstract: {meta.abstract[:300]}..."
        else:
            desc += ref.raw_text[:300]
        ref_descriptions.append(desc)

    refs_block = "\n\n".join(ref_descriptions)

    system_prompt = (
        "You are a research paper analyst. For each cited reference, explain "
        "in 1-2 concise sentences WHY the authors cited it and classify the "
        "relationship type.\n\n"
        "Relationship types: foundational work, direct comparison, dataset source, "
        "methodology extension, theoretical framework, evaluation baseline, "
        "related work, tool/library, survey/review, other.\n\n"
        "Return ONLY valid JSON — an array of objects with keys: "
        '"ref_id" (string), "purpose" (string), "relationship" (string).'
    )

    user_prompt = f"Paper context:\n{paper_context[:2000]}\n\nReferences to explain:\n{refs_block}"

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            temperature=OPENAI_TEMPERATURE,
            max_tokens=OPENAI_MAX_TOKENS,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )

        content = response.choices[0].message.content or ""
        return _parse_purposes(content, batch)

    except Exception as exc:
        logger.warning("OpenAI call failed: %s", exc)
        return [
            CitationPurpose(ref_id=r.ref_id, purpose="Purpose unavailable (API error).", relationship="unknown")
            for r in batch
        ]


def _parse_purposes(
    llm_output: str, batch: list[RawReference]
) -> list[CitationPurpose]:
    """
    Parse the LLM JSON response into ``CitationPurpose`` objects.

    Falls back to placeholder values if parsing fails.
    """
    # Strip markdown code fences if present
    cleaned = llm_output.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        # Remove first and last fence lines
        lines = [l for l in lines if not l.strip().startswith("```")]
        cleaned = "\n".join(lines)

    try:
        items = json.loads(cleaned)
        if not isinstance(items, list):
            items = [items]
    except json.JSONDecodeError:
        logger.warning("Failed to parse LLM output as JSON.")
        return [
            CitationPurpose(ref_id=r.ref_id, purpose="Purpose unavailable (parse error).", relationship="unknown")
            for r in batch
        ]

    # Build lookup by ref_id
    purpose_map: dict[str, dict] = {}
    for item in items:
        if isinstance(item, dict):
            rid = str(item.get("ref_id", ""))
            purpose_map[rid] = item

    results: list[CitationPurpose] = []
    for ref in batch:
        item = purpose_map.get(ref.ref_id, {})
        results.append(
            CitationPurpose(
                ref_id=ref.ref_id,
                purpose=item.get("purpose", "Purpose unavailable."),
                relationship=item.get("relationship", "unknown"),
            )
        )
    return results
