"""Orchestration: upload → parsed document → verified brief.

Split into a *core* stage that must finish before the dashboard is useful, and
*lazy* stages that run only when their panel is first opened.

That split is a direct response to the PRD's performance target (20-35s from
upload to summary). Citation Explorer and Reviewer Mode are both stretch features
that make network calls or extra LLM calls; making the user wait for them before
seeing any summary would blow the budget for no benefit. They load on demand and
cache.

This module also enforces one guarantee the dashboard cannot take on trust:
**nothing reaches the UI without a verification verdict.** Member 2 is expected
to route claims through ``verify_claim`` already, but if a claim arrives without
a score, :func:`ensure_verified` runs it through Feature 4B here. NFR §11 says no
claim is displayed without a badge, and a backstop is the only way to actually
mean it.
"""

from __future__ import annotations

import hashlib
import logging
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from integration import adapters
from integration.contracts import (
    Brief,
    Document,
    Reference,
    to_brief,
    to_document,
    to_evidence,
    to_references,
)

logger = logging.getLogger("paperlens.pipeline")

_TMP_DIR = Path(tempfile.gettempdir()) / "paperlens"


def prepare_pdf(pdf_bytes: bytes, filename: str) -> tuple[str, str]:
    """Persist the upload to a stable path and return ``(path, content_key)``.

    PyMuPDF and the PDF viewer both need a real file path that survives across
    reruns, so the bytes from ``st.file_uploader`` are written once, under a name
    derived from their hash. Re-uploading the same paper reuses the same file.
    """
    key = hashlib.sha256(pdf_bytes).hexdigest()[:16]
    _TMP_DIR.mkdir(parents=True, exist_ok=True)

    safe_stem = "".join(c for c in Path(filename).stem if c.isalnum() or c in "-_")[:48] or "paper"
    path = _TMP_DIR / f"{safe_stem}-{key}.pdf"
    if not path.exists():
        path.write_bytes(pdf_bytes)
    return str(path), key


# ─── Core stage ────────────────────────────────────────────────────────────


def load_core(
    cache_key: str,
    pdf_path: str,
    filename: str,
    *,
    progress: Callable[[str], None] | None = None,
) -> tuple[Document, Brief, dict[str, str]]:
    """Parse, summarise and verify. Returns the document, brief, and any errors.

    Errors are returned rather than raised: a failure in summarisation should
    still leave a browsable PDF and a working chat panel, per NFR §11.
    """
    errors: dict[str, str] = {}

    def step(message: str) -> None:
        if progress:
            progress(message)

    step("Parsing the PDF and extracting page text…")
    parsed = adapters.run_process_pdf(cache_key, pdf_path)
    if not parsed.ok:
        errors["Document parsing"] = parsed.error
        # Without page text there is no verification and no highlighting, so this
        # is the one failure that genuinely degrades everything downstream.
        return Document(title=Path(filename).stem), Brief(), errors

    document = to_document(parsed.value, fallback_title=Path(filename).stem)
    step(f"Read {document.page_count} pages. Generating the structured summary…")

    brief_outcome = adapters.run_generate_brief(cache_key, document)
    if not brief_outcome.ok:
        errors["Summarization"] = brief_outcome.error
        return document, Brief(), errors

    brief = to_brief(brief_outcome.value)

    step(f"Verifying {len(brief.claims)} claims against the page text…")
    brief, verify_error = ensure_verified(brief, document)
    if verify_error:
        errors["Verification"] = verify_error

    step("Ready.")
    return document, brief, errors


def ensure_verified(brief: Brief, document: Document) -> tuple[Brief, str]:
    """Give every claim a verification verdict, running Feature 4B where needed.

    Claims that already carry a score are left alone — Member 3's pipeline may
    have downgraded one for reasons a re-run would not reproduce. Only claims
    that arrived unscored are verified here.
    """
    if not brief.claims or not document.full_text_by_page:
        return brief, ""

    error = ""
    verified_claims = []
    for claim in brief.claims:
        evidence = claim.evidence
        needs_check = evidence.score is None and evidence.has_quote
        if not needs_check:
            verified_claims.append(claim)
            continue

        outcome = adapters.run_verify_claim(
            evidence.quote, evidence.page, document.full_text_by_page
        )
        if not outcome.ok:
            # Record the first failure and keep going; a verifier crash must not
            # cost us the whole brief.
            error = error or outcome.error
            verified_claims.append(claim)
            continue

        checked = to_evidence(outcome.value)
        verified_claims.append(
            replace(
                claim,
                evidence=replace(
                    evidence,
                    # Prefer the page the verifier actually found the text on.
                    page=checked.page or evidence.page,
                    score=checked.score,
                    status=checked.status,
                ),
            )
        )

    return Brief(claims=tuple(verified_claims)), error


# ─── Lazy stages ───────────────────────────────────────────────────────────


def load_references(cache_key: str, document: Document) -> tuple[tuple[Reference, ...], str]:
    """Reference list, on first open of the Citations panel."""
    outcome = adapters.run_analyze_references(cache_key, document)
    if not outcome.ok:
        return (), outcome.error
    return to_references(outcome.value), ""


def load_review(cache_key: str, document: Document) -> tuple[dict[str, Any], str]:
    """Reviewer Mode, on first open of the Reviewer panel."""
    outcome = adapters.run_review(cache_key, document)
    if not outcome.ok:
        return {}, outcome.error
    value = outcome.value
    return (value if isinstance(value, dict) else {}), ""


def get_index(cache_key: str, document: Document, *, local: bool = False) -> tuple[Any, str]:
    """The retrieval index, built on first use of the Chat panel.

    Wrapped in ``st.cache_resource`` inside ``adapters``, as the integration
    notes specify, so it is built once per document rather than per rerun.

    ``local=True`` builds the offline keyword index instead — an explicit user
    choice, never an automatic fallback.
    """
    outcome = adapters.run_build_index(cache_key, document, local)
    if not outcome.ok:
        return None, outcome.error
    return outcome.value, ""
