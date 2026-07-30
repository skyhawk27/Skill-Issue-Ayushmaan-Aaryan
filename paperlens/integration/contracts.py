"""UI-facing data model, and tolerant normalisers that produce it.

Why this exists
---------------
Five people are building PaperLens in parallel against a contract that is still
settling. Two of the briefing documents already disagree about it (see
``CONTRACT.md``). If the dashboard read teammate dicts directly, then somebody
renaming ``match_score`` to ``score`` at hour 18 would blank the summary panel
during the demo.

So nothing in ``ui/`` ever touches a raw teammate payload. Everything arrives as
one of the frozen dataclasses below, built by a normaliser that:

* accepts several plausible key spellings for the same field,
* accepts objects *or* dicts (Member 3 returns a ``VerifiedClaim`` object, while
  fixtures and JSON round-trips give dicts),
* never raises on a missing or malformed field — it degrades to a clearly
  labelled "not verified" state, which the PRD explicitly prefers over hiding.

The rule of thumb: be liberal about what we accept, strict about what ``ui/``
sees.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from ui.theme import Status, status_for_score

_WHITESPACE = re.compile(r"\s+")

# ─── Field aliases ─────────────────────────────────────────────────────────
# Each tuple is tried in order. Keep the name the PRD/contract uses first, then
# the variants seen in the briefing docs and the ones people naturally write.

_TEXT_KEYS = ("text", "claim", "statement", "content", "body", "summary")
_QUOTE_KEYS = ("quote", "supporting_quote", "evidence_quote", "candidate_quote", "span")
_PAGE_KEYS = ("page", "claimed_page", "page_number", "page_no", "pageno")
_SCORE_KEYS = ("match_score", "score", "similarity", "confidence_score")
_STATUS_KEYS = ("status", "verification", "verification_status", "badge")
_SECTION_KEYS = ("section", "heading", "title", "name", "label")
_ANSWER_KEYS = ("answer", "response", "text", "content")
_CONFIDENCE_KEYS = ("confidence", "confidence_label")


def _get(obj: Any, keys: Sequence[str], default: Any = None) -> Any:
    """Read the first present key/attribute from a dict or an object."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        for key in keys:
            if key in obj and obj[key] is not None:
                return obj[key]
        return default
    for key in keys:
        value = getattr(obj, key, None)
        if value is not None:
            return value
    return default


def _as_float(value: Any) -> float | None:
    """Coerce a score to a 0-1 float, tolerating ``"0.94"`` and ``94``."""
    if value is None or isinstance(value, bool):
        return None
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    # Some modules report percentages; rapidfuzz's partial_ratio is 0-100 while
    # the PRD's worked examples are 0-1. Normalise on the way in.
    if score > 1.0:
        score = score / 100.0
    return max(0.0, min(1.0, score))


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip() or default


# ─── The model ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Evidence:
    """A quote, the page it is claimed to be on, and how well it checked out."""

    quote: str = ""
    page: int | None = None
    score: float | None = None
    status: Status = "unverified"

    @property
    def has_quote(self) -> bool:
        return bool(self.quote.strip())

    @property
    def is_navigable(self) -> bool:
        """Can this drive the PDF pane? Needs a page; a quote is optional.

        Without a quote we can still jump to the page — we just cannot highlight
        a span, so the viewer shows the page with no overlay.
        """
        return self.page is not None and self.page >= 1

    @property
    def score_text(self) -> str:
        return "—" if self.score is None else f"{self.score:.2f}"


@dataclass(frozen=True)
class Claim:
    """One assertion the AI made about the paper, with its evidence.

    ``claim_id`` is stable across reruns so the PDF pane and the summary panel
    can agree on which claim is currently selected.
    """

    claim_id: str
    section: str
    text: str
    evidence: Evidence = field(default_factory=Evidence)

    @property
    def status(self) -> Status:
        return self.evidence.status


@dataclass(frozen=True)
class ChatTurn:
    """A question and its grounded, verified answer."""

    question: str
    answer: str
    evidence: Evidence = field(default_factory=Evidence)
    confidence: str = ""
    #: True when the module honestly reported it could not find support. The
    #: PRD's demo script depends on this state looking deliberate rather than
    #: like a failure, so it is modelled explicitly instead of inferred from an
    #: empty answer.
    no_evidence: bool = False


