"""
document_processor.py
======================
Member 1 deliverable: Document Processing.

Public API:
    process_pdf(pdf_path: str, output_json_path: str | None = None) -> dict

Turns a raw research-paper PDF into a structured, searchable JSON object with:
    - metadata            (title guess, page count, filename, timestamp)
    - pages               (per-page raw text + char count)
    - sections            (detected headings with page ranges)
    - chunks              (page-mapped text chunks, ready for embedding/RAG
                            by Member 3, and for evidence-linking by Member 2)

Design notes for the team:
    - Every chunk carries page_start/page_end AND char offsets, so Member 2's
      summaries and Member 3's chat answers can cite an exact page.
    - Section detection uses font-size + bold heuristics from PyMuPDF's layout
      dict, with a fallback to numbered/keyword heading regexes. It is a
      heuristic, not a perfect parser -- academic PDFs vary a lot. If no
      sections are found, the whole document is treated as a single section
      so downstream chunking never breaks.
"""

from __future__ import annotations

import json
import re
import statistics
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

import fitz  # PyMuPDF


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CHUNK_TARGET_CHARS = 900        # ~ a few hundred tokens, good default for RAG
CHUNK_OVERLAP_CHARS = 150       # overlap so answers near a chunk boundary aren't lost

# Common academic-paper section names, used as a fallback signal for headings
# even when font-size heuristics are ambiguous (e.g. some PDFs flatten fonts).
KNOWN_SECTION_NAMES = [
    "abstract", "introduction", "related work", "background",
    "methodology", "method", "methods", "approach", "system design",
    "experiments", "experimental setup", "evaluation", "results",
    "discussion", "limitations", "future work", "conclusion",
    "conclusions", "acknowledgments", "acknowledgements", "references",
    "appendix",
]
NUMBERED_HEADING_RE = re.compile(r"^\s*(\d+(\.\d+)*)[\.\)]?\s+[A-Z][A-Za-z].{0,80}$")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class PageRecord:
    page_number: int          # 1-indexed
    text: str
    char_count: int


@dataclass
class SectionRecord:
    id: str
    title: str
    level: int
    page_start: int
    page_end: int


@dataclass
class ChunkRecord:
    id: str
    section_id: str | None
    section_title: str | None
    text: str
    page_start: int
    page_end: int
    char_start: int   # offset within the section's concatenated text
    char_end: int


@dataclass
class DocumentJSON:
    metadata: dict
    pages: list = field(default_factory=list)
    sections: list = field(default_factory=list)
    chunks: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "metadata": self.metadata,
            "pages": [asdict(p) for p in self.pages],
            "sections": [asdict(s) for s in self.sections],
            "chunks": [asdict(c) for c in self.chunks],
        }


# ---------------------------------------------------------------------------
# Step 1: raw extraction (text + layout metadata per page)
# ---------------------------------------------------------------------------

def _extract_pages_with_layout(doc: "fitz.Document") -> list[dict]:
    """Returns per-page plain text plus block-level layout info used for
    heading detection (font size, bold flag, text, page number)."""
    pages = []
    for page_index in range(len(doc)):
        page = doc[page_index]
        raw = page.get_text("dict")
        blocks_info = []
        for block in raw.get("blocks", []):
            if block.get("type") != 0:  # 0 = text block, 1 = image block
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span.get("text", "").strip()
                    if not text:
                        continue
                    blocks_info.append({
                        "text": text,
                        "size": round(span.get("size", 0), 1),
                        "bold": bool(span.get("flags", 0) & 2 ** 4),
                    })
        plain_text = page.get_text("text")
        pages.append({
            "page_number": page_index + 1,
            "text": plain_text,
            "spans": blocks_info,
        })
    return pages


# ---------------------------------------------------------------------------
# Step 2: heading / section detection
# ---------------------------------------------------------------------------

def _body_font_size(pages: list[dict]) -> float:
    """Estimate the modal (most common) font size across the doc = body text."""
    sizes = [s["size"] for p in pages for s in p["spans"] if s["text"]]
    if not sizes:
        return 10.0
    try:
        return statistics.mode(sizes)
    except statistics.StatisticsError:
        return statistics.median(sizes)


def _looks_like_heading(span_text: str, span_size: float, body_size: float, bold: bool) -> bool:
    text = span_text.strip()
    if not text or len(text) > 90:
        return False
    lowered = text.lower().strip(" :.-")
    if lowered in KNOWN_SECTION_NAMES:
        return True
    if NUMBERED_HEADING_RE.match(text):
        return True
    # font-size heuristic: noticeably larger than body text, or bold + short line
    if span_size >= body_size + 1.5:
        return True
    if bold and span_size >= body_size and len(text.split()) <= 8 and text[0:1].isupper():
        return True
    return False


