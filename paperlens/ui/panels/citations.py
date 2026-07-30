"""Citations panel — PRD Feature 6, built to be concluded from rather than read.

The earlier version showed forty bordered cards and a node-link graph in which
every reference pointed at one node. Both were the wrong *form*:

* A node-link diagram's job is **topology**. A star has none, so it conveyed
  nothing at any size — enlarging it would not have helped. Influence is a
  magnitude comparison, so it becomes a **bar chart, one hue, sorted**.
* Past roughly seven items that all carry meaning, the right form is a **table**,
  not more cards. Forty cards each carrying a title, caption, purpose, abstract
  expander and link is the textbook version of that mistake.

So the panel now leads with conclusions, shows one chart that supports them, and
puts the full list in a single sortable table.

The PRD's reliability note is taken literally: a missing citation count is a
normal state rendered as *absence*, never as a fabricated number. In a product
whose premise is verifiability, an invented figure would be self-defeating.
"""

from __future__ import annotations

import streamlit as st

from integration import citation_stats, pipeline
from integration.contracts import Document, Reference
from ui import components, state

#: One hue for magnitude, taken from the theme's ink ramp. Emphatically not the
#: cycled categorical list: these bars are all the same measure, and colouring
#: them differently would imply a distinction that does not exist.
_BAR_INK = "#31302e"


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

    stats = citation_stats.summarise(references)

    _headline(stats)
    _chart(stats)
    _table(references, stats)
    _purposes(references)
    _family_tree_note()


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


def _headline(stats: citation_stats.CitationStats) -> None:
    """The numbers, then the conclusions drawn from them."""
    columns = st.columns(3)
    columns[0].metric("References", stats.total)
    columns[1].metric("With metadata", stats.resolved)
    columns[2].metric("Median year", stats.median_year if stats.median_year else "—")

    points = citation_stats.headline_points(stats)
    if points:
        with st.container(border=True):
            for point in points:
                st.markdown(f"- {point}")


def _chart(stats: citation_stats.CitationStats) -> None:
    """Influence where citation counts exist; the reference era otherwise.

    The fallback is not a consolation prize — "what years does this build on" is
    a real conclusion, and unlike citation counts it needs no network at all.
    """
    if stats.has_citation_counts:
        st.subheader("Most-cited references", anchor=False)
        st.bar_chart(
            {
                "reference": [_short(r.title) for r in stats.top_by_citations],
                "citations": [r.citation_count or 0 for r in stats.top_by_citations],
            },
            x="reference",
            y="citations",
            horizontal=True,   # long category names read far better on the y-axis
            color=_BAR_INK,
            height=300,
        )
        st.caption("How heavily the field leans on each — from the metadata lookup.")
        return

    if stats.has_years:
        st.subheader("What this paper builds on", anchor=False)
        st.bar_chart(
            {
                "year": list(stats.year_histogram),
                "references": list(stats.year_histogram.values()),
            },
            x="year",
            y="references",
            color=_BAR_INK,
            height=240,
        )
        st.caption(
            "Publication years across the reference list. Citation counts are "
            "unavailable, so this shows the paper's intellectual era instead."
        )


def _table(references: tuple[Reference, ...], stats: citation_stats.CitationStats) -> None:
    """Every reference in one sortable table, in place of forty cards."""
    st.subheader("All references", anchor=False)

    rows = [
        {
            "Title": ref.title,
            "Authors": ref.authors,
            "Year": _as_year(ref.year),
            "Citations": ref.citation_count,
            "Link": ref.url or None,
        }
        for ref in references
    ]

    column_config: dict[str, object] = {
        "Title": st.column_config.TextColumn("Title", width="large"),
        "Authors": st.column_config.TextColumn("Authors", width="medium"),
        "Year": st.column_config.NumberColumn("Year", format="%d", width="small"),
    }

    # Only offer columns that carry data. An all-empty column is noise, and an
    # empty ProgressColumn renders as a row of zero-width bars that look broken.
    if stats.has_citation_counts:
        column_config["Citations"] = st.column_config.ProgressColumn(
            "Citations",
            help="How often this reference has been cited.",
            format="%d",
            min_value=0,
            max_value=max((r.citation_count or 0) for r in references) or 1,
        )
    else:
        for row in rows:
            row.pop("Citations", None)

    if any(row.get("Link") for row in rows):
        column_config["Link"] = st.column_config.LinkColumn(
            "Link", display_text="Open", width="small"
        )
    else:
        for row in rows:
            row.pop("Link", None)

    st.dataframe(
        rows,
        column_config=column_config,
        hide_index=True,
        width="stretch",
        height=min(420, 45 + 35 * len(rows)),
    )
    st.caption("Sortable — click a column header to rank by year or citations.")


def _purposes(references: tuple[Reference, ...]) -> None:
    """Why each work was cited, behind one expander rather than forty inline blocks."""
    explained = [r for r in references if r.purpose]
    if not explained:
        return

    with st.expander(f"Why these were cited ({len(explained)})", icon=":material/psychology:"):
        for ref in explained:
            st.markdown(f"**{_short(ref.title, 70)}** — {ref.purpose}")


def _family_tree_note() -> None:
    """Say why the advertised family tree is not here, rather than just omitting it.

    Building it needs *this* paper resolved to an OpenAlex id plus multi-level
    lookups outward from it, which does not happen for an arbitrary uploaded PDF.
    The homepage lists the feature, so silence would read as a bug.
    """
    st.caption(
        ":material/hub: A research family tree needs this paper itself resolved in "
        "OpenAlex, which is not available for an uploaded PDF. The chart above "
        "shows what it builds on instead."
    )


def _as_year(year: str) -> int | None:
    try:
        return int(str(year).strip()[:4])
    except (TypeError, ValueError):
        return None


def _short(title: str, limit: int = 44) -> str:
    title = (title or "Untitled").strip()
    return title if len(title) <= limit else title[: limit - 1].rstrip() + "…"