@dataclass(frozen=True)
class Reference:
    """One entry from the paper's reference list, plus fetched metadata."""

    title: str
    authors: str = ""
    year: str = ""
    citation_count: int | None = None
    abstract: str = ""
    purpose: str = ""     # "why was this paper cited?"
    url: str = ""
    #: Set when metadata came from the local cache rather than a live lookup, so
    #: the UI can be honest about it (PRD reliability note on Semantic Scholar).
    from_cache: bool = False


@dataclass(frozen=True)
class ReviewerFinding:
    """A strength, weakness, gap, or consistency flag from Reviewer Mode."""

    kind: str          # "strength" | "weakness" | "missing" | "consistency"
    text: str
    evidence: Evidence = field(default_factory=Evidence)


@dataclass(frozen=True)
class Brief:
    """The structured summary: PRD Feature 3's sections, as verified claims."""

    claims: tuple[Claim, ...] = ()

    def by_section(self) -> dict[str, list[Claim]]:
        """Group claims under their section heading, preserving first-seen order."""
        grouped: dict[str, list[Claim]] = {}
        for claim in self.claims:
            grouped.setdefault(claim.section, []).append(claim)
        return grouped

    def tally(self) -> dict[Status, int]:
        """Count claims per status — drives the nav rail's at-a-glance tally."""
        counts: dict[Status, int] = {
            "verified": 0,
            "paraphrased": 0,
            "unsupported": 0,
            "unverified": 0,
        }
        for claim in self.claims:
            counts[claim.status] = counts.get(claim.status, 0) + 1
        return counts


@dataclass(frozen=True)
class Document:
    """The parsed paper, as the dashboard needs it.

    Two fields are surfaced as first-class rather than left inside an opaque blob,
    because downstream modules take them as *direct arguments*:

    * ``full_text_by_page`` — what ``verify_claim(quote, page, full_text_by_page)``
      consumes.
    * ``chunks`` — what ``build_index(chunks, full_text_by_page)`` consumes. Note
      that the real ``build_index`` takes chunks, **not** a document, so the
      adapter has to be able to hand them over separately.

    ``raw`` keeps the original payload so nothing is lost in translation.
    """

    title: str = "Untitled paper"
    page_count: int = 0
    full_text_by_page: dict[int, str] = field(default_factory=dict)
    sections: tuple[str, ...] = ()
    chunks: tuple[dict[str, Any], ...] = ()
    raw: Any = None


# ─── Normalisers ───────────────────────────────────────────────────────────


def to_evidence(payload: Any) -> Evidence:
    """Build ``Evidence`` from a claim dict, a ``VerifiedClaim``, or a mixture.

    Status resolution order matters. An explicit status from the verification
    module wins, because Member 3's pipeline may downgrade a claim for reasons a
    bare score does not capture (the PRD has it re-attempt retrieval once and
    then "downgrade confidence and label clearly"). Only if no explicit status
    is present do we derive one from the score.
    """
    if payload is None:
        return Evidence()

    quote = _as_str(_get(payload, _QUOTE_KEYS))
    page = _as_int(_get(payload, _PAGE_KEYS))
    score = _as_float(_get(payload, _SCORE_KEYS))

    raw_status = _get(payload, _STATUS_KEYS)
    if raw_status is not None:
        status = _normalise_status(raw_status, score)
    else:
        status = status_for_score(score)

    return Evidence(quote=quote, page=page, score=score, status=status)


#: Spellings of each status seen across the briefing docs, plus the obvious
#: synonyms and the emoji the PRD uses in its mock-ups.
_STATUS_SYNONYMS: dict[str, Status] = {
    "verified": "verified", "verify": "verified", "confirmed": "verified",
    "exact": "verified", "match": "verified", "pass": "verified",
    "true": "verified", "✅": "verified",
    "paraphrased": "paraphrased", "paraphrase": "paraphrased",
    "partial": "paraphrased", "weak": "paraphrased", "similar": "paraphrased",
    "⚠": "paraphrased", "⚠️": "paraphrased",
    "unsupported": "unsupported", "unverified_claim": "unsupported",
    "not_found": "unsupported", "missing": "unsupported", "fail": "unsupported",
    "failed": "unsupported", "false": "unsupported", "hallucinated": "unsupported",
    "❌": "unsupported",
    "unverified": "unverified", "unknown": "unverified", "none": "unverified",
    "skipped": "unverified", "n/a": "unverified",
}


