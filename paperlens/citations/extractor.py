"""
citations/extractor.py — Extract references from a parsed PDF document.

Takes the structured JSON produced by Member 1's ``pdf_parser.py`` and
extracts individual reference entries from the "References" section.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from citations.models import RawReference

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex patterns for splitting reference blocks
# ---------------------------------------------------------------------------

# Matches "[1]", "[2]", … at the start of a line or after whitespace
_NUMBERED_BRACKET_RE = re.compile(r"(?:^|\n)\s*\[(\d+)\]\s*")

# Matches "1.", "2.", … at the start of a line
_NUMBERED_DOT_RE = re.compile(r"(?:^|\n)\s*(\d+)\.\s+")

# Year pattern: (2023) or , 2023 or 2023.
_YEAR_RE = re.compile(r"(?:\(|\s|,)(\d{4})(?:\)|\.|\s|,|$)")

# Heuristic: title is usually the first sentence-like segment after authors.
# Authors often end with a year in parens or a period before the title.
_TITLE_RE = re.compile(
    r'["\u201c](.+?)["\u201d]'  # Quoted title
    r"|"
    r"(?:(?:19|20)\d{2}[.)]\s*)([^.]{15,200}\.)",  # After year, up to next period
    re.DOTALL,
)

# Author block: everything before the first year or quoted title
_AUTHORS_RE = re.compile(
    r"^(.+?)(?:\(\d{4}\)|(?:19|20)\d{2}[.,\s])",
    re.DOTALL,
)


def extract_references(parsed_doc: dict) -> list[RawReference]:
    """
    Extract references from a parsed PDF document.

    Parameters
    ----------
    parsed_doc : dict
        The structured JSON from the PDF parser.  Expected shapes:

        **Shape A** — list of section dicts::

            {
                "sections": [
                    {"section": "References", "page": 20, "text": "..."},
                    ...
                ]
            }

        **Shape B** — flat text keyed by section name::

            {
                "references_text": "...",
            }

        **Shape C** — pre-extracted list::

            {
                "references": [
                    {"ref_id": "1", "raw_text": "...", "title": "..."},
                    ...
                ]
            }

    Returns
    -------
    list[RawReference]
        Parsed reference objects.  Fields ``title``, ``authors_raw``, and
        ``year`` are best-effort heuristic extractions.
    """
    # --- Shape C: already extracted -----------------------------------------
    if "references" in parsed_doc and isinstance(parsed_doc["references"], list):
        return _from_pre_extracted(parsed_doc["references"])

    # --- Locate raw reference text ------------------------------------------
    ref_text = _find_reference_text(parsed_doc)
    if not ref_text:
        logger.warning("No references section found in parsed document.")
        return []

    # --- Split into individual entries --------------------------------------
    entries = _split_entries(ref_text)
    if not entries:
        logger.warning("Could not split reference text into entries.")
        return []

    # --- Parse each entry ---------------------------------------------------
    results: list[RawReference] = []
    for ref_id, raw in entries:
        title = _extract_title(raw)
        authors = _extract_authors(raw)
        year = _extract_year(raw)
        results.append(
            RawReference(
                ref_id=ref_id,
                raw_text=raw.strip(),
                title=title,
                authors_raw=authors,
                year=year,
            )
        )

    logger.info("Extracted %d references from PDF.", len(results))
    return results


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _from_pre_extracted(refs: list[dict]) -> list[RawReference]:
    """Convert a list of dicts (Shape C) into ``RawReference`` objects."""
    results = []
    for i, r in enumerate(refs):
        raw_text = r.get("raw_text", r.get("text", ""))
        results.append(
            RawReference(
                ref_id=r.get("ref_id", str(i + 1)),
                raw_text=raw_text,
                title=r.get("title"),
                authors_raw=r.get("authors_raw", r.get("authors")),
                year=r.get("year"),
            )
        )
    return results


def _find_reference_text(parsed_doc: dict) -> Optional[str]:
    """Locate the reference section text from the parsed document."""
    # Shape B
    if "references_text" in parsed_doc:
        return parsed_doc["references_text"]

    # Shape A — search sections
    sections = parsed_doc.get("sections", [])
    for sec in sections:
        name = (sec.get("section") or sec.get("heading") or "").strip().lower()
        if name in ("references", "bibliography", "works cited"):
            return sec.get("text", "")

    # Fallback: concatenate any section whose heading contains "reference"
    ref_texts: list[str] = []
    for sec in sections:
        name = (sec.get("section") or sec.get("heading") or "").strip().lower()
        if "reference" in name or "bibliography" in name:
            ref_texts.append(sec.get("text", ""))
    return "\n".join(ref_texts) if ref_texts else None


def _split_entries(text: str) -> list[tuple[str, str]]:
    """
    Split reference block text into ``(ref_id, entry_text)`` pairs.

    Tries bracket-numbered ``[1]`` first, then dot-numbered ``1.``,
    then falls back to double-newline splitting.
    """
    # Try bracket-numbered
    parts = _NUMBERED_BRACKET_RE.split(text)
    if len(parts) >= 3:
        # parts = [preamble, id1, text1, id2, text2, ...]
        return [(parts[i], parts[i + 1]) for i in range(1, len(parts) - 1, 2)]

    # Try dot-numbered
    parts = _NUMBERED_DOT_RE.split(text)
    if len(parts) >= 3:
        return [(parts[i], parts[i + 1]) for i in range(1, len(parts) - 1, 2)]

    # Fallback: split on double newlines
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    return [(str(i + 1), b) for i, b in enumerate(blocks)]


def _extract_year(text: str) -> Optional[int]:
    """Extract the first plausible publication year from reference text."""
    match = _YEAR_RE.search(text)
    if match:
        try:
            year = int(match.group(1))
            if 1900 <= year <= 2100:
                return year
        except (ValueError, TypeError):
            pass
    return None


def _extract_title(text: str) -> Optional[str]:
    """Best-effort title extraction via quoted text or heuristic patterns."""
    match = _TITLE_RE.search(text)
    if match:
        title = match.group(1) or match.group(2)
        if title:
            return title.strip().rstrip(".")
    return None


def _extract_authors(text: str) -> Optional[str]:
    """Best-effort author extraction: everything before the first year."""
    match = _AUTHORS_RE.match(text)
    if match:
        authors = match.group(1).strip().rstrip(",").rstrip(".")
        if len(authors) > 3:
            return authors
    return None