def _build_full_text_with_page_offsets(pages: list[dict]) -> tuple[str, list[tuple[int, int, int]]]:
    """Concatenate all pages into one string, tracking which page each
    character offset came from. Returns (full_text, [(page_num, start, end), ...])."""
    full_text = ""
    offsets = []
    for page in pages:
        start = len(full_text)
        full_text += page["text"]
        end = len(full_text)
        offsets.append((page["page_number"], start, end))
        full_text += "\n"
    return full_text, offsets


def _detect_section_headings(pages: list[dict]) -> list[tuple[int, str]]:
    """Returns ordered (page_number, heading_text) candidates, merging
    consecutive heading spans on the same page (e.g. a title wrapped onto
    two lines) into a single candidate."""
    body_size = _body_font_size(pages)
    candidates: list[tuple[int, str]] = []
    for page in pages:
        buffer_parts: list[str] = []

        def flush():
            if buffer_parts:
                candidates.append((page["page_number"], " ".join(buffer_parts).strip()))
                buffer_parts.clear()

        for span in page["spans"]:
            text = span["text"].strip()
            if not text:
                continue
            if _looks_like_heading(text, span["size"], body_size, span["bold"]):
                buffer_parts.append(text)
            else:
                flush()
        flush()
    return candidates


def _page_for_offset(offsets: list[tuple[int, int, int]], char_pos: int) -> int:
    for page_num, start, end in offsets:
        if start <= char_pos <= end:
            return page_num
    return offsets[-1][0] if offsets else 1


def _locate_heading_offsets(
    candidates: list[tuple[int, str]],
    full_text: str,
    page_offsets: list[tuple[int, int, int]],
) -> list[tuple[str, int]]:
    """For each (page_number, heading_text) candidate, find where it starts
    in full_text. Searches within that page's char range first (fast + most
    reliable); falls back to matching just the first word if whitespace
    reconstruction differs from the span-level text used for detection.
    Keeps a monotonically increasing pointer so sections never overlap or
    appear out of order."""
    page_range = {p: (s, e) for p, s, e in page_offsets}
    located: list[tuple[str, int]] = []
    search_floor = 0
    for page_num, title in candidates:
        page_start, page_end = page_range.get(page_num, (search_floor, len(full_text)))
        search_from = max(page_start, search_floor)
        pos = full_text.find(title, search_from, page_end)
        if pos == -1:
            first_part = title.split(" ")[0]
            pos = full_text.find(first_part, search_from, page_end)
        if pos == -1:
            pos = search_from  # last resort: anchor to page/search start
        located.append((title, pos))
        search_floor = max(search_floor, pos)
    return located


def _detect_sections(pages: list[dict]) -> tuple[list[SectionRecord], dict[str, tuple[int, int]]]:
    """Returns (sections, char_bounds) where char_bounds maps section id ->
    (char_start, char_end) offsets into the full document text, used by the
    chunker to slice exactly this section's content (never a whole page's
    worth of text when several sections share one page)."""
    full_text, page_offsets = _build_full_text_with_page_offsets(pages)
    candidates = _detect_section_headings(pages)

    if not candidates:
        last_page = pages[-1]["page_number"] if pages else 1
        section = SectionRecord(id="sec_1", title="Full Document", level=1,
                                 page_start=1, page_end=last_page)
        return [section], {"sec_1": (0, len(full_text))}

    located = _locate_heading_offsets(candidates, full_text, page_offsets)

    sections: list[SectionRecord] = []
    char_bounds: dict[str, tuple[int, int]] = {}
    for i, (title, start_pos) in enumerate(located):
        end_pos = located[i + 1][1] if i + 1 < len(located) else len(full_text)
        end_pos = max(end_pos, start_pos)
        page_start = _page_for_offset(page_offsets, start_pos)
        page_end = _page_for_offset(page_offsets, max(end_pos - 1, start_pos))
        sec_id = f"sec_{i + 1}"
        sections.append(SectionRecord(id=sec_id, title=title, level=1,
                                       page_start=page_start, page_end=page_end))
        char_bounds[sec_id] = (start_pos, end_pos)

    return sections, char_bounds


# ---------------------------------------------------------------------------
# Step 3: chunking (page-mapped, with overlap)
# ---------------------------------------------------------------------------