def _normalise_status(raw: Any, score: float | None) -> Status:
    """Map a teammate's status value onto our four bands."""
    if isinstance(raw, bool):
        return "verified" if raw else "unsupported"

    # An enum-like object: prefer .value, then .name, then str().
    for attr in ("value", "name"):
        candidate = getattr(raw, attr, None)
        if isinstance(candidate, str):
            raw = candidate
            break

    key = str(raw).strip().lower().replace("-", "_").replace(" ", "_")
    if key in _STATUS_SYNONYMS:
        return _STATUS_SYNONYMS[key]
    # Unrecognised label: fall back to the score rather than inventing a status.
    return status_for_score(score)


def to_claim(payload: Any, *, section: str = "", index: int = 0) -> Claim:
    """Build one ``Claim``. ``section``/``index`` seed a stable id."""
    text = _as_str(_get(payload, _TEXT_KEYS))
    resolved_section = _as_str(_get(payload, _SECTION_KEYS), default=section) or section
    evidence = to_evidence(payload)

    # Some modules nest evidence one level down instead of flattening it.
    if not evidence.has_quote and evidence.page is None:
        nested = _get(payload, ("evidence", "grounding", "support", "citation"))
        if nested is not None:
            evidence = to_evidence(nested)

    slug = (resolved_section or "claim").lower().replace(" ", "-")[:24]
    return Claim(
        claim_id=f"{slug}-{index}",
        section=resolved_section or "Summary",
        text=text,
        evidence=evidence,
    )


#: Section order from PRD Feature 3. Anything a teammate returns that is not in
#: this list still renders — it just sorts after the known sections.
CANONICAL_SECTIONS = (
    "Main contribution",
    "Methodology",
    "Results",
    "Limitations",
    "Prerequisites",
)


def to_brief(payload: Any) -> Brief:
    """Build a ``Brief`` from whatever shape ``generate_brief`` returned.

    Four shapes are accepted, because all are reasonable readings of the contract
    and it is cheaper to support them all than to negotiate:

    * ``{"Methodology": [claim, ...], ...}``   — section-keyed lists
    * ``{"Methodology": claim, ...}``          — one claim per section
    * ``[claim, ...]`` / ``{"claims": [...]}`` — a flat list carrying its own
      ``section`` field
    * a ``StructuredSummary``-style **dataclass** with one attribute per PRD
      section (``contributions``, ``methodology``, …). This one matters: the
      shared ``utils.models.StructuredSummary`` is a dataclass, not a dict, and
      without an explicit branch it is neither a dict nor iterable, so it would
      fall through and produce a silently empty summary.
    """
    if payload is None:
        return Brief()

    structured = _structured_summary_sections(payload)
    if structured is not None:
        payload = structured

    # Unwrap a container object/dict around the actual sections.
    for wrapper in ("brief", "summary", "sections", "claims"):
        inner = _get(payload, (wrapper,))
        if inner is not None and not isinstance(payload, (list, tuple, dict)):
            payload = inner
            break

    claims: list[Claim] = []

    if isinstance(payload, dict):
        for section, value in payload.items():
            section_name = _prettify_section(section)
            if isinstance(value, (list, tuple)):
                for i, item in enumerate(value):
                    claims.append(to_claim(item, section=section_name, index=len(claims)))
            elif isinstance(value, str):
                # A bare string section body: a claim with no evidence attached.
                claims.append(
                    Claim(
                        claim_id=f"{section_name.lower().replace(' ', '-')[:24]}-{len(claims)}",
                        section=section_name,
                        text=value.strip(),
                    )
                )
            elif value is not None:
                claims.append(to_claim(value, section=section_name, index=len(claims)))
    elif isinstance(payload, Iterable):
        for item in payload:
            claims.append(to_claim(item, section="Summary", index=len(claims)))

    claims.sort(key=lambda c: _section_rank(c.section))
    return Brief(claims=tuple(claims))


