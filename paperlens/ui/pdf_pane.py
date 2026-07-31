"""The right-hand PDF pane: page navigation, and the highlighted evidence.

Behaviour verified against the installed ``streamlit-pdf-viewer`` frontend
(``PdfViewer.vue``, recovered from its source map), because two things about it
are load-bearing and neither is in the docs.

**1. The component does not react to navigation props.** It declares exactly
three watchers::

    watch(() => props.args.binary, ...)
    watch(() => props.args.zoom_level, ...)
    watch(() => props.args.viewer_align, ...)

There is no watcher on ``scroll_to_page``, ``scroll_to_annotation``,
``annotations`` or ``pages_to_render``. The scroll routine only runs inside the
PDF load path, reached via ``onMounted`` or a change of ``binary``. So passing a
new ``scroll_to_page`` for the same document does *nothing* — the iframe just
sits there.

The fix is to change the Streamlit component ``key`` whenever the target moves.
A new key makes Streamlit destroy and recreate the component, which remounts it
and re-runs the load-and-scroll path. It is the only reliable trigger available
from Python, and it is why :func:`_viewer` builds its key from the navigation
state rather than using a constant.

**2. Page numbers are absolute.** ``scroll_to_page`` and ``annotations[].page``
are absolute 1-based PDF pages. (The docstring calls ``scroll_to_page`` a
"positional value", which reads as an index into ``pages_to_render``; the
component resolves ``getElementById("canvas_page_" + scroll_to_page)`` against
ids assigned from the absolute number.)

The whole document is rendered — no page windowing. Windowing was cheaper, but it
meant the viewer only ever held a slice of the paper, which is wrong for a tool
whose job is reading around the evidence.
"""

from __future__ import annotations

import hashlib

import streamlit as st
from streamlit_pdf_viewer import pdf_viewer

from integration import highlight
from ui import state
from ui.theme import PDF_VIEWER_HEIGHT, PDF_VIEWER_WIDTH, style_for


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
    """Locate the quote *and* its paragraph, cached on the document + quote."""
    if not quote.strip():
        return highlight.Passage(page=page)
    return highlight.locate_passage_cached(
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
        if page_count > 1:
            st.number_input(
                "Go to page",
                min_value=1,
                max_value=page_count,
                value=page,
                step=1,
                key="pl.pdf.goto",
                label_visibility="collapsed",
                on_change=_goto_from_input,
                help="Jump to a page number.",
                width=110,
            )

    _highlight_caption(result)


def _goto_from_input() -> None:
    """Callback for the page-number box."""
    requested = st.session_state.get("pl.pdf.goto")
    if isinstance(requested, int):
        state.goto_page(requested)


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

    # Where it landed, not just that it landed — the locator is the point of
    # resolving the paragraph at all.
    where = getattr(result, "locator", "")
    suffix = f" — {where.lower()}." if where and result.method != "none" else "."

    if result.method == "exact":
        st.caption(f":material/check_small: Matched text highlighted{suffix}")
    elif result.method == "fuzzy":
        score = f" (closest match, {result.score:.0%} similar)" if result.score else " (closest match)"
        st.caption(f":material/change_circle: Highlighted the nearest wording{score}{suffix}")
    else:
        st.caption(
            ":material/search_off: This quote could not be located on the page — "
            "showing the page without a highlight."
        )


def _nav_key(pdf_path: str, page: int, quote: str) -> str:
    """A component key that changes exactly when the view should move.

    The component has no watcher on its navigation props, so a stable key means
    a claim click updates nothing. Folding the target page and quote into the key
    forces a remount, which re-runs the component's load-and-scroll path.

    The document is in the key too, so switching papers cannot reuse a mounted
    viewer still holding the previous one.
    """
    digest = hashlib.md5(f"{pdf_path}|{page}|{quote}".encode()).hexdigest()[:10]
    return f"pl.pdf.viewer.{digest}"


def _viewer(pdf_path: str, page: int, page_count: int, result) -> None:
    """Render the component itself."""
    # Render the whole document. `pages_to_render=[]` means "all pages".
    annotations = [dict(a) for a in result.annotations]

    # Only one of these may be passed — the component raises if both are set.
    # Prefer the annotation, which centres the matched span rather than parking
    # the page top at the fold.
    scroll_kwargs: dict[str, int] = {}
    if annotations:
        scroll_kwargs["scroll_to_annotation"] = 1
    else:
        scroll_kwargs["scroll_to_page"] = page

    try:
        pdf_viewer(
            pdf_path,
            width=PDF_VIEWER_WIDTH,
            height=PDF_VIEWER_HEIGHT,
            annotations=annotations,
            pages_to_render=[],
            annotation_outline_size=2,
            render_text=True,
            zoom_level="auto",
            viewer_align="center",
            show_page_separator=True,
            scroll_behavior="smooth",
            key=_nav_key(pdf_path, page, state.get(state.TARGET_QUOTE) or ""),
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
