"""
Fuzzy string matching for the evidence verification pipeline (Feature 4B).

Uses rapidfuzz to compare a candidate LLM-generated quote against the actual
extracted page text from the PDF.  The match score determines a verification
badge:

    >= 90  →  ✅ Verified   (quote essentially found verbatim)
    60–90  →  ⚠️ Paraphrased (idea present, wording differs)
    <  60  →  ❌ Unsupported  (quote not found in page text)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from rapidfuzz import fuzz, process


# ---------------------------------------------------------------------------
# Thresholds — importable so verifier.py and tests can reference them.
# ---------------------------------------------------------------------------
VERIFIED_THRESHOLD: float = 90.0
PARAPHRASED_THRESHOLD: float = 60.0


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class MatchResult:
    """Return type of fuzzy_match_quote()."""
    score: float              # 0–100, from rapidfuzz
    matched_text: str         # the best-matching substring found in target
    char_start: int           # start offset in *target_text*
    char_end: int             # end offset in *target_text*
    status: str               # "verified" | "paraphrased" | "unsupported"


# ---------------------------------------------------------------------------
# Core matching function
# ---------------------------------------------------------------------------

def fuzzy_match_quote(
    candidate_quote: str,
    target_text: str,
    *,
    verified_threshold: float = VERIFIED_THRESHOLD,
    paraphrased_threshold: float = PARAPHRASED_THRESHOLD,
) -> MatchResult:
    """
    Compare *candidate_quote* against *target_text* using fuzzy matching.

    Strategy
    --------
    1.  Run ``fuzz.partial_ratio`` for an overall similarity score (fast).
    2.  Slide a window of roughly the candidate's length across the target
        to locate the best-matching substring and its character offsets.
    3.  Classify into verified / paraphrased / unsupported by thresholds.

    Parameters
    ----------
    candidate_quote:
        The quote the LLM claims exists in the paper.
    target_text:
        The actual extracted text to search within (usually one page ± 1).
    verified_threshold:
        Score at or above which the quote is considered verbatim-verified.
    paraphrased_threshold:
        Score at or above which the quote is a recognisable paraphrase.

    Returns
    -------
    MatchResult with score, matched substring, offsets, and status.
    """
    if not candidate_quote or not target_text:
        return MatchResult(
            score=0.0,
            matched_text="",
            char_start=-1,
            char_end=-1,
            status="unsupported",
        )

    # Normalise whitespace for fairer comparison.
    candidate_clean = _normalise(candidate_quote)
    target_clean = _normalise(target_text)

    # Step 1: overall score.
    overall_score: float = fuzz.partial_ratio(
        candidate_clean, target_clean
    )

    # Step 2: locate the best-matching window in the *original* target text
    # so we can report char offsets useful for PDF highlighting.
    matched_text, char_start, char_end = _locate_best_window(
        candidate_clean, target_text
    )

    # Step 3: classify.
    if overall_score >= verified_threshold:
        status = "verified"
    elif overall_score >= paraphrased_threshold:
        status = "paraphrased"
    else:
        status = "unsupported"

    return MatchResult(
        score=overall_score,
        matched_text=matched_text,
        char_start=char_start,
        char_end=char_end,
        status=status,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalise(text: str) -> str:
    """Collapse runs of whitespace and strip, for fairer fuzzy comparison."""
    return " ".join(text.split())


def _locate_best_window(
    candidate: str,
    target: str,
    *,
    window_pad: int = 30,
) -> tuple[str, int, int]:
    """
    Slide a window across *target* and return the best-matching substring
    together with its (start, end) character offsets in the original
    *target* string.

    The window length is ``len(candidate) ± window_pad`` to allow for minor
    length differences between the candidate and the actual text.

    Falls back to (candidate, -1, -1) if nothing reasonable is found.
    """
    candidate_norm = _normalise(candidate)
    target_norm = _normalise(target)

    cand_len = len(candidate_norm)
    if cand_len == 0 or len(target_norm) == 0:
        return ("", -1, -1)

    best_score: float = -1.0
    best_start: int = 0
    best_end: int = 0
    best_text: str = ""

    min_window = max(1, cand_len - window_pad)
    max_window = cand_len + window_pad

    # We work on the normalised target for scoring, but we need to map
    # back to original offsets.  Build a map from normalised-index to
    # original-index.
    norm_to_orig = _build_norm_to_orig_map(target)

    for win_len in range(min_window, min(max_window + 1, len(target_norm) + 1)):
        for start in range(0, len(target_norm) - win_len + 1, max(1, win_len // 4)):
            window = target_norm[start : start + win_len]
            score = fuzz.ratio(candidate_norm, window)
            if score > best_score:
                best_score = score
                best_start = start
                best_end = start + win_len
                best_text = window

    # Map back to original offsets.
    if best_score < 0:
        return ("", -1, -1)

    orig_start = norm_to_orig[best_start] if best_start < len(norm_to_orig) else 0
    orig_end = (
        norm_to_orig[min(best_end, len(norm_to_orig) - 1)] + 1
        if best_end <= len(norm_to_orig)
        else len(target)
    )
    original_text = target[orig_start:orig_end]

    return (original_text, orig_start, orig_end)


def _build_norm_to_orig_map(text: str) -> list[int]:
    """
    Build a list where ``map[normalised_index] = original_index``.

    Normalisation collapses runs of whitespace to a single space and strips
    leading/trailing whitespace, matching ``_normalise()``.
    """
    mapping: list[int] = []
    in_space = False
    stripped = text.lstrip()
    offset = len(text) - len(text.lstrip())  # leading whitespace offset

    for i, ch in enumerate(text):
        if i < offset:
            continue  # skip leading whitespace
        if ch in (" ", "\t", "\n", "\r"):
            if not in_space:
                mapping.append(i)
                in_space = True
            # else: skip subsequent whitespace chars
        else:
            mapping.append(i)
            in_space = False

    # Trim trailing if original text ended with whitespace and _normalise
    # would strip it.
    while mapping and text[mapping[-1]] in (" ", "\t", "\n", "\r"):
        mapping.pop()

    return mapping