#: Attribute name on a StructuredSummary-style object → display section name.
#: Keys follow the shared ``utils.models.StructuredSummary`` field names; values
#: follow the PRD Feature 3 headings.
_STRUCTURED_FIELDS: tuple[tuple[str, str], ...] = (
    ("contributions", "Main contribution"),
    ("contribution", "Main contribution"),
    ("main_contribution", "Main contribution"),
    ("methodology", "Methodology"),
    ("results", "Results"),
    ("limitations", "Limitations"),
    ("prerequisites", "Prerequisites"),
)


def _structured_summary_sections(payload: Any) -> dict[str, Any] | None:
    """Convert a ``StructuredSummary``-style object into a section-keyed dict.

    Returns ``None`` when ``payload`` is not that shape, so callers can fall
    through to the dict/list handling.
    """
    if isinstance(payload, (dict, list, tuple, str, bytes)):
        return None

    sections: dict[str, Any] = {}
    for attr, display in _STRUCTURED_FIELDS:
        value = getattr(payload, attr, None)
        if value:
            sections.setdefault(display, value)

    return sections or None


def _prettify_section(raw: Any) -> str:
    """``"main_contribution"`` → ``"Main contribution"`` (sentence case)."""
    text = _as_str(raw, "Summary").replace("_", " ").replace("-", " ").strip()
    if not text:
        return "Summary"
    for canonical in CANONICAL_SECTIONS:
        if text.lower() == canonical.lower():
            return canonical
    return text[0].upper() + text[1:]


def _section_rank(section: str) -> int:
    lowered = section.lower()
    for i, canonical in enumerate(CANONICAL_SECTIONS):
        if lowered == canonical.lower():
            return i
    return len(CANONICAL_SECTIONS)


def to_chat_turn(question: str, payload: Any) -> ChatTurn:
    """Build a ``ChatTurn`` from ``ask_question``'s return value.

    Member 3's note says the returned dict "has everything you need for
    rendering badges and PDF navigation", so we read answer, evidence and
    confidence out of one payload.
    """
    if payload is None:
        return ChatTurn(
            question=question,
            answer="",
            no_evidence=True,
        )

    if isinstance(payload, str):
        return ChatTurn(question=question, answer=payload.strip())

    answer = _as_str(_get(payload, _ANSWER_KEYS))
    evidence = to_evidence(payload)
    if not evidence.has_quote and evidence.page is None:
        nested = _get(payload, ("evidence", "grounding", "support", "citation"))
        if nested is not None:
            evidence = to_evidence(nested)

    explicit_flag = _get(payload, ("no_evidence", "insufficient_evidence", "not_found"))
    no_evidence = bool(explicit_flag) if explicit_flag is not None else (
        not answer or (not evidence.has_quote and evidence.page is None)
    )

    return ChatTurn(
        question=question,
        answer=answer,
        evidence=evidence,
        confidence=_as_str(_get(payload, _CONFIDENCE_KEYS)),
        no_evidence=no_evidence,
    )


def _trim_reference_text(text: str) -> str:
    """Pull a readable title out of a full reference string.

    Reference entries read "Authors. Title. Venue, Year." — so the clause after
    the first sentence-ending period is usually the title. Falls back to a plain
    truncation when that shape does not hold.
    """
    text = _WHITESPACE.sub(" ", text).strip()
    if not text:
        return "Untitled reference"

    _authors, sep, remainder = text.partition(". ")
    candidate = (remainder or text) if sep else text
    # Drop the venue / identifier tail.
    candidate = re.split(
        r"\s+(?:In |In:|CoRR|arXiv preprint|arXiv:|Proceedings|Advances in)",
        candidate,
    )[0].strip(" .,")

    if not candidate:
        candidate = text
    return candidate if len(candidate) <= 140 else candidate[:139].rstrip() + "…"


