"""Local stand-ins for the other members' modules.

These are **not** canned mocks. Each one does real, honest work on the actual
uploaded PDF using only local libraries (PyMuPDF + rapidfuzz, both already
required by the dashboard):

* ``process_pdf`` genuinely parses the PDF.
* ``verify_claim`` is a genuine implementation of the Feature 4B fuzzy check —
  ``rapidfuzz.fuzz.partial_ratio`` against the real page text, which is exactly
  what the PRD specifies.
* ``ask_question`` does real extractive retrieval over real page text.
* ``generate_brief`` selects real sentences from the real paper.

The consequence is worth stating plainly, because it is the point: **the
claim → page-jump → highlight path works end to end with none of the other four
modules present and no ``OPENAI_API_KEY``.** Quotes are verbatim from the
document, so they actually locate in the PDF and actually verify. That makes the
dashboard developable and demoable today, and it means the seam being tested is
the real one.

What these are *not*: they are not LLM summarisation, they do not do semantic
retrieval, and they do not fetch citation metadata. A brief produced here is
extractive, not abstractive. The UI says so — see ``fallback_notice`` — because
passing local heuristics off as verified AI output would be exactly the
dishonesty PaperLens exists to prevent.

No module here imports Streamlit; caching and error isolation are applied one
layer up in ``adapters.py``.
"""

from __future__ import annotations

import re
from typing import Any

# ─── Shared text helpers ───────────────────────────────────────────────────

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z(])")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9\-']+")

#: Words too common to be worth matching on in the keyword retriever.
_STOPWORDS = frozenset(
    """a an and are as at be but by for from has have how in into is it its of on or
    that the their there these this to was were what when where which who why with
    we our they do does can could would should paper method approach using used""".split()
)


#: Front-matter and licence text that is technically prose but never a claim
#: about the research. Without this filter the first "contribution" a demo
#: audience sees is a Google copyright notice.
_BOILERPLATE_CUES = (
    "grants permission", "copyright", "all rights reserved", "creative commons",
    "arxiv:", "preprint", "under review", "conference on neural information",
    "equal contribution", "corresponding author", "@", "http://", "https://",
    # Author-contribution footnotes: prose about who did what, not about the work.
    "was responsible for", "experimented with", "designed and implemented",
    "has been crucially involved", "listed order is random",
)


def _is_front_matter(text: str) -> bool:
    """Cue-only check, safe to run on a candidate *title*.

    Deliberately excludes the shape heuristics in :func:`_is_boilerplate`: paper
    titles are legitimately title-cased, so the "mostly capitalised words means
    author list" rule would reject exactly the string we are looking for.
    """
    lowered = text.lower()
    return any(cue in lowered for cue in _BOILERPLATE_CUES)


def _is_boilerplate(sentence: str) -> bool:
    """True for licence notices, author blocks, and other front matter."""
    if _is_front_matter(sentence):
        return True

    letters = sum(c.isalpha() or c.isspace() for c in sentence)
    if letters / max(1, len(sentence)) < 0.80:
        # Dense in digits, symbols or daggers: a table row, an equation, or an
        # affiliation block rather than a sentence.
        return True

    # Author lists are mostly Capitalised Tokens with almost no lowercase words.
    words = sentence.split()
    if len(words) >= 5:
        capitalised = sum(1 for w in words if w[:1].isupper())
        if capitalised / len(words) > 0.65:
            return True

    return False


def _sentences(text: str) -> list[str]:
    """Split page text into candidate sentences, dropping the unusable ones."""
    flattened = re.sub(r"\s+", " ", text).strip()
    out: list[str] = []
    for sentence in _SENTENCE_SPLIT.split(flattened):
        sentence = sentence.strip()
        # Too short to be a claim; too long to highlight cleanly on one page.
        if not 60 <= len(sentence) <= 400:
            continue
        if _is_boilerplate(sentence):
            continue
        out.append(sentence)
    return out


def _keywords(text: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(text) if w.lower() not in _STOPWORDS and len(w) > 2}


def fallback_notice(missing: list[str]) -> str:
    """One-line, non-euphemistic description of what is running locally."""
    if not missing:
        return ""
    return (
        f"Running local fallbacks for: {', '.join(missing)}. "
        "Summaries are extractive (real sentences pulled from the PDF), not LLM-generated, "
        "and citation metadata is not fetched. Verification badges are real "
        "rapidfuzz matches against the page text."
    )


# ─── Member 1 — parser.pdf_parser.process_pdf ──────────────────────────────