def _chunk_sections(
    sections: list[SectionRecord],
    char_bounds: dict[str, tuple[int, int]],
    full_text: str,
    page_offsets: list[tuple[int, int, int]],
) -> list[ChunkRecord]:
    chunks: list[ChunkRecord] = []
    chunk_counter = 1

    for section in sections:
        sec_start, sec_end = char_bounds[section.id]
        section_text = full_text[sec_start:sec_end]
        if not section_text.strip():
            continue

        pos = 0
        text_len = len(section_text)
        while pos < text_len:
            end = min(pos + CHUNK_TARGET_CHARS, text_len)
            # try not to cut mid-sentence: extend to next period within a small window
            if end < text_len:
                window_end = min(end + 120, text_len)
                boundary = section_text.rfind(". ", pos, window_end)
                if boundary != -1 and boundary > pos:
                    end = boundary + 1

            chunk_text = section_text[pos:end].strip()
            if chunk_text:
                abs_start = sec_start + pos
                abs_end = sec_start + end
                page_start = _page_for_offset(page_offsets, abs_start)
                page_end = _page_for_offset(page_offsets, max(abs_end - 1, abs_start))
                chunks.append(ChunkRecord(
                    id=f"chunk_{chunk_counter}",
                    section_id=section.id,
                    section_title=section.title,
                    text=chunk_text,
                    page_start=page_start,
                    page_end=page_end,
                    char_start=pos,
                    char_end=end,
                ))
                chunk_counter += 1

            if end >= text_len:
                break
            pos = max(end - CHUNK_OVERLAP_CHARS, pos + 1)

    return chunks


# ---------------------------------------------------------------------------
# Step 4: metadata / title guess
# ---------------------------------------------------------------------------

_PLACEHOLDER_METADATA_VALUES = {"", "(anonymous)", "anonymous", "untitled", "untitled document"}


def _clean_metadata_field(value: str | None) -> str | None:
    """PDF writers (e.g. reportlab) often stamp literal placeholder strings
    like '(anonymous)' into title/author metadata rather than leaving it
    blank. Treat those as absent rather than trusting them."""
    if not value:
        return None
    cleaned = value.strip()
    if cleaned.lower() in _PLACEHOLDER_METADATA_VALUES:
        return None
    return cleaned


def _guess_title(pages: list[dict], pdf_metadata: dict) -> str:
    clean_meta_title = _clean_metadata_field(pdf_metadata.get("title"))
    if clean_meta_title:
        return clean_meta_title
    if not pages:
        return "Untitled Document"
    first_page_spans = pages[0]["spans"]
    if not first_page_spans:
        return "Untitled Document"
    # Title is usually the largest-font text block on page 1.
    largest = max(first_page_spans, key=lambda s: s["size"])
    candidates = [s["text"] for s in first_page_spans if s["size"] >= largest["size"] - 0.5]
    title = " ".join(candidates[:3]).strip()
    return title if title else "Untitled Document"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def process_pdf(pdf_path: str, output_json_path: str | None = None) -> dict:
    """
    Ingest a PDF and produce the structured document JSON used by the rest
    of the pipeline (Member 2 briefing, Member 3 RAG tutor).

    Args:
        pdf_path: path to the source PDF.
        output_json_path: if given, the JSON is also written to this path.

    Returns:
        dict matching DocumentJSON.to_dict() schema.

    Raises:
        FileNotFoundError if pdf_path doesn't exist.
        ValueError if the PDF has no extractable text (e.g. pure scanned
        images) -- caller should route to an OCR fallback in that case.
    """
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    doc = fitz.open(str(path))
    try:
        pdf_metadata = doc.metadata or {}
        pages_raw = _extract_pages_with_layout(doc)

        total_chars = sum(len(p["text"]) for p in pages_raw)
        if total_chars < 20:
            raise ValueError(
                f"No extractable text found in '{pdf_path}'. "
                "This looks like a scanned/image-only PDF and needs OCR "
                "before it can be processed."
            )

        full_text, page_offsets = _build_full_text_with_page_offsets(pages_raw)
        sections, char_bounds = _detect_sections(pages_raw)
        chunks = _chunk_sections(sections, char_bounds, full_text, page_offsets)

        page_records = [
            PageRecord(page_number=p["page_number"], text=p["text"], char_count=len(p["text"]))
            for p in pages_raw
        ]

        result = DocumentJSON(
            metadata={
                "filename": path.name,
                "title": _guess_title(pages_raw, pdf_metadata),
                "author": _clean_metadata_field(pdf_metadata.get("author")),
                "num_pages": len(pages_raw),
                "num_sections": len(sections),
                "num_chunks": len(chunks),
                "total_characters": total_chars,
                "processed_at": datetime.now(timezone.utc).isoformat(),
            },
            pages=page_records,
            sections=sections,
            chunks=chunks,
        ).to_dict()

    finally:
        doc.close()

    if output_json_path:
        Path(output_json_path).write_text(json.dumps(result, indent=2), encoding="utf-8")

    return result


# ---------------------------------------------------------------------------
# CLI entry point (handy for teammates to test locally)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python document_processor.py <path_to_pdf> [output.json]")
        sys.exit(1)

    in_path = sys.argv[1]
    out_path = sys.argv[2] if len(sys.argv) > 2 else None
    data = process_pdf(in_path, out_path)
    print(f"Title:    {data['metadata']['title']}")
    print(f"Pages:    {data['metadata']['num_pages']}")
    print(f"Sections: {data['metadata']['num_sections']}")
    print(f"Chunks:   {data['metadata']['num_chunks']}")