def _unwrap_result_and_stats(payload: Any) -> Any:
    """Unwrap a ``(results, stats)`` two-tuple to just ``results``.

    A common shape for pipelines that report telemetry alongside their output —
    ``explore_citations`` returns exactly this. Deliberately narrow: only a
    2-tuple whose first element is a sequence and whose second is a mapping, so a
    genuine 2-item list of references is never mistaken for one.
    """
    if (
        isinstance(payload, tuple)
        and len(payload) == 2
        and isinstance(payload[0], (list, tuple))
        and isinstance(payload[1], dict)
    ):
        return payload[0]
    return payload


def to_references(payload: Any) -> tuple[Reference, ...]:
    """Build the reference list from ``analyze_references``' return value."""
    if payload is None:
        return ()

    # ``explore_citations`` returns ``(list[EnrichedCitation], stats_dict)``.
    # Without this, the tuple itself is iterated and yields two junk entries —
    # one from the list, one from the stats dict.
    payload = _unwrap_result_and_stats(payload)

    if isinstance(payload, dict):
        for wrapper in ("references", "citations", "papers", "items"):
            inner = payload.get(wrapper)
            if inner is not None:
                payload = inner
                break
        else:
            payload = list(payload.values())

    if not isinstance(payload, Iterable) or isinstance(payload, (str, bytes)):
        return ()

    references: list[Reference] = []
    for item in payload:
        if isinstance(item, str):
            references.append(Reference(title=item.strip()))
            continue
        references.append(_to_reference(item))
    return tuple(references)


def _to_reference(item: Any) -> Reference:
    """Build one ``Reference``, flattening nested metadata if present.

    Handles two shapes at once. A flat dict works, and so does the shipped
    ``EnrichedCitation``, which nests the interesting fields one level down::

        EnrichedCitation(ref_id, raw_text, metadata=PaperMetadata(...),
                         purpose=CitationPurpose(purpose=..., relationship=...),
                         resolved=bool)

    Nested values win where both exist, since a resolved Semantic Scholar title
    is better than the raw reference string it was parsed from.
    """
    meta = _get(item, ("metadata", "paper", "paper_metadata"))
    purpose_obj = _get(item, ("purpose", "citation_purpose"))

    def pick(keys: Sequence[str], default: Any = None) -> Any:
        """Prefer the nested metadata value, then the top-level one."""
        if meta is not None:
            value = _get(meta, keys)
            if value is not None:
                return value
        return _get(item, keys, default)

    authors = pick(("authors", "author", "creators"))
    if isinstance(authors, (list, tuple)):
        names = [_as_str(_get(a, ("name", "display_name")) or a) for a in authors]
        authors = ", ".join(n for n in names if n)

    # purpose may be a plain string or a CitationPurpose-like object.
    if purpose_obj is not None and not isinstance(purpose_obj, str):
        purpose = _as_str(_get(purpose_obj, ("purpose", "text", "explanation")))
        relationship = _as_str(_get(purpose_obj, ("relationship",)))
        if relationship and relationship.lower() != "unknown":
            purpose = f"{purpose} ({relationship})" if purpose else relationship
    else:
        purpose = _as_str(purpose_obj or _get(item, ("why_cited", "reason", "explanation")))

    title = _as_str(pick(("title", "name")))
    if not title:
        # Fall back to the raw reference string when the lookup did not resolve.
        # Trimmed to roughly the title clause, since the whole entry (authors,
        # venue, arXiv id, year) makes an unreadable card heading.
        title = _trim_reference_text(
            _as_str(_get(item, ("raw_text", "raw", "text")), "Untitled reference")
        )

    url = _as_str(pick(("url", "link", "doi_url", "externalUrl")))
    if not url:
        open_access = pick(("open_access_pdf", "openAccessPdf"))
        if open_access is not None:
            url = _as_str(_get(open_access, ("url",)))

    # ``resolved`` is the shipped module's flag for "Semantic Scholar answered".
    # Its inverse is what the UI wants to disclose: served without a live lookup.
    resolved = _get(item, ("resolved",))
    from_cache = bool(_get(item, ("from_cache", "cached"), False))
    if resolved is not None and not resolved:
        from_cache = from_cache or False

    return Reference(
        title=title,
        authors=_as_str(authors),
        year=_as_str(pick(("year", "published_year", "date"))),
        citation_count=_as_int(pick(("citation_count", "citationCount", "cited_by"))),
        abstract=_as_str(pick(("abstract", "summary"))),
        purpose=purpose,
        url=url,
        from_cache=from_cache,
    )


