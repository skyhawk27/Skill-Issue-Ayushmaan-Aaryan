"""The single boundary between the dashboard and everyone else's code.

Nothing in ``ui/`` imports a teammate's module directly. Everything goes through
here, which does four things no panel should have to think about:

1. **Import-or-fallback.** Each function is looked for at every module path the
   briefing documents mention. If none is importable, the local implementation
   from ``stubs.py`` is used. The dashboard therefore runs today and upgrades
   itself the moment a teammate's file lands — no flag, no edit to any UI file.

2. **Signature-tolerant dispatch.** ``instructions.txt`` says
   ``ask_question(question, document)``; the integration notes say to build an
   index with ``build_index()`` and call ``ask_question()`` per input. Rather
   than bet on one, :func:`invoke` reads the callee's signature and supplies
   arguments by parameter *name*, so both wirings work unchanged. Same for
   ``verify_claim``'s ``full_text_by_page``.

3. **Error isolation.** :func:`safe_call` converts an exception into a value.
   NFR §11 requires that a OpenAlex outage or a Reviewer Mode crash
   leave the rest of the dashboard usable; that is enforced structurally here
   rather than by hoping every panel remembers a try/except.

4. **Caching.** ``build_index`` under ``st.cache_resource`` (as the integration
   notes ask), the rest under ``st.cache_data``, all keyed on a content hash of
   the uploaded file so re-running a paper is instant and switching papers is
   correct.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import inspect
import logging
from dataclasses import dataclass
from typing import Any, Callable, Iterable

import streamlit as st

from integration import llm, stubs
from integration.textblocks import references_text

logger = logging.getLogger("paperlens.integration")


# ─── Resolution ────────────────────────────────────────────────────────────

#: Where to look for each contract function, most-specific path first.
#: Covers the PRD §13 layout, the flatter layout in instructions.txt, and the
#: import paths named in the integration notes.
_LOOKUP: dict[str, tuple[tuple[str, str], ...]] = {
    "process_pdf": (
        ("parser.document_processor", "process_pdf"),
        ("parser.pdf_parser", "process_pdf"),
        ("parser.process_pdf", "process_pdf"),
        ("parser", "process_pdf"),
        ("document_processor", "process_pdf"),
    ),
    "generate_brief": (
        ("summarization.briefing", "generate_brief"),
        ("summarization", "generate_brief"),
        ("ai.summarizer", "generate_brief"),
        ("briefing.generate_brief", "generate_brief"),
        ("briefing", "generate_brief"),
        ("ai", "generate_brief"),
    ),
    "build_index": (
        ("rag.embeddings", "build_index"),
        ("rag.retriever", "build_index"),
        ("rag", "build_index"),
    ),
    "ask_question": (
        ("rag.chat", "ask_question"),
        ("rag.retriever", "ask_question"),
        ("chat.ask_question", "ask_question"),
        ("chat", "ask_question"),
        ("rag", "ask_question"),
    ),
    "verify_claim": (
        ("verification.verifier", "verify_claim"),
        ("verification.fuzzy_match", "verify_claim"),
        ("verification", "verify_claim"),
    ),
    "verify_claims_batch": (
        ("verification.verifier", "verify_claims_batch"),
        ("verification", "verify_claims_batch"),
    ),
    "analyze_references": (
        # The team contract names this analyze_references, but the shipped module
        # calls it explore_citations. Both are looked for, most-capable first:
        # explore_citations runs the full extract -> resolve -> explain pipeline,
        # while extract_references does extraction only (no metadata, no
        # "why cited"). Without these two entries the dashboard would silently
        # fall back to its stub even though the real module was right there.
        ("citations", "analyze_references"),
        ("citations.explorer", "explore_citations"),
        ("citations", "explore_citations"),
        ("citations.extractor", "analyze_references"),
        ("citations.extractor", "extract_references"),
        ("citations", "extract_references"),
        ("citations.openalex", "analyze_references"),
    ),
    "review": (
        ("reviewer.reviewer", "review"),
        ("reviewer.reviewer", "review_paper"),
        ("reviewer.reviewer", "reviewer_mode"),
        ("reviewer", "review"),
    ),
}

#: Which stub backs each name when nothing real is importable.
_STUBS: dict[str, Callable[..., Any]] = {
    "process_pdf": stubs.process_pdf,
    "generate_brief": stubs.generate_brief,
    "build_index": stubs.build_index,
    "ask_question": stubs.ask_question,
    "verify_claim": stubs.verify_claim,
    "verify_claims_batch": stubs.verify_claims_batch,
    "analyze_references": stubs.analyze_references,
    "review": stubs.review,
}

#: Human-readable owner, used in the UI's "what's live" panel.
OWNERS: dict[str, str] = {
    "process_pdf": "Member 1 · parser",
    "generate_brief": "Member 2 · summarizer",
    "build_index": "Member 3 · retrieval",
    "ask_question": "Member 3 · chat",
    "verify_claim": "Member 3 · verification",
    "verify_claims_batch": "Member 3 · verification",
    "analyze_references": "Member 4 · citations",
    "review": "Member 4 · reviewer",
}


@dataclass(frozen=True)
class Resolved:
    """A contract function, plus where it actually came from."""

    name: str
    fn: Callable[..., Any]
    is_real: bool
    source: str

    @property
    def owner(self) -> str:
        return OWNERS.get(self.name, self.name)


def _looks_like_placeholder(module: Any, attr: str) -> bool:
    """True when the attribute is missing from one of our placeholder packages."""
    return not callable(getattr(module, attr, None))


@st.cache_resource(show_spinner=False)
def resolve(name: str) -> Resolved:
    """Find the real implementation of ``name``, or fall back to the stub.

    Cached as a resource: import resolution is process-wide and must not be
    repeated on every rerun. Restart the server (or clear caches) after dropping
    in a new teammate module.
    """
    for module_path, attr in _LOOKUP.get(name, ()):
        try:
            module = importlib.import_module(module_path)
        except Exception:
            continue
        if _looks_like_placeholder(module, attr):
            continue
        fn = getattr(module, attr)
        logger.info("paperlens: using real %s from %s.%s", name, module_path, attr)
        if name == "generate_brief":
            # The summarizer fans out one call per page and cannot be reached
            # through a `client=` parameter, so its burst is brought under the
            # shared budget here, at the moment it is first resolved.
            llm.pace_summarizer()
        return Resolved(name=name, fn=fn, is_real=True, source=f"{module_path}.{attr}")

    stub = _STUBS.get(name)
    if stub is None:
        raise KeyError(f"No stub registered for contract function {name!r}")
    return Resolved(name=name, fn=stub, is_real=False, source="integration.stubs")


def integration_status() -> dict[str, Resolved]:
    """Resolution state for every contract function — drives the status UI."""
    return {name: resolve(name) for name in _LOOKUP}


def missing_capabilities() -> list[str]:
    """Contract function names currently served by a local fallback.

    Returns function names rather than owners: the UI describes what is running
    locally in terms of its effect on the output, not whose module is missing.
    """
    return [name for name, resolved in integration_status().items() if not resolved.is_real]


# ─── Signature-tolerant invocation ─────────────────────────────────────────

#: Parameter-name synonyms, so we can satisfy a callee's signature whatever the
#: author happened to call things. Keys are the logical values we can provide.
_SYNONYMS: dict[str, tuple[str, ...]] = {
    "question": ("question", "query", "q", "user_input", "prompt", "text"),
    "document": ("document", "doc", "parsed", "parsed_doc", "parsed_document",
                 "paper", "data"),
    "index": ("index", "idx", "doc_index", "docindex", "retriever", "vector_store",
              "store", "vectorstore", "faiss_index", "db"),
    "context": ("context", "ctx"),
    "quote": ("quote", "candidate_quote", "span", "text"),
    "page": ("page", "claimed_page", "page_number", "page_no"),
    "full_text_by_page": ("full_text_by_page", "text_by_page", "pages_text",
                          "pages", "page_text"),
    "client": ("client", "openai_client", "llm_client"),
    "pdf_path": ("pdf_path", "path", "file_path", "filename", "pdf"),
    "claims": ("claims", "items", "batch"),
    # build_index takes chunks, not a document — see run_build_index.
    "chunks": ("chunks", "passages", "segments", "docs"),
}


def invoke(fn: Callable[..., Any], provided: dict[str, Any], *, fallback_order: Iterable[str] = ()) -> Any:
    """Call ``fn``, matching what we can provide against what it asks for.

    ``provided`` maps logical names (the keys of ``_SYNONYMS``) to values. Each
    of ``fn``'s parameters is satisfied by name where possible; parameters we
    cannot fill are left to their defaults. ``fallback_order`` lists logical
    names to pass positionally when a signature cannot be read at all (C
    extensions, some decorated callables) or when parameter names match nothing.

    This is what lets ``ask_question(question, document)`` and
    ``ask_question(question, index)`` both work without the caller knowing which
    one Member 3 shipped.
    """
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return _call(fn, [provided[name] for name in fallback_order if name in provided], {})

    # Reverse-map each parameter name to a logical value.
    reverse: dict[str, str] = {}
    for logical, aliases in _SYNONYMS.items():
        for alias in aliases:
            reverse.setdefault(alias, logical)

    args: list[Any] = []
    kwargs: dict[str, Any] = {}
    unfilled_positional = False
    remaining = [name for name in fallback_order if name in provided]

    for param in signature.parameters.values():
        if param.kind is inspect.Parameter.VAR_POSITIONAL:
            continue
        if param.kind is inspect.Parameter.VAR_KEYWORD:
            continue

        logical = reverse.get(param.name.lower())
        value = provided.get(logical) if logical else None

        # A parameter we recognise but have no value for behaves as unknown.
        if logical is None or value is None:
            if param.default is not inspect.Parameter.empty:
                continue  # let the callee's own default stand
            # Required parameter we could not name-match: take the next thing we
            # have, in declared fallback order.
            if remaining:
                value = provided[remaining.pop(0)]
            else:
                unfilled_positional = True
                continue
        elif logical in remaining:
            remaining.remove(logical)

        if param.kind is inspect.Parameter.POSITIONAL_ONLY:
            args.append(value)
        else:
            kwargs[param.name] = value

    if unfilled_positional:
        logger.warning(
            "paperlens: could not fill every required parameter of %s%s; calling anyway",
            getattr(fn, "__name__", fn), signature,
        )

    return _call(fn, args, kwargs)


def _call(fn: Callable[..., Any], args: list[Any], kwargs: dict[str, Any]) -> Any:
    """Invoke ``fn``, awaiting it if it turns out to be a coroutine function.

    ``citations.explorer.explore_citations`` is ``async def`` — it fans out
    concurrent OpenAlex lookups. Streamlit's script runner is synchronous,
    so somebody has to bridge that, and the adapter boundary is the right place:
    no panel should have to know whether a teammate's function happens to be
    async.
    """
    result = fn(*args, **kwargs)

    if not inspect.isawaitable(result):
        return result

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No loop on this thread, which is the normal Streamlit case.
        return asyncio.run(result)

    # Already inside a running loop (e.g. a test harness). asyncio.run would
    # raise, so hand off to a separate thread with its own loop.
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, result).result()


# ─── Error isolation ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class Outcome:
    """The result of a guarded call: a value, or a reason there isn't one."""

    ok: bool
    value: Any = None
    error: str = ""

    def unwrap(self, default: Any = None) -> Any:
        return self.value if self.ok else default


