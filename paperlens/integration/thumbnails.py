"""Page thumbnails for the visual index.

PyMuPDF is already a dependency (``integration/highlight.py`` uses it for
evidence geometry), so rasterising a page costs nothing extra to install and very
little to run: the full 15-page demo paper renders in ~0.17s at ~20KB per page.

The cache therefore is not rescuing a slow operation — it is stopping the work
repeating on every Streamlit rerun, which happens on *every* click in the app.

Failure returns ``None`` rather than raising. A page that will not rasterise
(encrypted, malformed, an exotic colourspace) should cost the reader one tile in
a grid, not the whole panel.
"""

from __future__ import annotations

import logging

import streamlit as st

logger = logging.getLogger("paperlens.thumbnails")

#: Render scale. 0.35 gives roughly 215x278px for a Letter page — small enough
#: for a five-across grid, large enough that a figure or table is recognisable,
#: which is the entire point of showing pages instead of page numbers.
DEFAULT_SCALE = 0.35


@st.cache_data(show_spinner=False, max_entries=512)
def page_thumbnail(
    cache_key: str,
    pdf_path: str,
    page_no: int,
    scale: float = DEFAULT_SCALE,
) -> bytes | None:
    """Render one 1-based page to PNG bytes, or ``None`` if it cannot be drawn.

    ``cache_key`` is the document content hash; it is part of the cache identity
    so two papers cannot serve each other's thumbnails.
    """
    import fitz  # PyMuPDF

    try:
        with fitz.open(pdf_path) as doc:
            if not 1 <= page_no <= doc.page_count:
                return None
            pixmap = doc[page_no - 1].get_pixmap(matrix=fitz.Matrix(scale, scale))
            return pixmap.tobytes("png")
    except Exception:  # noqa: BLE001 - a bad page must not take out the grid
        logger.exception("paperlens: could not render thumbnail for page %s", page_no)
        return None