def to_document(payload: Any, *, fallback_title: str = "Untitled paper") -> Document:
    """Build a ``Document`` from ``process_pdf``'s return value.

    The important job here is finding ``full_text_by_page`` whatever it is
    called, and coercing its keys to ``int``. JSON round-trips turn integer page
    keys into strings, which would silently break every page lookup downstream.
    """
    if payload is None:
        return Document(title=fallback_title)

    pages_raw = _get(payload, ("full_text_by_page", "text_by_page", "pages_text", "page_text"))
    full_text_by_page: dict[int, str] = {}

    if isinstance(pages_raw, dict):
        for key, value in pages_raw.items():
            page_no = _as_int(key)
            if page_no is not None:
                full_text_by_page[page_no] = _as_str(value)
    elif isinstance(pages_raw, (list, tuple)):
        # A list is 0-indexed in Python but pages are 1-indexed for humans.
        for i, value in enumerate(pages_raw, start=1):
            full_text_by_page[i] = _as_str(value)
    else:
        # Fall back to a list of page objects, e.g. [{"page": 1, "text": "..."}].
        pages = _get(payload, ("pages",))
        if isinstance(pages, (list, tuple)):
            for i, page in enumerate(pages, start=1):
                page_no = _as_int(_get(page, _PAGE_KEYS)) or i
                full_text_by_page[page_no] = _as_str(_get(page, ("text", "content", "body")))

    sections = _get(payload, ("sections", "section_names", "outline")) or ()
    if isinstance(sections, dict):
        sections = list(sections.keys())
    section_names = tuple(
        _as_str(_get(s, _SECTION_KEYS) if not isinstance(s, str) else s)
        for s in sections
    ) if isinstance(sections, (list, tuple)) else ()

    page_count = _as_int(_get(payload, ("page_count", "num_pages", "n_pages"))) or len(full_text_by_page)

    return Document(
        title=_as_str(_get(payload, ("title", "paper_title", "name")), fallback_title),
        page_count=page_count,
        full_text_by_page=full_text_by_page,
        sections=tuple(s for s in section_names if s),
        chunks=to_chunks(_get(payload, ("chunks", "passages", "segments")), full_text_by_page),
        raw=payload,
    )


def to_chunks(
    raw_chunks: Any,
    full_text_by_page: dict[int, str],
) -> tuple[dict[str, Any], ...]:
    """Normalise chunks to the dict shape ``build_index`` documents.

    The real ``build_index`` annotates ``chunks: list[dict]`` and indexes into
    them with ``c["text"]`` / ``c["page"]``, but the parser contract's
    ``ParsedDocument.chunks`` is a list of ``Chunk`` *dataclasses*. Rather than
    let that mismatch surface as a ``TypeError`` deep inside embedding, convert
    here — normalising at the boundary is exactly this layer's job.

    When there are no chunks at all (the local fallback parser does not produce
    them), synthesise one per page. Whole-page chunks are coarse for retrieval,
    but they let real semantic search run before Member 1's chunker lands
    instead of leaving chat dead.
    """
    normalised: list[dict[str, Any]] = []

    if isinstance(raw_chunks, (list, tuple)) and raw_chunks:
        for i, chunk in enumerate(raw_chunks):
            text = _as_str(_get(chunk, ("text", "content", "body")))
            if not text.strip():
                continue
            page = _as_int(_get(chunk, _PAGE_KEYS))
            normalised.append(
                {
                    "chunk_id": _as_str(_get(chunk, ("chunk_id", "id")), f"chunk_{i}"),
                    "text": text,
                    "page": page if page is not None else 1,
                    "section": _as_str(_get(chunk, _SECTION_KEYS)),
                }
            )
        if normalised:
            return tuple(normalised)

    for page_no in sorted(full_text_by_page):
        text = (full_text_by_page.get(page_no) or "").strip()
        if text:
            normalised.append(
                {
                    "chunk_id": f"page_{page_no}",
                    "text": text,
                    "page": page_no,
                    "section": "",
                }
            )
    return tuple(normalised)
