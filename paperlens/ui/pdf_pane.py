"""The right-hand PDF pane: page navigation, and the highlighted evidence.

Behaviour verified directly against the installed ``streamlit-pdf-viewer``
frontend bundle, because its docstrings are ambiguous on two points that matter:

* ``scroll_to_page`` is the **absolute** 1-based PDF page number. (The docstring
  says "positional value", which reads as an index into ``pages_to_render``; the
  bundle resolves ``getElementById("canvas_page_" + scroll_to_page)`` and assigns
  that id from the absolute page number, so absolute is correct.)
* ``annotations[].page`` is likewise matched against the absolute page number,
  and ``scroll_to_annotation`` centres that box in the viewport.

Knowing both let us render only a window of pages around the point of interest.
That matters more than it sounds: every claim click is a Streamlit rerun, which
re-renders the component, and painting 40 canvases at device pixel ratio on each
click makes the whole app feel broken. A three-page window keeps it instant.
"""

from __future__ import annotations

import streamlit as st
from streamlit_pdf_viewer import pdf_viewer

from integration import highlight
from ui import state
from ui.theme import PDF_VIEWER_HEIGHT, PDF_VIEWER_WIDTH, style_for

#: Pages either side of the target to render alongside it. One is enough to read
#: across a page break without paying for the whole document.
_WINDOW = 1

_SHOW_ALL = "pl.pdf_show_all"


def render(page_count: int) -> None:
    """Draw the PDF pane. ``page_count`` bounds the navigation controls."""
    pdf_path = state.get(state.PDF_PATH)
    if not pdf_path:
        return

    target_page = max(1, min(int(state.get(state.TARGET_PAGE) or 1), max(1, page_count)))
    quote = state.get(state.TARGET_QUOTE) or ""
    status = state.get(state.TARGET_STATUS)

    result = _resolve_highlight(pdf_path, quote, target_page, status)

    # A quote found on a neighbouring page means the claim's page attribution was
    # off by one. Follow the evidence, not the claim.
    if result.found and result.page and result.page != target_page:
        target_page = result.page

    _nav_row(target_page, page_count, result)
    _viewer(pdf_path, target_page, page_count, result)


def _resolve_highlight(pdf_path: str, quote: str, page: int, status: str | None):
    """Locate the quote, cached on the document + quote."""
    if not quote.strip():
        return highlight.HighlightResult(page=page)
    return highlight.locate_quote_cached(
        state.get(state.DOC_KEY) or "",
        pdf_path,
        quote,
        page,
        style_for(status).highlight,
    )


def _nav_row(page: int, page_count: int, result) -> None:
    """Page controls, plus an honest report of what the highlight actually did."""
    with st.container(horizontal=True, vertical_alignment="center"):
        st.button(
            "",
            key="pl.pdf.prev",
            icon=":material/chevron_left:",
            disabled=page <= 1,
            help="Previous page",
            on_click=state.goto_page,
            args=(page - 1,),
        )
        st.button(
            "",
            key="pl.pdf.next",
            icon=":material/chevron_right:",
            disabled=page >= page_count,
            help="Next page",
            on_click=state.goto_page,
            args=(page + 1,),
        )
        st.markdown(f"**Page {page}** of {page_count}")
        st.space("stretch")
        st.toggle(
            "All pages",
            key=_SHOW_ALL,
            help=(
                "Render the whole document instead of just the pages around the "
                "current one. Slower on long papers."
            ),
        )

    _highlight_caption(result)


def _highlight_caption(result) -> None:
    """Say what happened to the highlight, in plain language.

    A viewer that jumps to a page and draws nothing, with no explanation, reads
    as a bug. Naming the outcome — especially "could not locate" — is also the
    honest thing to do: it is the same failure the verification badge is warning
    about, surfaced where the user is looking.
    """
    quote = state.get(state.TARGET_QUOTE) or ""
    if not quote.strip():
        st.caption("Select a claim to highlight its supporting quote here.")
        return

    if result.method == "exact":
        st.caption(":material/check_small: Matched text highlighted.")
    elif result.method == "fuzzy":
        score = f" (closest match, {result.score:.0%} similar)" if result.score else " (closest match)"
        st.caption(f":material/change_circle: Highlighted the nearest wording{score}.")
    else:
        st.caption(
            ":material/search_off: This quote could not be located on the page — "
            "showing the page without a highlight."
        )


def _viewer(pdf_path: str, page: int, page_count: int, result) -> None:
    """Render the component itself."""
    show_all = bool(st.session_state.get(_SHOW_ALL))
    if show_all:
        pages_to_render: list[int] = []       # empty means "all", per the component
    else:
        low = max(1, page - _WINDOW)
        high = min(page_count, page + _WINDOW)
        pages_to_render = list(range(low, high + 1))

    annotations = [dict(a) for a in result.annotations]

    # Only one of these may be passed — the component raises if both are set.
    # Prefer the annotation, which centres the matched span rather than parking
    # the page top at the fold.
    scroll_kwargs: dict[str, int] = {}
    if annotations and (show_all or annotations[0]["page"] in pages_to_render):
        scroll_kwargs["scroll_to_annotation"] = 1
    else:
        scroll_kwargs["scroll_to_page"] = page

    try:
        pdf_viewer(
            pdf_path,
            width=PDF_VIEWER_WIDTH,
            height=PDF_VIEWER_HEIGHT,
            annotations=annotations,
            pages_to_render=pages_to_render,
            annotation_outline_size=2,
            render_text=True,
            zoom_level="auto",
            viewer_align="center",
            show_page_separator=True,
            scroll_behavior="smooth",
            key="pl.pdf.viewer",
            **scroll_kwargs,
        )
    except Exception as exc:  # noqa: BLE001
        # The viewer is a third-party iframe component; if it fails we still want
        # the rest of the dashboard usable, and the user told where to look.
        st.error(
            "The PDF viewer could not render this document.",
            icon=":material/broken_image:",
        )
        st.caption(f"{type(exc).__name__}: {exc}")
