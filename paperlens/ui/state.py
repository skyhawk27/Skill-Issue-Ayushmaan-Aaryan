"""Session state: every key in one place, with the navigation primitive.

Two conventions worth knowing before you add anything:

* Every key is namespaced ``pl.*``. Widget keys created elsewhere in the app sit
  in the same flat ``st.session_state`` dict, and the prefix keeps our own state
  from ever colliding with one.

* The panels never touch the PDF viewer directly. A claim click calls
  :func:`show_evidence`, which records *what* to show; the PDF pane reads that
  and works out *how*. That indirection is what lets any panel — summary, chat,
  reviewer — drive the viewer without knowing the others exist.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

# ─── Keys ──────────────────────────────────────────────────────────────────

DOC_KEY = "pl.doc_key"            # content hash of the uploaded PDF
PDF_PATH = "pl.pdf_path"          # temp file on disk, for PyMuPDF and the viewer
PDF_NAME = "pl.pdf_name"          # original filename, for display

DOCUMENT = "pl.document"          # contracts.Document
BRIEF = "pl.brief"                # contracts.Brief
REFERENCES = "pl.references"      # tuple[contracts.Reference, ...]
REVIEW = "pl.review"              # dict from Reviewer Mode
ERRORS = "pl.errors"              # {feature label: message} for scoped fallbacks

PANEL = "pl.panel"                # which content panel is showing
CHAT = "pl.chat"                  # list[contracts.ChatTurn]

#: Whether the reader has pressed Enter on the opening splash. The splash is a
#: gate, not a timed flash, so it re-renders every run until this flips.
#: Session-scoped, and deliberately not reset by :func:`reset_document` — loading
#: a second paper is not a new session, and re-gating there would be an
#: irritation rather than a flourish.
SPLASH_DISMISSED = "pl.splash_dismissed"

TARGET_PAGE = "pl.target_page"    # page the viewer should scroll to
TARGET_QUOTE = "pl.target_quote"  # quote to highlight there
TARGET_STATUS = "pl.target_status"  # its verification status, which picks the colour
ACTIVE_CLAIM = "pl.active_claim"  # claim_id currently selected, for the selected state

#: Page whose summary the visual index is showing. Belongs to the current paper,
#: so unlike SPLASH_DISMISSED it *is* cleared by :func:`reset_document`.
PAGE_SELECTED = "pl.page_selected"

PANELS = ("Summary", "Chat", "Citations", "Reviewer")

#: Material Symbols icon per panel, used in the nav rail.
PANEL_ICONS = {
    "Summary": ":material/summarize:",
    "Chat": ":material/forum:",
    "Citations": ":material/account_tree:",
    "Reviewer": ":material/rate_review:",
}

_DEFAULTS: dict[str, Any] = {
    DOC_KEY: None,
    PDF_PATH: None,
    PDF_NAME: "",
    DOCUMENT: None,
    BRIEF: None,
    REFERENCES: (),
    REVIEW: None,
    ERRORS: {},
    PANEL: "Summary",
    CHAT: [],
    SPLASH_DISMISSED: False,
    TARGET_PAGE: 1,
    TARGET_QUOTE: "",
    TARGET_STATUS: None,
    ACTIVE_CLAIM: None,
    PAGE_SELECTED: None,
}


def init() -> None:
    """Seed every key once. Safe to call on every rerun."""
    for key, default in _DEFAULTS.items():
        if key not in st.session_state:
            # Copy mutables so sessions never share a list or dict.
            st.session_state[key] = default.copy() if isinstance(default, (list, dict)) else default


def get(key: str, default: Any = None) -> Any:
    return st.session_state.get(key, default)


def has_document() -> bool:
    return st.session_state.get(DOCUMENT) is not None


def reset_document() -> None:
    """Clear everything derived from the current paper.

    Called when a different file is uploaded. Chat history and the selected claim
    belong to the old paper and would be actively misleading against a new one.
    """
    for key in (DOCUMENT, BRIEF, REFERENCES, REVIEW, ACTIVE_CLAIM, TARGET_QUOTE,
                TARGET_STATUS, PAGE_SELECTED):
        st.session_state[key] = _DEFAULTS[key]
    st.session_state[CHAT] = []
    st.session_state[ERRORS] = {}
    st.session_state[TARGET_PAGE] = 1


# ─── Navigation ────────────────────────────────────────────────────────────


def show_evidence(page: int | None, quote: str = "", status: str | None = None,
                  claim_id: str | None = None) -> None:
    """Point the PDF pane at a piece of evidence.

    This is the whole "click a claim → jump to the highlighted span" interaction,
    and it is one function call from anywhere in the UI. The pane resolves the
    quote to geometry itself, so callers need only say what they want shown.
    """
    if page is not None and page >= 1:
        st.session_state[TARGET_PAGE] = int(page)
    st.session_state[TARGET_QUOTE] = quote or ""
    st.session_state[TARGET_STATUS] = status
    st.session_state[ACTIVE_CLAIM] = claim_id


def goto_page(page: int) -> None:
    """Move the viewer without changing the highlight (the page-nav arrows)."""
    st.session_state[TARGET_PAGE] = max(1, int(page))


def record_error(label: str, message: str) -> None:
    """Note that one feature degraded, so its panel can say so in place."""
    errors = dict(st.session_state.get(ERRORS) or {})
    errors[label] = message
    st.session_state[ERRORS] = errors


def error_for(label: str) -> str:
    return (st.session_state.get(ERRORS) or {}).get(label, "")
