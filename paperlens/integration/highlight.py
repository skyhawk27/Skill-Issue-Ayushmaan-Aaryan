"""Turn a verified quote into bounding boxes the PDF viewer can draw.

The gap this closes
-------------------
The PRD's Feature 4B stores "the matched span's character offsets so the PDF
viewer can highlight the exact matched text". But ``streamlit-pdf-viewer`` draws
overlays from *geometry* — ``{page, x, y, width, height, color, border}`` in PDF
points — and character offsets into a page's extracted text say nothing about
where that text sits on the page. Nobody in the team contract owns the
conversion, and without it clicking a claim jumps to the right page and
highlights nothing: the demo's whole "look, it's actually checked" moment lands
flat.

So this module owns it, and does it geometrically rather than from offsets, which
turns out to be both simpler and more robust:

1. ``page.search_for(quote)`` — PyMuPDF's own search. Fast, exact, and already
   dehyphenates across line breaks. Wins on short quotes.
2. A **word-run fallback** for when that returns nothing, which happens more
   often than you would hope: LLM quotes are long, get re-wrapped, lose
   ligatures, and pick up stray whitespace. We walk the page's words with
   ``rapidfuzz`` over a sliding window and take the best-scoring contiguous run.
3. Both are tried on the claimed page **and its neighbours**, because the PRD
   anticipates off-by-one page attribution.

Rects are unioned per text line, so a quote spanning three lines yields three
tidy boxes rather than one that swallows the paragraph.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Sequence

import streamlit as st

logger = logging.getLogger("paperlens.highlight")

#: Minimum rapidfuzz score (0-1) for the word-run fallback to accept a match.
#: Below this we would be drawing a box around roughly-similar prose, which is
#: worse than drawing nothing — a wrong highlight actively misleads.
_MIN_RUN_SCORE = 0.62

#: Cap on how much of the quote we try to locate. Very long quotes are slow to
#: window over and rarely sit on one page anyway.
_MAX_QUOTE_WORDS = 60

_WS = re.compile(r"\s+")


@dataclass(frozen=True)
class HighlightResult:
    """Boxes for one quote, plus how they were found.

    ``page`` is where the text was *actually* located, which may differ from the
    page the claim asserted — the caller should navigate here, not to the claimed
    page, so an off-by-one attribution still lands on the evidence.
    """

    annotations: tuple[dict[str, Any], ...] = ()
    page: int | None = None
    method: str = "none"      # "exact" | "fuzzy" | "none"
    score: float | None = None

    @property
    def found(self) -> bool:
        return bool(self.annotations)


def _normalise(text: str) -> str:
    """Collapse whitespace and neutralise the characters PDFs mangle."""
    text = text.replace("­", "")           # soft hyphen
    text = text.replace("ﬁ", "fi").replace("ﬂ", "fl").replace("ﬀ", "ff")
    text = text.replace("’", "'").replace("‘", "'")
    text = text.replace("“", '"').replace("”", '"')
    text = text.replace("–", "-").replace("—", "-").replace("−", "-")
    return _WS.sub(" ", text).strip()


def _candidate_pages(page: int | None, page_count: int) -> list[int]:
    """The claimed page first, then its neighbours (PRD: off-by-one tolerance)."""
    if page is None:
        return []
    order = [page, page - 1, page + 1]
    return [p for p in order if 1 <= p <= page_count]


def _rects_to_annotations(
    rects: Sequence[Any],
    page_no: int,
    color: str,
    page_origin: tuple[float, float],
) -> list[dict[str, Any]]:
    """Convert PyMuPDF rects into the viewer's annotation dicts.

    ``page_origin`` is the page rect's top-left. Most PDFs put it at (0, 0), but
    a cropped page does not, and pdf.js renders relative to the crop box — so we
    subtract it or every box lands offset by the crop margin.
    """
    annotations: list[dict[str, Any]] = []
    ox, oy = page_origin
    for rect in rects:
        width, height = rect.x1 - rect.x0, rect.y1 - rect.y0
        # Degenerate rects come back for whitespace-only matches.
        if width <= 1 or height <= 1:
            continue
        annotations.append(
            {
                "page": page_no,
                "x": round(rect.x0 - ox, 2),
                "y": round(rect.y0 - oy, 2),
                "width": round(width, 2),
                "height": round(height, 2),
                "color": color,
                "border": "solid",
            }
        )
    return annotations


def _merge_by_line(words: Sequence[tuple], fitz_module: Any) -> list[Any]:
    """Union the rects of matched words, grouped by their text line.

    ``page.get_text("words")`` yields
    ``(x0, y0, x1, y1, word, block_no, line_no, word_no)``, so words carry their
    own line identity — no need to infer lines from y-coordinates.
    """
    lines: dict[tuple[int, int], Any] = {}
    for x0, y0, x1, y1, _word, block_no, line_no, *_ in words:
        key = (block_no, line_no)
        rect = fitz_module.Rect(x0, y0, x1, y1)
        lines[key] = rect if key not in lines else lines[key] | rect
    return list(lines.values())


def _find_word_run(page: Any, quote: str, fitz_module: Any) -> tuple[list[Any], float]:
    """Locate ``quote`` by sliding a window over the page's words.

    Returns the per-line rects of the best-scoring run and its score. This is the
    workhorse: it survives re-wrapping, hyphenation, ligature loss and minor
    LLM transcription drift, none of which ``search_for`` tolerates.
    """
    from rapidfuzz import fuzz

    words = page.get_text("words")
    if not words:
        return [], 0.0

    quote_tokens = _normalise(quote).split()[:_MAX_QUOTE_WORDS]
    if len(quote_tokens) < 3:
        return [], 0.0

    needle = " ".join(quote_tokens).lower()
    normalised = [_normalise(w[4]).lower() for w in words]
    window = len(quote_tokens)

    best_score, best_slice = 0.0, None
    # Try a few window widths: extracted text often splits or joins tokens
    # relative to the quote, so an exact-width window can undershoot.
    for width in {window, max(3, int(window * 0.8)), int(window * 1.2) + 1}:
        if width > len(words):
            continue
        for start in range(0, len(words) - width + 1):
            candidate = " ".join(normalised[start : start + width])
            if not candidate:
                continue
            score = fuzz.ratio(needle, candidate) / 100.0
            if score > best_score:
                best_score, best_slice = score, (start, start + width)

    if best_slice is None or best_score < _MIN_RUN_SCORE:
        return [], best_score

    start, end = best_slice
    return _merge_by_line(words[start:end], fitz_module), best_score


def _locate_on_page(page: Any, quote: str, fitz_module: Any) -> tuple[list[Any], str, float]:
    """Try exact search, then the word-run fallback, on a single page."""
    normalised = _normalise(quote)

    # PyMuPDF's own search handles line-wrapped text and is much faster than
    # windowing, so it always gets first refusal.
    try:
        rects = page.search_for(normalised)
    except Exception:
        rects = []
    if rects:
        return list(rects), "exact", 1.0

    # A long quote may be cut off by the page break. Retry on a leading slice,
    # which is usually enough to anchor the highlight.
    tokens = normalised.split()
    if len(tokens) > 12:
        try:
            rects = page.search_for(" ".join(tokens[:12]))
        except Exception:
            rects = []
        if rects:
            return list(rects), "exact", 1.0

    rects, score = _find_word_run(page, quote, fitz_module)
    if rects:
        return rects, "fuzzy", score
    return [], "none", score


#: The paragraph frame's colour — ``{colors.ink-faint}``. Deliberately recessive:
#: it should say "the sentence lives here" without competing with the
#: status-coloured box that carries the actual verdict.
PARAGRAPH_INK = "#a39e98"


@dataclass(frozen=True)
class Passage:
    """A located quote, plus the paragraph it sits inside.

    ``annotations`` puts the **sentence rects first**. That ordering is load
    bearing: ``scroll_to_annotation`` is positional, so first-in-list is what the
    viewer centres on, and centring the sentence rather than the top of its
    paragraph is the whole point of locating both.
    """

    page: int | None = None
    paragraph_index: int | None = None      # 1-based, reading order
    paragraph_text: str = ""
    annotations: tuple[dict[str, Any], ...] = ()
    method: str = "none"                    # "exact" | "fuzzy" | "none"
    score: float | None = None

    @property
    def found(self) -> bool:
        return bool(self.annotations)

    @property
    def locator(self) -> str:
        """Human-readable position, e.g. ``"Page 3 · paragraph 2"``."""
        if self.page is None:
            return ""
        if self.paragraph_index is None:
            return f"Page {self.page}"
        return f"Page {self.page} · paragraph {self.paragraph_index}"


def _paragraph_for(page: Any, quote: str) -> tuple[int | None, str, Any]:
    """The text block containing ``quote``: 1-based reading index, text, rect.

    PyMuPDF blocks correspond closely to paragraphs, and sorting them top-to-
    bottom then left-to-right gives an index a human can actually count to on the
    page — which is what makes "paragraph 2" a usable reference rather than an
    opaque id.
    """
    try:
        blocks = page.get_text("blocks")
    except Exception:
        return None, "", None

    # Text blocks only (block_type 0); sorted into reading order.
    text_blocks = [b for b in blocks if len(b) < 7 or b[6] == 0]
    text_blocks.sort(key=lambda b: (round(b[1], 1), round(b[0], 1)))

    needle = _normalise(quote).lower()
    if not needle:
        return None, "", None
    # Match on a leading slice: the full quote may wrap beyond one block.
    probe = needle[:60]

    for index, block in enumerate(text_blocks, start=1):
        body = _normalise(block[4]).lower()
        if probe and probe in body:
            import fitz  # PyMuPDF

            rect = fitz.Rect(block[0], block[1], block[2], block[3])
            return index, _normalise(block[4]), rect
    return None, "", None


def locate_passage(
    pdf_path: str,
    quote: str,
    page: int | None,
    color: str,
) -> Passage:
    """Locate ``quote`` and the paragraph around it, ready to draw.

    Degrades in steps rather than failing: no paragraph → sentence only; no
    sentence → nothing drawn and the caller says so. Neither is an error state.
    """
    result = locate_quote(pdf_path, quote, page, color)
    if not result.found or result.page is None:
        return Passage(page=result.page, method=result.method, score=result.score)

    paragraph_index: int | None = None
    paragraph_text = ""
    paragraph_annotation: dict[str, Any] | None = None

    import fitz  # PyMuPDF

    try:
        with fitz.open(pdf_path) as doc:
            pdf_page = doc[result.page - 1]
            paragraph_index, paragraph_text, rect = _paragraph_for(pdf_page, quote)
            if rect is not None:
                origin = (pdf_page.rect.x0, pdf_page.rect.y0)
                boxes = _rects_to_annotations([rect], result.page, PARAGRAPH_INK, origin)
                if boxes:
                    # Dashed, because the component draws outlines only — there is
                    # no fill available, so weight and style carry the distinction.
                    boxes[0]["border"] = "dashed"
                    paragraph_annotation = boxes[0]
    except Exception:
        logger.exception("paperlens: could not resolve the paragraph on page %s", result.page)

    annotations = list(result.annotations)          # sentence first — see docstring
    if paragraph_annotation is not None:
        annotations.append(paragraph_annotation)

    return Passage(
        page=result.page,
        paragraph_index=paragraph_index,
        paragraph_text=paragraph_text,
        annotations=tuple(annotations),
        method=result.method,
        score=result.score,
    )


@st.cache_data(show_spinner=False, max_entries=256)
def locate_passage_cached(
    cache_key: str,
    pdf_path: str,
    quote: str,
    page: int | None,
    color: str,
) -> Passage:
    """Cached :func:`locate_passage`, keyed on the document plus the quote."""
    return locate_passage(pdf_path, quote, page, color)


def locate_quote(
    pdf_path: str,
    quote: str,
    page: int | None,
    color: str,
) -> HighlightResult:
    """Find ``quote`` in the PDF and return drawable annotations.

    Searches the claimed page first, then its neighbours. The first page with a
    usable match wins; an exact hit anywhere beats a fuzzy hit elsewhere.
    """
    if not quote or not quote.strip():
        return HighlightResult(page=page)

    import fitz  # PyMuPDF

    try:
        doc = fitz.open(pdf_path)
    except Exception:
        logger.exception("paperlens: could not open %s for highlighting", pdf_path)
        return HighlightResult(page=page)

    try:
        pages = _candidate_pages(page, doc.page_count) or list(range(1, doc.page_count + 1))

        best: HighlightResult | None = None
        for page_no in pages:
            pdf_page = doc[page_no - 1]

            if pdf_page.rotation:
                # Extracted coordinates are in unrotated space while pdf.js draws
                # rotated, so boxes would be misplaced. Better to jump to the
                # page with no highlight than to draw a confidently wrong one.
                logger.warning(
                    "paperlens: page %s is rotated %s°; skipping highlight geometry",
                    page_no, pdf_page.rotation,
                )
                continue

            rects, method, score = _locate_on_page(pdf_page, quote, fitz)
            if not rects:
                continue

            origin = (pdf_page.rect.x0, pdf_page.rect.y0)
            annotations = _rects_to_annotations(rects, page_no, color, origin)
            if not annotations:
                continue

            result = HighlightResult(
                annotations=tuple(annotations),
                page=page_no,
                method=method,
                score=score,
            )
            if method == "exact":
                return result          # can't do better than exact
            if best is None:
                best = result          # keep the first fuzzy hit, keep looking

        return best or HighlightResult(page=page)
    finally:
        doc.close()


@st.cache_data(show_spinner=False, max_entries=256)
def locate_quote_cached(
    cache_key: str,
    pdf_path: str,
    quote: str,
    page: int | None,
    color: str,
) -> HighlightResult:
    """Cached :func:`locate_quote`.

    Keyed on the document's content hash plus the quote, so clicking back and
    forth between claims is instant. The word-run fallback is the expensive path
    (it windows over every word on the page), which is exactly what makes
    caching worth it here.
    """
    return locate_quote(pdf_path, quote, page, color)
