"""Citations panel — PRD Feature 6, plus the Feature 7 family tree.

The PRD's reliability note gets taken literally here: OpenAlex lookups
can fail or rate-limit, so a missing citation count or abstract is a normal state
this panel renders explicitly rather than an error. Nothing is invented to fill a
gap — an unknown citation count shows as unknown, because a fabricated number in
a tool whose entire premise is verifiability would be self-defeating.

The graph uses ``st.graphviz_chart``, which is native and needs no extra
dependency. The theme's chart colours are an ink ramp, so the graph reads as
structure rather than as a second palette competing with the verification badges.
"""

from __future__ import annotations

import streamlit as st

from integration import pipeline
from integration.contracts import Document, Reference
from ui import components, state


def render(doc: Document, preloaded: tuple[Reference, ...] = ()) -> None:
    references, error = _load(doc, preloaded)

    if error:
        components.unavailable(
            "Citation data",
            "Reference metadata could not be retrieved. The rest of the dashboard is unaffected.",
        )
        with st.expander("Error detail", icon=":material/bug_report:"):
            st.code(error, language="text")
        return

    if not references:
        components.empty_state(
            "No references found",
            "No reference list could be extracted from this PDF.",
            icon=":material/account_tree:",
        )
        return

    enriched = [r for r in references if r.citation_count is not None or r.abstract]
    st.caption(
        f"{len(references)} references extracted"
        + (f" · {len(enriched)} with fetched metadata" if enriched else " · metadata not fetched")
    )

    tab_list, tab_graph = st.tabs(["References", "Citation graph"])

    with tab_list:
        _reference_list(references)

    with tab_graph:
        _graph(doc, references)


def _load(doc: Document, preloaded: tuple[Reference, ...]):
    """Use references handed in by the caller, else load them lazily."""
    if preloaded:
        return preloaded, ""
    cached = state.get(state.REFERENCES)
    if cached:
        return cached, state.error_for("Citation analysis")

    with st.spinner("Extracting the reference list…"):
        references, error = pipeline.load_references(state.get(state.DOC_KEY) or "", doc)

    st.session_state[state.REFERENCES] = references
    if error:
        state.record_error("Citation analysis", error)
    return references, error


def _reference_list(references: tuple[Reference, ...]) -> None:
    for position, ref in enumerate(references):
        with st.container(border=True):
            st.markdown(f"**{ref.title}**")

            meta = [part for part in (ref.authors, ref.year) if part]
            if ref.citation_count is not None:
                meta.append(f"{ref.citation_count:,} citations")
            if meta:
                st.caption(" · ".join(meta))

            if ref.from_cache:
                st.badge("Cached metadata", icon=":material/cached:", color="gray",
                         help="Served from the local cache rather than a live lookup.")

            if ref.purpose:
                st.markdown(f"_Why cited:_ {ref.purpose}")

            if ref.abstract:
                with st.expander("Abstract", icon=":material/article:"):
                    st.markdown(ref.abstract)

            if ref.url:
                st.markdown(f"[Open reference]({ref.url})")

            if ref.citation_count is None and not ref.abstract and not ref.purpose:
                st.caption(
                    ":material/cloud_off: Metadata not fetched for this reference."
                )
            _ = position


def _graph(doc: Document, references: tuple[Reference, ...]) -> None:
    """Feature 7 — the research family tree, as a Graphviz digraph.

    Only the most-cited references are drawn. A 40-node star graph is unreadable,
    and the point of this view is lineage, not completeness.
    """
    ranked = sorted(
        references,
        key=lambda r: (r.citation_count is None, -(r.citation_count or 0), r.year),
    )[:8]

    if not ranked:
        components.empty_state("Nothing to plot", icon=":material/hub:")
        return

    current = _escape(doc.title or "This paper")

    lines = [
        "digraph G {",
        '  rankdir=BT;',
        '  bgcolor="transparent";',
        '  node [shape=box, style="rounded,filled", fontname="Inter", fontsize=10,'
        '        color="#e6e6e6", fillcolor="#ffffff", fontcolor="#000000", margin="0.14,0.09"];',
        '  edge [color="#a39e98", arrowsize=0.6];',
        f'  current [label="{current}", fillcolor="#000000", fontcolor="#ffffff"];',
    ]

    for i, ref in enumerate(ranked):
        label = _escape(_shorten(ref.title))
        if ref.year:
            label += f"\\n{ref.year}"
        lines.append(f'  ref{i} [label="{label}"];')
        lines.append(f"  ref{i} -> current;")

    lines.append("}")

    st.graphviz_chart("\n".join(lines), width="stretch")
    st.caption(
        "Most-cited references feeding into this paper. "
        + ("Ordered by citation count." if ranked[0].citation_count is not None
           else "Citation counts unavailable, so ordering is by appearance.")
    )


def _shorten(title: str, limit: int = 40) -> str:
    title = title.strip()
    return title if len(title) <= limit else title[: limit - 1].rstrip() + "…"


def _escape(text: str) -> str:
    """Graphviz labels are quoted strings; escape what would break out of them."""
    return text.replace("\\", " ").replace('"', "'").replace("\n", " ")