#: Substrings that mark a failure as "missing/invalid API credentials" rather
#: than a bug. Worth distinguishing: it is the single most likely reason a
#: freshly-cloned checkout cannot answer a question, and the fix is one env var.
_CREDENTIAL_MARKERS = (
    "api_key", "api key", "openai_api_key", "missing credentials",
    "authenticationerror", "incorrect api key", "unauthorized", "401",
)


def is_credential_error(message: str) -> bool:
    """True when an error message looks like absent or rejected API credentials."""
    lowered = (message or "").lower()
    return any(marker in lowered for marker in _CREDENTIAL_MARKERS)


def safe_call(label: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Outcome:
    """Run ``fn``, turning any exception into a failed :class:`Outcome`.

    ``label`` names the feature for the log and for the UI's fallback message
    ("Citation data unavailable"), so a failure reads as a scoped degradation
    rather than a stack trace.
    """
    try:
        return Outcome(ok=True, value=fn(*args, **kwargs))
    except Exception as exc:  # noqa: BLE001 - deliberately broad; see docstring
        logger.exception("paperlens: %s failed", label)
        return Outcome(ok=False, error=f"{type(exc).__name__}: {exc}")


# ─── Cache keys ────────────────────────────────────────────────────────────


def content_key(pdf_bytes: bytes) -> str:
    """Stable cache key for an uploaded file.

    Hashing content rather than filename means re-uploading the same paper hits
    the cache, and two papers that happen to share a filename do not collide.
    """
    return hashlib.sha256(pdf_bytes).hexdigest()[:16]


# ─── The public, cached pipeline ────────────────────────────────────────────
# Streamlit excludes leading-underscore parameters from cache hashing, which is
# how unhashable values (documents, indexes) travel alongside a hashable key.


@st.cache_data(show_spinner=False, max_entries=8)
def run_process_pdf(cache_key: str, pdf_path: str) -> Outcome:
    """Parse the PDF. ``cache_key`` is the content hash; ``pdf_path`` the temp file."""
    resolved = resolve("process_pdf")
    return safe_call(
        "Document parsing",
        lambda: invoke(resolved.fn, {"pdf_path": pdf_path}, fallback_order=("pdf_path",)),
    )


@st.cache_data(show_spinner=False, max_entries=8)
def run_generate_brief(cache_key: str, _document: Any) -> Outcome:
    """Generate the structured summary.

    The shipped ``generate_brief(doc_json: dict)`` wants the raw parser payload,
    not our ``Document`` dataclass, so ``Document.raw`` is offered first — same
    reasoning as :func:`run_analyze_references`.
    """
    resolved = resolve("generate_brief")
    raw = getattr(_document, "raw", None)
    payload = raw if isinstance(raw, dict) else _document

    return safe_call(
        "Summarization",
        lambda: invoke(
            resolved.fn,
            {"document": payload, "context": payload},
            fallback_order=("document",),
        ),
    )


@st.cache_resource(show_spinner=False, max_entries=4)
def run_build_index(cache_key: str, _document: Any, local: bool = False) -> Outcome:
    """Build the retrieval index.

    ``st.cache_resource`` per the integration notes: the index is a shared,
    non-serialisable resource (it holds a FAISS index), so it must not be copied
    per session the way ``cache_data`` would.

    Note the real signature is ``build_index(chunks, full_text_by_page)`` — it
    takes **chunks, not a document**. So chunks and page text are offered
    separately here, with the document kept as a fallback for any implementation
    that prefers the whole thing.
    """
    resolved = resolve("build_index")
    chunks = list(getattr(_document, "chunks", ()) or ())
    pages = getattr(_document, "full_text_by_page", {}) or {}

    if local:
        return safe_call("Search index", lambda: stubs.build_index(_document))

    return safe_call(
        "Search index",
        lambda: invoke(
            resolved.fn,
            {
                "chunks": chunks,
                "full_text_by_page": pages,
                "document": _document,
                "context": _document,
                # None when no provider override is set, which every teammate
                # function reads as "build your own default client".
                "client": llm.client(),
            },
            fallback_order=("chunks", "full_text_by_page"),
        ),
    )


def run_ask_question(question: str, document: Any, index: Any, *, local: bool = False) -> Outcome:
    """Ask one question.

    Deliberately uncached — every user question is new, and caching would make a
    repeated question look instant in a way that misrepresents the pipeline.
    Both ``document`` and ``index`` are offered; :func:`invoke` passes whichever
    the real ``ask_question`` actually declares.

    ``local=True`` forces the offline keyword retriever. This is only ever set
    from an explicit user opt-in in the chat panel — never as an automatic
    fallback when the real module fails, because silently downgrading semantic
    retrieval to keyword matching would misrepresent the answer's provenance.
    """
    resolved = Resolved("ask_question", stubs.ask_question, False, "integration.stubs") \
        if local else resolve("ask_question")
    return safe_call(
        "Chat",
        lambda: invoke(
            resolved.fn,
            {
                "question": question,
                "document": document,
                "index": index,
                "context": index if index is not None else document,
                "client": llm.client(),
            },
            fallback_order=("question", "index", "document"),
        ),
    )


def _with_references_text(payload: Any, document: Any) -> Any:
    """Ensure the payload carries the reference block the extractor reads.

    The shipped parser emits ``sections`` as
    ``[id, title, level, page_start, page_end]`` — no ``text`` — and no
    ``references_text``. The citation extractor looks for a section literally
    named "References" and reads its ``text``. The two never meet, so extraction
    found **zero** references on a paper containing forty.

    Repairing it here is exactly this layer's job: neither teammate has to change
    shape, and the fix disappears on its own the day the parser supplies either
    field itself.
    """
    if not isinstance(payload, dict):
        return payload

    if _as_text(payload.get("references_text")):
        return payload

    sections = payload.get("sections")
    if isinstance(sections, (list, tuple)) and any(
        _as_text(s.get("text")) for s in sections if isinstance(s, dict)
    ):
        return payload  # the parser already carries section text

    pages = getattr(document, "full_text_by_page", None) or {}
    block = references_text(pages)
    if not block:
        return payload

    repaired = dict(payload)
    repaired["references_text"] = block
    logger.info("paperlens: supplied references_text (%d chars) for extraction", len(block))
    return repaired


def _as_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


@st.cache_data(show_spinner=False, max_entries=8)
def run_analyze_references(cache_key: str, _document: Any) -> Outcome:
    """Extract and enrich the reference list.

    The shipped ``extract_references(parsed_doc: dict)`` is annotated for a
    **dict** and indexes into it, so the original parser payload is offered
    ahead of our ``Document`` dataclass. ``Document.raw`` preserves it for
    exactly this reason.
    """
    resolved = resolve("analyze_references")
    raw = getattr(_document, "raw", None)
    payload = raw if isinstance(raw, dict) else _document
    payload = _with_references_text(payload, _document)

    return safe_call(
        "Citation analysis",
        lambda: invoke(
            resolved.fn,
            {"document": payload, "context": payload},
            fallback_order=("document",),
        ),
    )


@st.cache_data(show_spinner=False, max_entries=8)
def run_review(cache_key: str, _document: Any) -> Outcome:
    """Run Reviewer Mode."""
    resolved = resolve("review")
    return safe_call(
        "Reviewer mode",
        lambda: invoke(
            resolved.fn,
            {"document": _document, "context": _document},
            fallback_order=("document",),
        ),
    )


def run_verify_claim(quote: str, page: int | None, full_text_by_page: dict[int, str]) -> Outcome:
    """Verify a single claim through Member 3's pipeline (or the local stub).

    Uncached on purpose: the verifier is cheap (a fuzzy match), and the
    dashboard calls it only for claims that arrived without a status.
    """
    resolved = resolve("verify_claim")
    return safe_call(
        "Verification",
        lambda: invoke(
            resolved.fn,
            {"quote": quote, "page": page, "full_text_by_page": full_text_by_page},
            fallback_order=("quote", "page", "full_text_by_page"),
        ),
    )
