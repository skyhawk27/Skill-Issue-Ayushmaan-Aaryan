"""
Evidence Verification Pipeline — Feature 4B (core differentiator).

Every LLM-generated claim is checked against the actual extracted PDF text
before being shown to the user.  This module is the shared ``verify_claim()``
function used by Summarisation, Chat, and Reviewer modules.

Pipeline
--------
1. Take a candidate_quote and claimed_page from the LLM.
2. Gather actual text for that page ± 1 adjacent page.
3. Fuzzy-match the quote against that text.
4. Assign a badge:  ✅ Verified  /  ⚠️ Paraphrased  /  ❌ Unsupported.
5. On ❌, optionally re-attempt against ALL pages (fallback search).
6. Return a VerifiedClaim with match_score and character offsets for
   PDF highlighting.
"""

from __future__ import annotations

import sys
import os

# Ensure project root is importable when running as a module.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from typing import Optional

from verification.fuzzy_match import fuzzy_match_quote, MatchResult
from utils.models import VerifiedClaim


def verify_claim(
    candidate_quote: str,
    claimed_page: Optional[int],
    full_text_by_page: dict[int, str],
    *,
    fallback_search: bool = True,
) -> VerifiedClaim:
    """
    Verify a single LLM-generated quote against the actual PDF text.

    Parameters
    ----------
    candidate_quote:
        The quote string the LLM says exists in the paper.
    claimed_page:
        The 1-based page number the LLM claims the quote is from.
        May be ``None`` if the LLM couldn't determine the page.
    full_text_by_page:
        Mapping from 1-based page numbers to the full extracted text of each
        page.  Provided by Member 1's ParsedDocument.
    fallback_search:
        If ``True`` and the initial match is "unsupported", search ALL pages
        once more to see if the quote exists on a different page than claimed.

    Returns
    -------
    VerifiedClaim with match_score, status, and character offsets.
    """

    # ------------------------------------------------------------------
    # Edge case: nothing to verify
    # ------------------------------------------------------------------
    if not candidate_quote or not candidate_quote.strip():
        return VerifiedClaim(
            quote=candidate_quote or "",
            page=claimed_page,
            match_score=0.0,
            status="unsupported",
        )

    # ------------------------------------------------------------------
    # Step 1: build target text from claimed_page ± 1
    # ------------------------------------------------------------------
    primary_result = _match_against_pages(
        candidate_quote, claimed_page, full_text_by_page
    )

    if primary_result.status != "unsupported":
        # We found a good match on the expected page (±1).
        return _to_verified_claim(
            candidate_quote,
            claimed_page,
            primary_result,
        )

    # ------------------------------------------------------------------
    # Step 2: fallback — search ALL pages
    # ------------------------------------------------------------------
    if fallback_search:
        best_result: Optional[MatchResult] = None
        best_page: Optional[int] = None

        for page_num, page_text in full_text_by_page.items():
            # Skip pages already checked in the primary attempt.
            if claimed_page is not None and abs(page_num - claimed_page) <= 1:
                continue

            result = fuzzy_match_quote(candidate_quote, page_text)
            if best_result is None or result.score > best_result.score:
                best_result = result
                best_page = page_num

        if best_result is not None and best_result.status != "unsupported":
            return _to_verified_claim(
                candidate_quote,
                best_page,
                best_result,
            )

    # ------------------------------------------------------------------
    # Nothing found anywhere — genuinely unsupported.
    # ------------------------------------------------------------------
    return VerifiedClaim(
        quote=candidate_quote,
        page=claimed_page,
        match_score=primary_result.score,
        status="unsupported",
    )


def verify_claims_batch(
    claims: list[dict],
    full_text_by_page: dict[int, str],
) -> list[VerifiedClaim]:
    """
    Batch-verify a list of claims.

    Each dict in *claims* must have keys ``"quote"`` and ``"page"``.
    """
    return [
        verify_claim(
            candidate_quote=c.get("quote", ""),
            claimed_page=c.get("page"),
            full_text_by_page=full_text_by_page,
        )
        for c in claims
    ]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _match_against_pages(
    candidate_quote: str,
    claimed_page: Optional[int],
    full_text_by_page: dict[int, str],
) -> MatchResult:
    """
    Run fuzzy_match_quote against claimed_page ± 1.

    Returns the single best MatchResult across those pages.
    """
    pages_to_check: list[int] = []

    if claimed_page is not None:
        for offset in (0, -1, 1):
            p = claimed_page + offset
            if p in full_text_by_page:
                pages_to_check.append(p)
    else:
        # No page claim — check all pages (small cost for short papers).
        pages_to_check = list(full_text_by_page.keys())

    best: Optional[MatchResult] = None

    for page_num in pages_to_check:
        page_text = full_text_by_page[page_num]
        result = fuzzy_match_quote(candidate_quote, page_text)
        if best is None or result.score > best.score:
            best = result

    if best is None:
        return MatchResult(
            score=0.0,
            matched_text="",
            char_start=-1,
            char_end=-1,
            status="unsupported",
        )

    return best


def _to_verified_claim(
    original_quote: str,
    page: Optional[int],
    match: MatchResult,
) -> VerifiedClaim:
    """Convert a MatchResult into a VerifiedClaim dataclass."""
    return VerifiedClaim(
        quote=original_quote,
        page=page,
        match_score=match.score,
        status=match.status,
        matched_text=match.matched_text,
        char_start=match.char_start,
        char_end=match.char_end,
    )
