"""Summary panel — PRD Feature 3, and the front half of the demo spine.

Sections appear in the PRD's canonical order (Main contribution, Methodology,
Results, Limitations, Prerequisites). Every claim carries a badge, and every
claim with a page reference is one click from its highlighted evidence.
"""

from __future__ import annotations

import streamlit as st

from integration.contracts import Brief, Document
from ui import components, overview, state


def render(doc: Document, brief: Brief) -> None:
    error = state.error_for("Summarization")
    if error:
        components.unavailable(
            "Summary",
            "The summarization module failed, so there are no claims to show. "
            "The PDF viewer and chat still work.",
        )
        with st.expander("Error detail", icon=":material/bug_report:"):
            st.code(error, language="text")
        return

    # Overview first, then navigation, then the checked claims — the order a
    # reader actually works in. Each part draws only if it has content, so a
    # brief without them degrades to exactly the panel this used to be.
    overview.render_abstract(brief.abstract)
    if brief.page_summaries or doc.page_count:
        overview.render_page_index(doc, brief.page_summaries)

    if not brief.claims:
        if not brief.abstract.is_empty or brief.page_summaries:
            # There is still an overview above; a full-panel empty state here
            # would wrongly imply the panel had nothing at all.
            return
        components.empty_state(
            "No claims yet",
            "The summarizer returned nothing for this paper.",
            icon=":material/summarize:",
        )
        return

    grouped = brief.by_section()

    unsupported = brief.tally().get("unsupported", 0)
    if unsupported:
        # Lead with the bad news. The product's value is that it tells you when
        # its own output is not backed by the paper, so this cannot be buried
        # below the fold.
        st.warning(
            f"{unsupported} claim{'s' if unsupported > 1 else ''} could not be found "
            "in the paper. Treat those with caution.",
            icon=":material/report:",
        )

    for section, claims in grouped.items():
        st.subheader(section, anchor=False)
        components.claim_list(claims, key_prefix="summary")

    st.space("small")
    _export_row(doc, brief)


def _export_row(doc: Document, brief: Brief) -> None:
    """Feature 10 — one-click verified summary export."""
    from ui import export

    with st.container(horizontal=True, horizontal_alignment="left"):
        st.download_button(
            "Export verified summary",
            data=export.summary_markdown(doc, brief),
            file_name=f"{(doc.title or 'paperlens')[:60].strip().replace(' ', '-')}-verified-summary.md",
            mime="text/markdown",
            icon=":material/download:",
            help="Download the summary with every claim's verification badge and page reference.",
        )