_HEADING = re.compile(
    r"^\s*(?:(\d+(?:\.\d+)*)\s+)?"
    r"(abstract|introduction|related work|background|method(?:s|ology)?|approach|model"
    r"|architecture|experiments?|setup|results?|evaluation|discussion|analysis"
    r"|limitations?|conclusions?|future work|references|acknowledge?ments?)\s*$",
    re.IGNORECASE,
)


def process_pdf(pdf_path: str) -> dict[str, Any]:
    """Parse a PDF into the shape the dashboard and verifier need.

    Returns ``full_text_by_page`` keyed by 1-based page number — the structure
    ``verify_claim`` consumes — plus a detected section outline.
    """
    import fitz  # PyMuPDF

    full_text_by_page: dict[int, str] = {}
    sections: list[dict[str, Any]] = []

    with fitz.open(pdf_path) as doc:
        title = (doc.metadata or {}).get("title") or ""
        for page_index, page in enumerate(doc, start=1):
            text = page.get_text("text") or ""
            full_text_by_page[page_index] = text
            for line in text.splitlines():
                match = _HEADING.match(line)
                if match:
                    name = match.group(2).strip()
                    sections.append({"section": name.title(), "page": page_index})

        page_count = doc.page_count

        # Fall back to the largest text on page 1, which is almost always the
        # title in an academic paper.
        if not title.strip() and page_count:
            title = _title_from_first_page(doc[0])

    # De-duplicate sections, keeping the first occurrence of each.
    seen: set[str] = set()
    unique_sections = []
    for section in sections:
        key = section["section"].lower()
        if key not in seen:
            seen.add(key)
            # Carry the page's text on the section entry. The citation extractor
            # reads section text to find the reference list, so a section entry
            # with only a name and a page number is useless to it.
            unique_sections.append(
                {**section, "text": full_text_by_page.get(section["page"], "")}
            )

    return {
        "title": (title or "Untitled paper").strip(),
        "page_count": page_count,
        "full_text_by_page": full_text_by_page,
        "sections": unique_sections,
        # The citation extractor's preferred input ("Shape B"): the whole
        # reference block as one string. Supplying it matters — a reference list
        # spans several pages, so handing over only the page the heading sits on
        # truncates it badly.
        "references_text": _references_text(full_text_by_page),
    }


def _references_text(full_text_by_page: dict[int, str]) -> str:
    """Everything from the 'References' heading to the end of the document."""
    heading = re.compile(r"^\s*(references|bibliography|works cited)\s*$",
                         re.IGNORECASE | re.MULTILINE)

    ordered = sorted(full_text_by_page)
    for page_no in ordered:
        match = heading.search(full_text_by_page[page_no] or "")
        if not match:
            continue
        # Start just after the heading, then take every following page whole.
        parts = [full_text_by_page[page_no][match.end():]]
        parts.extend(full_text_by_page[p] or "" for p in ordered if p > page_no)
        return "\n".join(parts)
    return ""


def _title_from_first_page(page: Any) -> str:
    """Largest-font *horizontal* run of text on page 1.

    The direction check is not optional. Preprints carry a rotated identifier
    stamp down the left margin ("arXiv:1706.03762v7 [cs.CL] 2 Aug 2023") set
    larger than the actual title, so a naive largest-font heuristic picks the
    stamp every time. Horizontal lines report ``dir == (1, 0)``; the stamp
    reports ``(0, -1)``.
    """
    try:
        blocks = page.get_text("dict")["blocks"]
    except Exception:
        return ""

    best_size, best_text = 0.0, ""
    for block in blocks:
        for line in block.get("lines", []):
            direction = line.get("dir") or (1.0, 0.0)
            if abs(direction[0]) < 0.99:      # not left-to-right: rotated text
                continue
            for span in line.get("spans", []):
                size = span.get("size", 0)
                text = span.get("text", "").strip()
                if size > best_size and len(text) > 12 and not _is_front_matter(text):
                    best_size, best_text = size, text
    return best_text


# ─── Member 3 — verification.verifier.verify_claim (Feature 4B) ────────────


class VerifiedClaim:
    """Mirrors the shape described in the integration notes: ``.status`` + ``.match_score``."""

    __slots__ = ("status", "match_score", "page", "quote", "matched_text")

    def __init__(self, status: str, match_score: float, page: int | None,
                 quote: str, matched_text: str = "") -> None:
        self.status = status
        self.match_score = match_score
        self.page = page
        self.quote = quote
        self.matched_text = matched_text

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"VerifiedClaim(status={self.status!r}, match_score={self.match_score:.3f}, page={self.page})"


