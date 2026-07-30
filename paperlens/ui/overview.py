"""The abstract and the visual page index, shown above the verified claims.

Together these answer the two questions a reader has before reading anything
else: *what is this paper about* and *where in it should I look*. They sit at the
top of the Summary panel so the reading order is overview → pick a page → the
claims that were checked.

The page index is deliberately more than a picker. Selecting a page also drives
the PDF pane through :func:`ui.state.goto_page`, so it doubles as document
navigation — one control, two jobs, and no second place to keep in sync.

No custom CSS. ``ui/splash.py`` remains the only file with a stylesheet.
"""

from __future__ import annotations

import streamlit as st

from integration.contracts import Abstract, Document, PageSummary
from integration.thumbnails import page_thumbnail
from ui import state

#: Thumbnails per row. Five fits the content pane without the tiles becoming
#: unreadable; more and a page is just a grey rectangle.
_COLUMNS = 5

#: Cap on pages shown at once. Long papers get a page-range selector instead of
#: several hundred images, which would be slow to scroll and useless to scan.
_PAGE_CHUNK = 20


def render_abstract(abstract: Abstract) -> None:
    """The plain-language overview: labelled bullets, prerequisites, synthesis."""
    if abstract.is_empty:
        return

    st.subheader("In short", anchor=False)
    with st.container(border=True):
        for label, text in abstract.points:
            st.markdown(f"**{label}:** {text}")

        if abstract.prerequisites:
            st.space("small")
            st.caption("Helpful background")
            with st.container(horizontal=True, horizontal_alignment="left"):
                for item in abstract.prerequisites:
                    st.badge(item, color="gray")

        if abstract.conclusion:
            # 150-200 words is too much for the top of a panel and too useful to
            # drop, so it is one click away rather than gone.
            with st.expander("Full synthesis", icon=":material/notes:"):
                st.markdown(abstract.conclusion)


def render_page_index(doc: Document, summaries: tuple[PageSummary, ...]) -> None:
    """A grid of page thumbnails; picking one shows its summary and moves the PDF."""
    pdf_path = state.get(state.PDF_PATH)
    page_count = doc.page_count or (max((s.page for s in summaries), default=0))
    if not pdf_path or page_count < 1:
        return

    by_page = {s.page: s for s in summaries}

    st.subheader("Pages", anchor=False)
    st.caption("Pick a page to read what it covers. The viewer follows along.")

    first, last = _visible_range(page_count)
    selected = _selected_page(page_count)

    for row_start in range(first, last + 1, _COLUMNS):
        row = range(row_start, min(row_start + _COLUMNS, last + 1))
        for column, page_no in zip(st.columns(_COLUMNS), row):
            with column:
                _page_tile(pdf_path, page_no, selected=page_no == selected,
                           has_summary=page_no in by_page)

    st.space("small")
    _selected_summary(by_page.get(selected), selected)


def _visible_range(page_count: int) -> tuple[int, int]:
    """Which pages to draw. Chunked for long papers, with a range picker."""
    if page_count <= _PAGE_CHUNK:
        return 1, page_count

    chunks = [
        (start, min(start + _PAGE_CHUNK - 1, page_count))
        for start in range(1, page_count + 1, _PAGE_CHUNK)
    ]
    choice = st.selectbox(
        "Page range",
        options=chunks,
        format_func=lambda pair: f"Pages {pair[0]}–{pair[1]}",
        key="pl.page_index_range",
    )
    return choice


def _selected_page(page_count: int) -> int:
    """The page whose summary is showing. Defaults to the first."""
    current = state.get(state.PAGE_SELECTED)
    if isinstance(current, int) and 1 <= current <= page_count:
        return current
    return 1


def _page_tile(pdf_path: str, page_no: int, *, selected: bool, has_summary: bool) -> None:
    """One thumbnail plus its select button."""
    image = page_thumbnail(state.get(state.DOC_KEY) or "", pdf_path, page_no)

    if image is not None:
        st.image(image, width="stretch")
    else:
        # Rasterising failed. Keep the page selectable — it is still navigation.
        with st.container(border=True, horizontal_alignment="center"):
            st.markdown(f"### {page_no}")
            st.caption("preview\nunavailable")

    st.button(
        f"Page {page_no}",
        key=f"pl.page-index-{page_no}",
        type="primary" if selected else "secondary",
        width="stretch",
        help=None if has_summary else "No summary was generated for this page.",
        on_click=_select_page,
        args=(page_no,),
    )


def _select_page(page_no: int) -> None:
    """Show this page's summary *and* move the viewer to it."""
    st.session_state[state.PAGE_SELECTED] = page_no
    state.goto_page(page_no)


def _selected_summary(summary: PageSummary | None, page_no: int) -> None:
    """The chosen page's summary, or an honest note that there isn't one."""
    with st.container(border=True):
        st.markdown(f"**Page {page_no}**")
        if summary is not None and summary.has_summary:
            st.markdown(summary.summary)
        else:
            st.caption(
                "No summary was generated for this page. You can still read it in "
                "the viewer on the right."
            )
