"""``render_dashboard`` — Member 5's public contract function.

Layout
------
Three columns: a narrow nav rail, the content pane, and a persistent PDF pane.

The persistence is the whole point. Clicking a claim is *one* click and the proof
appears beside the claim rather than replacing it, so a reader never loses their
place and a demo audience sees the claim and its evidence in one frame. Tabs or a
modal would both break that: the claim would be gone at the moment you are asked
to trust it.

The left pane is a fixed-height scroll container so the two panes stay
side-by-side rather than the page growing to the height of whichever is longer.
The PDF pane manages its own scrolling inside the component iframe.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from integration import adapters
from integration.contracts import Brief, Document, Reference, to_brief, to_document, to_references
from ui import components, pdf_pane, state
from ui.panels import chat as chat_panel
from ui.panels import citations as citations_panel
from ui.panels import reviewer as reviewer_panel
from ui.panels import summary as summary_panel
from ui.theme import PANE_HEIGHT, SPLIT_RATIO


def render_dashboard(document: Any, brief: Any = None, citations: Any = None) -> None:
    """Render the whole PaperLens dashboard.

    This is the integration-contract entry point, so it accepts whatever the
    other members hand over — dataclasses, dicts, or raw module output — and
    normalises through ``integration.contracts`` before anything is drawn.
    Callers do not need to pre-convert.

    Args:
        document: Parsed paper from ``process_pdf``.
        brief: Structured summary from ``generate_brief``.
        citations: Reference analysis from ``analyze_references``.
    """
    state.init()

    doc = document if isinstance(document, Document) else to_document(document)
    the_brief = brief if isinstance(brief, Brief) else to_brief(brief)
    refs: tuple[Reference, ...]
    if isinstance(citations, tuple) and all(isinstance(c, Reference) for c in citations):
        refs = citations
    else:
        refs = to_references(citations)

    _header(doc)

    rail, content, viewer = st.columns(SPLIT_RATIO, gap="medium", vertical_alignment="top")

    with rail:
        _nav_rail(the_brief)

    with content:
        with st.container(height=PANE_HEIGHT, border=False):
            _content_pane(doc, the_brief, refs)

    with viewer:
        pdf_pane.render(doc.page_count or 1)


def _header(doc: Document) -> None:
    """Product name, paper title, and the export action."""
    with st.container(horizontal=True, vertical_alignment="center"):
        st.markdown("### PaperLens")
        st.badge(
            "Evidence-grounded",
            icon=":material/verified:",
            color="primary",
            help="Every claim below is checked against the actual PDF text before you see it.",
        )
        st.space("stretch")
        st.caption(state.get(state.PDF_NAME) or doc.title)

    components.fallback_banner(_fallback_notice())


def _fallback_notice() -> str:
    """Disclose which parts of the output are produced locally."""
    from integration import stubs

    return stubs.fallback_notice(adapters.missing_capabilities())


def _nav_rail(brief: Brief) -> None:
    """Panel switcher plus the verification tally.

    A vertical radio, not tabs: tabs across the top would compete with the
    header, and the rail has room for the tally underneath, which keeps the
    paper's overall trustworthiness on screen no matter which panel is open.
    """
    st.radio(
        "Section",
        options=state.PANELS,
        key=state.PANEL,
        label_visibility="collapsed",
        format_func=lambda name: f"{state.PANEL_ICONS.get(name, '')} {name}",
    )

    st.space("small")
    components.tally_rail(brief.tally())


def _content_pane(doc: Document, brief: Brief, refs: tuple[Reference, ...]) -> None:
    """Dispatch to the selected panel."""
    panel = state.get(state.PANEL) or "Summary"

    if panel == "Summary":
        summary_panel.render(doc, brief)
    elif panel == "Chat":
        chat_panel.render(doc)
    elif panel == "Citations":
        citations_panel.render(doc, refs)
    elif panel == "Reviewer":
        reviewer_panel.render(doc)
    else:  # pragma: no cover - defensive
        components.empty_state("Nothing to show", f"Unknown panel {panel!r}.")