def verify_claim(quote: str, page: int | None, full_text_by_page: dict[int, str]) -> VerifiedClaim:
    """A faithful local implementation of the Feature 4B check.

    Scores ``quote`` against the claimed page with
    ``rapidfuzz.fuzz.partial_ratio``, then against pages ±1 — the PRD
    anticipates off-by-one page attribution and asks for adjacent pages to be
    tried. The best-scoring page wins and is reported back, so a claim whose
    page number was off by one is corrected rather than marked unsupported.
    """
    from rapidfuzz import fuzz

    if not quote or not quote.strip():
        return VerifiedClaim("unverified", 0.0, page, quote)
    if not full_text_by_page:
        return VerifiedClaim("unverified", 0.0, page, quote)

    needle = re.sub(r"\s+", " ", quote).strip()

    candidates: list[int]
    if page is None:
        candidates = sorted(full_text_by_page)
    else:
        candidates = [p for p in (page, page - 1, page + 1) if p in full_text_by_page]
        if not candidates:
            candidates = sorted(full_text_by_page)

    best_score, best_page = 0.0, page
    for candidate in candidates:
        haystack = re.sub(r"\s+", " ", full_text_by_page.get(candidate, ""))
        if not haystack:
            continue
        score = fuzz.partial_ratio(needle, haystack) / 100.0
        if score > best_score:
            best_score, best_page = score, candidate

    # Thresholds per PRD Feature 4B.
    if best_score >= 0.90:
        status = "verified"
    elif best_score >= 0.60:
        status = "paraphrased"
    else:
        status = "unsupported"

    return VerifiedClaim(status, best_score, best_page, quote)


def verify_claims_batch(claims: list[dict[str, Any]], full_text_by_page: dict[int, str]) -> list[VerifiedClaim]:
    """Bulk form of :func:`verify_claim`."""
    return [
        verify_claim(claim.get("quote", ""), claim.get("page"), full_text_by_page)
        for claim in claims
    ]


# ─── Member 2 — ai.summarizer.generate_brief ───────────────────────────────

#: Cue words that place a sentence under a PRD Feature 3 section. Ordered by
#: specificity — the first section whose cues hit wins.
_SECTION_CUES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Main contribution", ("we propose", "we present", "we introduce", "contribution",
                           "in this paper", "we show that", "novel")),
    ("Results", ("results", "outperform", "state-of-the-art", "accuracy", "bleu",
                 "f1", "achieves", "improves", "baseline", "%")),
    ("Methodology", ("we train", "architecture", "we use", "consists of", "our model",
                     "algorithm", "layer", "encoder", "decoder", "objective", "loss")),
    ("Limitations", ("limitation", "however", "future work", "does not", "fails",
                     "cannot", "leave", "remains")),
    ("Prerequisites", ("assume", "background", "prior work", "builds on", "based on")),
)

_MAX_PER_SECTION = 2


def generate_brief(document: Any) -> dict[str, list[dict[str, Any]]]:
    """Build an *extractive* brief: real sentences, real pages, real quotes.

    Because each claim's quote is lifted verbatim from the page it is attributed
    to, every claim here verifies at ~1.0 and highlights precisely. That is what
    makes the demo spine testable without an LLM — but it also means this brief
    reads as excerpts rather than as summary prose. Member 2's real
    ``generate_brief`` replaces it with abstractive claims whose quotes are
    separate supporting evidence.
    """
    pages = _pages_of(document)
    if not pages:
        return {}

    brief: dict[str, list[dict[str, Any]]] = {}
    used: set[str] = set()

    # Front matter carries contribution framing; skip the reference list at the end.
    considered = [p for p in sorted(pages) if p <= max(1, int(len(pages) * 0.75))]

    for section, cues in _SECTION_CUES:
        picks: list[dict[str, Any]] = []
        for page_no in considered:
            for sentence in _sentences(pages[page_no]):
                lowered = sentence.lower()
                if sentence in used or not any(cue in lowered for cue in cues):
                    continue
                picks.append({"text": sentence, "quote": sentence, "page": page_no})
                used.add(sentence)
                if len(picks) >= _MAX_PER_SECTION:
                    break
            if len(picks) >= _MAX_PER_SECTION:
                break
        if picks:
            brief[section] = picks

    # If cue matching found nothing (an unusual paper, or a non-English one),
    # still produce something rather than an empty dashboard.
    if not brief:
        first_page = considered[0] if considered else sorted(pages)[0]
        for sentence in _sentences(pages[first_page])[:2]:
            brief.setdefault("Main contribution", []).append(
                {"text": sentence, "quote": sentence, "page": first_page}
            )

    return brief


def _pages_of(document: Any) -> dict[int, str]:
    """Pull ``full_text_by_page`` off a Document dataclass or a raw dict."""
    pages = getattr(document, "full_text_by_page", None)
    if isinstance(pages, dict) and pages:
        return pages
    if isinstance(document, dict):
        for key in ("full_text_by_page", "text_by_page", "pages_text"):
            value = document.get(key)
            if isinstance(value, dict) and value:
                return {int(k): str(v) for k, v in value.items()}
    raw = getattr(document, "raw", None)
    if isinstance(raw, dict):
        return _pages_of(raw)
    return {}


# ─── Member 3 — rag.retriever.build_index / ask_question ───────────────────


class KeywordIndex:
    """A tiny inverted index — the local stand-in for FAISS + embeddings.

    Lexical, not semantic: it will miss paraphrased questions that a real
    embedding retriever would catch. Good enough to exercise the chat UI, the
    badge path and the honest "no evidence" state.
    """

    __slots__ = ("pages", "page_keywords")

    def __init__(self, pages: dict[int, str]) -> None:
        self.pages = pages
        self.page_keywords = {page: _keywords(text) for page, text in pages.items()}

    def best_pages(self, question: str, limit: int = 3) -> list[tuple[int, float]]:
        terms = _keywords(question)
        if not terms:
            return []
        scored = []
        for page, keywords in self.page_keywords.items():
            overlap = len(terms & keywords)
            if overlap:
                scored.append((page, overlap / len(terms)))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:limit]


def build_index(document: Any) -> KeywordIndex:
    """Build the retrieval index. Cached by ``adapters`` via ``st.cache_resource``."""
    return KeywordIndex(_pages_of(document))


#: Below this share of question terms found on a page, we decline to answer.
#: The PRD's demo script deliberately asks an unanswerable question, so this
#: threshold has to actually bite.
_NO_EVIDENCE_FLOOR = 0.34


def ask_question(question: str, context: Any = None) -> dict[str, Any]:
    """Answer extractively from the paper, or honestly decline.

    Accepts either a ``KeywordIndex`` or a document as ``context`` so it works
    whichever way the caller wires it up — the two briefing documents disagree
    on that, and ``adapters`` resolves it by signature inspection.
    """
    index = context if isinstance(context, KeywordIndex) else build_index(context)

    ranked = index.best_pages(question)
    if not ranked or ranked[0][1] < _NO_EVIDENCE_FLOOR:
        return {
            "answer": "The uploaded paper does not provide enough evidence to answer this.",
            "quote": "",
            "page": None,
            "match_score": None,
            "status": "unsupported",
            "confidence": "Low",
            "no_evidence": True,
        }

    page, coverage = ranked[0]
    terms = _keywords(question)

    # Pick the sentence on that page with the greatest overlap with the question.
    best_sentence, best_overlap = "", 0
    for sentence in _sentences(index.pages[page]):
        overlap = len(terms & _keywords(sentence))
        if overlap > best_overlap:
            best_sentence, best_overlap = sentence, overlap

    if not best_sentence:
        return {
            "answer": "The uploaded paper does not provide enough evidence to answer this.",
            "quote": "", "page": None, "match_score": None,
            "status": "unsupported", "confidence": "Low", "no_evidence": True,
        }

    # The quote is lifted verbatim, so it verifies and highlights for real.
    verified = verify_claim(best_sentence, page, index.pages)
    return {
        "answer": best_sentence,
        "quote": best_sentence,
        "page": verified.page,
        "match_score": verified.match_score,
        "status": verified.status,
        "confidence": "High" if coverage >= 0.6 else "Medium",
        "no_evidence": False,
    }


# ─── Member 4 — citations.extractor.analyze_references ─────────────────────

#: Start of a numbered reference entry: "[12] " or "12. ".
_REF_START = re.compile(r"^\s*(?:\[(\d{1,3})\]|(\d{1,3})\.)\s+(.*)$")
_YEAR = re.compile(r"\b(19|20)\d{2}\b")
_MAX_REFERENCES = 60


def analyze_references(document: Any) -> list[dict[str, Any]]:
    """Extract the reference list from the tail of the paper.

    Entries are accumulated from their ``[n]`` marker up to the next marker,
    because reference entries wrap across two or three lines and the year — the
    thing that distinguishes a real citation from an ordinary numbered list —
    usually lands on a continuation line, not the first one.

    Local and offline: no Semantic Scholar lookup, so citation counts, abstracts
    and "why was this cited" are absent. The citations panel renders that absence
    explicitly rather than inventing values — which also keeps the PRD's
    API-failure fallback path exercised during normal development.
    """
    pages = _pages_of(document)
    if not pages:
        return []

    ordered = sorted(pages)
    # References live at the end; find the heading, else take the last quarter.
    start = ordered[max(0, int(len(ordered) * 0.75))]
    for page_no in ordered:
        if re.search(r"^\s*references\s*$", pages[page_no], re.IGNORECASE | re.MULTILINE):
            start = page_no
            break

    # Flatten the tail into lines, dropping everything above the heading.
    lines: list[str] = []
    for page_no in [p for p in ordered if p >= start]:
        page_lines = pages[page_no].splitlines()
        if page_no == start:
            for i, line in enumerate(page_lines):
                if re.match(r"^\s*references\s*$", line, re.IGNORECASE):
                    page_lines = page_lines[i + 1 :]
                    break
        lines.extend(page_lines)

    # Group into entries by marker.
    entries: list[tuple[int, str]] = []
    current_number: int | None = None
    current_parts: list[str] = []

    def flush() -> None:
        if current_number is not None and current_parts:
            entries.append((current_number, " ".join(current_parts)))

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        match = _REF_START.match(line)
        if match:
            flush()
            current_number = int(match.group(1) or match.group(2))
            current_parts = [match.group(3).strip()]
        elif current_number is not None:
            current_parts.append(line)
    flush()

    references: list[dict[str, Any]] = []
    for _number, body in entries:
        body = re.sub(r"\s+", " ", body).strip()
        year_match = _YEAR.search(body)
        # A year is what separates a citation from an ordinary numbered list.
        if not year_match or len(body) < 25:
            continue

        authors, _, remainder = body.partition(". ")
        title = (remainder or body).strip(" .")
        # Trim the venue/identifier tail so the card shows a title, not a whole entry.
        title = re.split(r"\s+(?:In |CoRR|arXiv preprint|arXiv:|In:)", title)[0].strip(" .,")

        references.append(
            {
                "title": title[:180] or body[:180],
                "authors": authors[:120] if remainder else "",
                "year": year_match.group(0),
                "citation_count": None,
                "abstract": "",
                "purpose": "",
                "from_cache": False,
            }
        )
        if len(references) >= _MAX_REFERENCES:
            break

    return references


# ─── Member 4 — reviewer.reviewer (Reviewer Mode, PRD Feature 8) ───────────

#: Reproducibility signals the PRD's own mock-up checks for. Each is a genuine
#: keyword probe over the paper text, so the score is derived, not invented.
_REPRODUCIBILITY_SIGNALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Dataset", ("dataset", "corpus", "benchmark", "training data")),
    ("Code", ("github.com", "code is available", "open-source", "implementation is available")),
    ("Random seed", ("random seed", "seeds", "seed value")),
    ("Hardware", ("gpu", "tpu", "v100", "a100", "cpu hours", "nvidia")),
    ("Statistical significance", ("statistical significance", "p-value", "p <",
                                  "confidence interval", "standard deviation", "std")),
    ("Hyperparameters", ("learning rate", "batch size", "optimizer", "adam", "dropout")),
)


def review(document: Any) -> dict[str, Any]:
    """Heuristic Reviewer Mode: keyword-probe reproducibility signals."""
    pages = _pages_of(document)
    if not pages:
        return {}

    corpus = " ".join(pages[p] for p in sorted(pages)).lower()

    checks = []
    for name, cues in _REPRODUCIBILITY_SIGNALS:
        present = any(cue in corpus for cue in cues)
        checks.append({"name": name, "present": present})

    hits = sum(1 for c in checks if c["present"])
    score = round(10.0 * hits / len(checks), 1)

    weaknesses = []
    for check in checks:
        if not check["present"]:
            weaknesses.append(
                {
                    "kind": "weakness",
                    "text": f"No mention of {check['name'].lower()} found in the text.",
                }
            )

    return {
        "reproducibility_score": score,
        "checks": checks,
        "findings": weaknesses,
        "consistency": [],  # needs cross-section LLM comparison; not done locally
    }
