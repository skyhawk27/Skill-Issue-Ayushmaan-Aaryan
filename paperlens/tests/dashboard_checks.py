"""End-to-end checks of the dashboard, driven headlessly through AppTest.

Run with::

    .venv/bin/python paperlens/tests/dashboard_checks.py

Deliberately **not** named ``test_*``: it is a script whose body executes on
import, so pytest collecting it would run the whole dashboard during
``pytest tests/`` and break the team's suite. Converting these to proper pytest
functions is a worthwhile follow-up; until then keep the filename as-is.

These walk the PRD's success criteria as literally as possible: upload a paper,
navigate between sections, click a claim and confirm the viewer is pointed at the
highlighted evidence, confirm every claim and answer carries a badge, confirm the
honest "not enough evidence" state, and confirm a failing module degrades only its
own panel.

AppTest executes the real app script, so this covers session state, panel
routing, button callbacks and chat. It does not execute the PDF viewer's
JavaScript — that is a third-party iframe component — so the highlight geometry is
asserted directly against ``integration.highlight`` instead, which is where the
logic actually lives.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest  # noqa: E402

from integration import adapters, highlight, pipeline  # noqa: E402
from ui import state  # noqa: E402

APP = str(ROOT / "app.py")
FIXTURE = ROOT / "integration" / "fixtures" / "attention-is-all-you-need.pdf"

_passed = 0
_failed: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    global _passed
    if condition:
        _passed += 1
        print(f"  PASS  {label}")
    else:
        _failed.append(label)
        print(f"  FAIL  {label}" + (f"\n        {detail}" if detail else ""))


def loaded_app(*, timeout: float = 60) -> AppTest:
    """An AppTest with a real paper already parsed and verified.

    The file_uploader widget cannot be driven from AppTest, so the pipeline is
    run directly and its output seeded into session state — the same values
    ``app.py`` would have written.
    """
    pdf_bytes = FIXTURE.read_bytes()
    path, key = pipeline.prepare_pdf(pdf_bytes, FIXTURE.name)
    document, brief, errors = pipeline.load_core(key, path, FIXTURE.name)
    assert not errors.get("Document parsing"), errors

    at = AppTest.from_file(APP, default_timeout=timeout)
    at.session_state[state.DOC_KEY] = key
    at.session_state[state.PDF_PATH] = path
    at.session_state[state.PDF_NAME] = FIXTURE.name
    at.session_state[state.DOCUMENT] = document
    at.session_state[state.BRIEF] = brief
    # Past the splash gate: these checks are about the dashboard a reader sees
    # after pressing Enter, not about the gate itself (covered separately).
    at.session_state[state.SPLASH_DISMISSED] = True
    return at.run()


def section(title: str) -> None:
    print(f"\n{title}")


def splash_html(at_instance) -> str:
    """Concatenated st.html output, so the splash can be located in a run."""
    blocks = []
    for element in at_instance.main:
        body = getattr(element, "body", None)
        if isinstance(body, str) and "pl-splash" in body:
            blocks.append(body)
    return "\n".join(blocks)


# ── 0. Splash screen ───────────────────────────────────────────────────────
section("Splash screen")
from ui import splash as splash_mod  # noqa: E402
from ui.theme import STATUS_STYLES  # noqa: E402

_markup = splash_mod.markup()
check("splash markup builds", "pl-splash" in _markup)
check("tick reuses the badge green rather than a copied hex",
      STATUS_STYLES["verified"].highlight in _markup,
      f'expected {STATUS_STYLES["verified"].highlight} in the markup')

# The splash is now a gate that holds indefinitely, so these safety properties
# matter more, not less: they are what stop a fixed full-viewport overlay from
# trapping the user if its stylesheet is stripped, unsupported or disabled.
# Assert them so they cannot be quietly edited away.
check("overlay never intercepts clicks", "pointer-events: none" in _markup)
check("overlay's resting state is invisible", "opacity: 0;" in _markup)
check("reduced motion still shows the way out",
      "prefers-reduced-motion" in _markup and "pl_splash_enter" in _markup.split(
          "prefers-reduced-motion")[1],
      "reduced-motion block does not re-show the Enter button")

at_splash = AppTest.from_file(APP, default_timeout=60).run()
check("no exception on the run that shows the splash", not at_splash.exception,
      str(at_splash.exception))
check("splash renders on the first run", "pl-splash" in splash_html(at_splash))
check("gate starts open", at_splash.session_state[state.SPLASH_DISMISSED] is False)

_enter = [b for b in at_splash.button if b.key == "pl_splash_enter"]
check("Enter is a real Streamlit widget, not a styled div", len(_enter) == 1,
      f"found {len(_enter)} buttons with that key")

# A gate must persist across reruns — otherwise it is a flash, not a gate.
at_still_open = at_splash.run()
check("splash persists on a rerun until Enter is pressed",
      "pl-splash" in splash_html(at_still_open),
      "splash vanished without the button being pressed")

# ...and pressing Enter must actually end it, on this run and every later one.
at_entered = at_still_open.button(key="pl_splash_enter").click().run()
check("no exception when entering", not at_entered.exception, str(at_entered.exception))
check("Enter dismisses the gate",
      at_entered.session_state[state.SPLASH_DISMISSED] is True)
check("splash is gone after entering", "pl-splash" not in splash_html(at_entered),
      "splash markup survived the Enter click")
check("splash stays gone on the next rerun",
      "pl-splash" not in splash_html(at_entered.run()))


# ── 1. Upload screen ───────────────────────────────────────────────────────
section("Upload screen")
at = AppTest.from_file(APP, default_timeout=60).run()
check("no exception on first load", not at.exception, str(at.exception))
check("file uploader present", len(at.file_uploader) == 1)
check("title rendered", any("PaperLens" in t.value for t in at.title))


# ── 1b. Feature catalogue and badge legend ─────────────────────────────────
section("Feature catalogue")
from ui import home as home_mod  # noqa: E402


def page_text(at_instance) -> str:
    """All markdown/caption/subheader text in a run, for content assertions.

    Some element proxies raise on ``.value`` (widgets whose key is not in session
    state), so this reads defensively rather than assuming every element has one.
    """
    parts = []
    for element in at_instance.main:
        try:
            value = getattr(element, "value", None)
        except Exception:  # noqa: BLE001 - probing heterogeneous element proxies
            continue
        if isinstance(value, str):
            parts.append(value)
    return "\n".join(parts)


_landing_text = page_text(at)

check("catalogue heading rendered", home_mod.CATALOGUE_HEADING in _landing_text)
check("catalogue fills a clean grid (9 cards, 3 columns)",
      len(home_mod.FEATURES) == 9 and len(home_mod.FEATURES) % 3 == 0,
      f"{len(home_mod.FEATURES)} features would leave a ragged row")

_missing = [f.title for f in home_mod.FEATURES if f.title not in _landing_text]
check("every feature card renders", not _missing, f"missing: {_missing}")

# The legend must come from STATUS_STYLES, not hardcoded copy — that is the
# whole reason it is sourced there. Assert against the table itself.
for _status in ("verified", "paraphrased", "unsupported"):
    _style = STATUS_STYLES[_status]
    check(f"legend shows {_status} from STATUS_STYLES",
          _style.label in _landing_text and _style.blurb in _landing_text,
          f"expected label {_style.label!r} and its blurb")

check("legend omits the internal 'not verified' state",
      STATUS_STYLES["unverified"].blurb not in _landing_text,
      "an internal state leaked into the reader-facing legend")

# The catalogue belongs to the landing screen only.
_loaded_text = page_text(loaded_app())
check("catalogue does not leak into the loaded dashboard",
      home_mod.CATALOGUE_HEADING not in _loaded_text)
check("feature cards do not leak into the loaded dashboard",
      not any(f.title in _loaded_text for f in home_mod.FEATURES),
      "a feature card rendered with a document loaded")


# ── 1c. Abstract, page index, and the normaliser bug they exposed ─────────
section("Abstract and page index")
from integration import thumbnails as thumbs_mod  # noqa: E402
from integration.contracts import (  # noqa: E402
    _get as _contracts_get,
    to_abstract,
    to_brief as _to_brief,
    to_page_summaries,
)

# Regression: the summarizer's real payload used to yield 9 claims instead of 1,
# with sections like "Paper title" and one that read
# "<built-in method title of str object at 0x...>".
_m2_payload = {
    "paper_title": "Attention Is All You Need",
    "total_pages_processed": 15,
    "page_by_page_summaries": [
        {"page_number": 2, "summary": "Background on recurrence."},
        {"page_number": 1, "summary": "Introduces the Transformer."},
    ],
    "conclusion": "Top-level conclusion.",
    "contributions": [{"claim": "Self-attention replaces recurrence.",
                       "candidate_quote": "dispensing with recurrence",
                       "claimed_page": 1, "match_score": 95, "status": "verified"}],
    "methodology": [], "results": [], "limitations": [],
    "prerequisites": ["Attention", "Seq2seq"],
    "global_synthesis": {
        "contributions_summary": "Proposes the Transformer.",
        "methodology_summary": "Encoder-decoder with self-attention.",
        "results_summary": "28.4 BLEU on WMT14.",
        "limitations_summary": "",
        "conclusion": "Nested conclusion.",
        "prerequisites": ["Attention", "Seq2seq", "RNNs"],
    },
}
_m2_brief = _to_brief(_m2_payload)

check("summarizer payload yields only real claims", len(_m2_brief.claims) == 1,
      f"{len(_m2_brief.claims)} claims: {[c.section for c in _m2_brief.claims]}")
_sections = {c.section for c in _m2_brief.claims}
check("document metadata is not turned into claim sections",
      not (_sections & {"Paper title", "Total pages processed", "Conclusion",
                        "Page by page summaries", "Global synthesis", "Prerequisites"}),
      f"junk sections: {_sections}")
check("no built-in method leaks in as a section name",
      not any("built-in method" in s for s in _sections), f"{_sections}")

# The underlying cause: getattr on a bare string returns str.title, a bound
# method, which is not None and sailed straight through.
check("_get ignores built-in methods on non-dict objects",
      _contracts_get("Attention", ("title", "name")) is None,
      f'got {_contracts_get("Attention", ("title", "name"))!r}')
check("_get still reads dicts", _contracts_get({"title": "real"}, ("title",)) == "real")

# Abstract
_abstract = _m2_brief.abstract
check("abstract builds labelled points", len(_abstract.points) == 3,
      f"{[l for l, _ in _abstract.points]}")
check("empty synthesis fields are dropped",
      "Limitations" not in [label for label, _ in _abstract.points])
check("nested global_synthesis wins over the top level",
      _abstract.conclusion == "Nested conclusion.", _abstract.conclusion)
check("abstract falls back to top level when synthesis is absent",
      to_abstract({"conclusion": "only top level"}).conclusion == "only top level")
check("empty payload gives an empty abstract", to_abstract({}).is_empty)

# Page summaries
check("page summaries parsed and ordered",
      [s.page for s in _m2_brief.page_summaries] == [1, 2],
      f"{[s.page for s in _m2_brief.page_summaries]}")
check("page summaries accept page/text spellings",
      to_page_summaries({"page_summaries": [{"page": 3, "text": "alt"}]})[0].summary == "alt")

# Thumbnails
_thumb_key = at.session_state[state.DOC_KEY] if state.DOC_KEY in at.session_state else "k"
_at_loaded_for_thumbs = loaded_app()
_pdf = _at_loaded_for_thumbs.session_state[state.PDF_PATH]
_doc_key = _at_loaded_for_thumbs.session_state[state.DOC_KEY]
_png = thumbs_mod.page_thumbnail(_doc_key, _pdf, 1)
check("thumbnail renders as PNG bytes",
      isinstance(_png, bytes) and _png[:8] == b"\x89PNG\r\n\x1a\n", f"{type(_png)}")
check("bad path returns None rather than raising",
      thumbs_mod.page_thumbnail(_doc_key, "/no/such.pdf", 1) is None)
check("out-of-range page returns None",
      thumbs_mod.page_thumbnail(_doc_key, _pdf, 99999) is None)

# The panel wiring: picking a page must move the viewer too.
_brief_loaded = _at_loaded_for_thumbs.session_state[state.BRIEF]
check("stub supplies page summaries so the index works without an API key",
      len(_brief_loaded.page_summaries) > 0,
      "no page summaries from the fallback summarizer")
check("stub supplies an abstract", not _brief_loaded.abstract.is_empty)

_page_buttons = [b for b in _at_loaded_for_thumbs.button
                 if b.key and b.key.startswith("pl.page-index-")]
check("page index renders a button per visible page", len(_page_buttons) > 1,
      f"{len(_page_buttons)} page buttons")

if len(_page_buttons) > 2:
    _target = _page_buttons[2]
    _after = _target.click().run()
    check("no exception selecting a page", not _after.exception, str(_after.exception))
    _picked = _after.session_state[state.PAGE_SELECTED]
    check("selecting a page records it", isinstance(_picked, int), f"{_picked!r}")
    check("selecting a page also moves the PDF viewer",
          _after.session_state[state.TARGET_PAGE] == _picked,
          f"TARGET_PAGE={_after.session_state[state.TARGET_PAGE]} vs page={_picked}")

# Degrade path: a brief with neither must not render either surface.
from integration.contracts import Brief as _Brief  # noqa: E402

check("empty brief carries an empty abstract and no pages",
      _Brief().abstract.is_empty and _Brief().page_summaries == ())


# ── 1d. Citations: extraction, stats, and form ────────────────────────────
section("Citations")
from integration import citation_stats as cstats  # noqa: E402
from integration.contracts import Reference as _Ref  # noqa: E402
from integration.textblocks import references_text as _refs_text  # noqa: E402

# THE regression that matters: the shipped parser emits sections with no `text`
# and no `references_text`, so extraction returned zero references on a paper
# containing forty. The adapter now repairs the payload before the extractor.
_at_cit = loaded_app()
_cit_doc = _at_cit.session_state[state.DOCUMENT]
_raw = _cit_doc.raw
check("parser payload really does lack the extractor's input",
      isinstance(_raw, dict) and not _raw.get("references_text")
      and not any((s or {}).get("text") for s in (_raw.get("sections") or [])),
      "fixture no longer reproduces the original mismatch")
check("references_text can be derived from page text",
      len(_refs_text(_cit_doc.full_text_by_page)) > 500,
      "derived reference block is suspiciously short")

_cit_refs, _cit_err = pipeline.load_references(
    _at_cit.session_state[state.DOC_KEY], _cit_doc)
check("extraction returns references (was 0)", len(_cit_refs) > 10,
      f"{len(_cit_refs)} references, err={_cit_err[:80]!r}")

# Year is recovered from raw text when the metadata lookup gives none.
check("years recovered without any metadata lookup",
      sum(1 for r in _cit_refs if r.year) > len(_cit_refs) * 0.5,
      f"{sum(1 for r in _cit_refs if r.year)}/{len(_cit_refs)} have a year")
check("year recovery takes the trailing year, not one inside a title",
      _contracts_get is not None
      and __import__("integration.contracts", fromlist=["_resolve_year"])._resolve_year(
          None, {"raw_text": "Krizhevsky. ImageNet 2012 challenge. NIPS, 2017."}) == "2017")

# Stats
_stats = cstats.summarise(_cit_refs)
check("stats compute a span and median", _stats.span is not None and _stats.median_year,
      f"span={_stats.span} median={_stats.median_year}")
check("headline points are produced", len(cstats.headline_points(_stats)) > 0)
check("stats degrade to None on empty input",
      cstats.summarise(()).span is None
      and cstats.summarise(()).median_year is None
      and cstats.headline_points(cstats.summarise(())) == [])

_fake = (
    _Ref(title="A", year="2020", citation_count=100),
    _Ref(title="B", year="2010", citation_count=5),
    _Ref(title="C", year="2000"),
)
_fs = cstats.summarise(_fake)
check("most_cited picks the highest count", _fs.most_cited is not None
      and _fs.most_cited.title == "A", f"{_fs.most_cited}")
check("median year ignores undated entries", _fs.median_year == 2010, f"{_fs.median_year}")
check("year histogram covers every dated reference",
      sum(_fs.year_histogram.values()) == 3, f"{_fs.year_histogram}")
check("chart falls back to years when no counts exist",
      not cstats.summarise((_Ref(title="X", year="1999"),)).has_citation_counts
      and cstats.summarise((_Ref(title="X", year="1999"),)).has_years)

# Form: a table, not forty cards. The verbosity regression guard.
_at_cit.session_state[state.PANEL] = "Citations"
_at_cit.session_state[state.REFERENCES] = _cit_refs
_at_cit = _at_cit.run()
check("citations panel renders without exception", not _at_cit.exception, str(_at_cit.exception))
check("references shown as one dataframe", len(_at_cit.dataframe) == 1,
      f"{len(_at_cit.dataframe)} dataframes")
check("conclusions shown as metrics", len(_at_cit.metric) == 3,
      f"{len(_at_cit.metric)} metrics")
_cit_text = page_text(_at_cit)
check("no per-reference card wall", _cit_text.count("Why cited:") == 0,
      "inline per-reference purpose blocks are back")


# ── 1e. LLM provider config and rate limiting ─────────────────────────────
section("LLM provider")
from integration import llm as llm_mod  # noqa: E402

# Unconfigured must behave exactly as before this module existed: returning None
# makes every teammate function build its own default client.
_saved_env = {k: os.environ.get(k) for k in (
    "PAPERLENS_LLM_BASE_URL", "PAPERLENS_LLM_API_KEY",
    "PAPERLENS_CHAT_MODEL", "PAPERLENS_EMBEDDING_MODEL")}
try:
    for k in _saved_env:
        os.environ.pop(k, None)
    llm_mod._client = None
    check("no provider configured -> client() is None, original behaviour kept",
          llm_mod.client() is None and not llm_mod.is_configured())

    os.environ["PAPERLENS_LLM_BASE_URL"] = "https://example.invalid/v1"
    os.environ["PAPERLENS_LLM_API_KEY"] = "test-key"
    os.environ["PAPERLENS_CHAT_MODEL"] = "some/chat-model"
    os.environ["PAPERLENS_EMBEDDING_MODEL"] = "some/embedqa-model"
    llm_mod._client = None
    check("provider configured -> a client is built", llm_mod.is_configured())
    _c = llm_mod.client()
    check("client exposes the OpenAI surface callers use",
          hasattr(_c, "embeddings") and hasattr(_c.chat, "completions"))
    check("asymmetric embedding models are detected",
          llm_mod._needs_input_type("nvidia/nv-embedqa-e5-v5")
          and not llm_mod._needs_input_type("nvidia/nv-embed-v1"))
    check("query vs passage inferred from the batch size",
          llm_mod._ThrottledEmbeddings._input_type(["one"]) == "query"
          and llm_mod._ThrottledEmbeddings._input_type(["a", "b"]) == "passage")
finally:
    for k, v in _saved_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    llm_mod._client = None

# The limiter is what stops the summarizer's 16-call burst starving chat.
_lim = llm_mod._RateLimiter(3)
_t0 = time.monotonic()
for _ in range(3):
    _lim.acquire()
check("limiter allows a burst up to its budget", time.monotonic() - _t0 < 1.0)
check("limiter tracks its window", len(_lim._calls) == 3)

_lim_full = llm_mod._RateLimiter(1)
_lim_full.acquire()
check("limiter blocks once the budget is spent",
      len(_lim_full._calls) == 1 and _lim_full._calls[0] <= time.monotonic())


# ── 2. Dashboard renders ───────────────────────────────────────────────────
section("Dashboard")
at = loaded_app()
check("no exception with a paper loaded", not at.exception, str(at.exception))

radios = [r for r in at.radio if r.label == "Section"]
check("nav rail present", len(radios) == 1)
if radios:
    # options come back with format_func applied, so match on the panel names.
    labels = list(radios[0].options)
    check(
        "all four panels offered",
        len(labels) == 4
        and all(
            name in label
            for name, label in zip(["Summary", "Chat", "Citations", "Reviewer"], labels)
        ),
        str(labels),
    )

brief = at.session_state[state.BRIEF]
check("claims produced", len(brief.claims) > 0, f"{len(brief.claims)} claims")


# ── 3. Every claim carries a verdict (PRD: no claim without a badge) ───────
section("Verification coverage")
unbadged = [c for c in brief.claims if c.evidence.has_quote and c.evidence.score is None]
check("no quoted claim lacks a score", not unbadged, f"{len(unbadged)} unscored")
check(
    "every claim has a known status",
    all(c.status in ("verified", "paraphrased", "unsupported", "unverified") for c in brief.claims),
)
tally = brief.tally()
check("tally sums to claim count", sum(tally.values()) == len(brief.claims))


# ── 4. Click a claim → the viewer is pointed at highlighted evidence ──────
section("Claim to evidence (the demo spine)")
jump_buttons = [b for b in at.button if b.key and b.key.startswith("summary-")]
check("summary claims have jump buttons", len(jump_buttons) > 0, f"{len(jump_buttons)} buttons")

if jump_buttons:
    target = brief.claims[0]
    at2 = jump_buttons[0].click().run()
    check("no exception after clicking a claim", not at2.exception, str(at2.exception))
    check(
        "viewer page set to the claim's page",
        at2.session_state[state.TARGET_PAGE] == target.evidence.page,
        f"{at2.session_state[state.TARGET_PAGE]} != {target.evidence.page}",
    )
    check(
        "quote handed to the viewer",
        at2.session_state[state.TARGET_QUOTE] == target.evidence.quote,
    )
    check("clicked claim marked active", at2.session_state[state.ACTIVE_CLAIM] == target.claim_id)

    # The geometry itself, since AppTest cannot run the viewer's JS.
    result = highlight.locate_quote(
        at2.session_state[state.PDF_PATH],
        target.evidence.quote,
        target.evidence.page,
        "#1aae39",
    )
    check("highlight boxes produced", result.found, f"method={result.method}")
    check("boxes carry the resolved page", all(a["page"] == result.page for a in result.annotations))
    check(
        "boxes have positive extent",
        all(a["width"] > 0 and a["height"] > 0 for a in result.annotations),
    )

# Highlight recall across every claim in the paper.
hits = sum(
    1 for c in brief.claims
    if c.evidence.has_quote
    and highlight.locate_quote(
        at.session_state[state.PDF_PATH], c.evidence.quote, c.evidence.page, "#1aae39"
    ).found
)
quoted = sum(1 for c in brief.claims if c.evidence.has_quote)
# Not 100%: with a real LLM summarizer the claims are abstractive, so some quotes
# genuinely do not appear verbatim in the paper. That is Feature 4B working, not
# failing — the invariant that matters is the one asserted immediately below.
check(f"highlight recall {hits}/{quoted}", quoted and hits >= quoted * 0.6,
      f"{quoted - hits} of {quoted} claims failed to locate — unusually low")

# The real guarantee: a quote that cannot be found must never be badged Verified.
# A locatable-but-wrong highlight is the one failure that would actively mislead.
_unlocatable_but_verified = [
    c for c in brief.claims
    if c.evidence.has_quote
    and c.evidence.status == "verified"
    and not highlight.locate_quote(
        at.session_state[state.PDF_PATH], c.evidence.quote, c.evidence.page, "#1aae39"
    ).found
]
check("no claim is badged Verified while its quote cannot be located",
      not _unlocatable_but_verified,
      f"{len(_unlocatable_but_verified)} verified claims have unlocatable quotes")


# ── 5. A fabricated quote must NOT be highlighted ─────────────────────────
section("No false highlights")
bogus = highlight.locate_quote(
    at.session_state[state.PDF_PATH],
    "The authors deploy a quantum blockchain to eliminate hallucination entirely.",
    1,
    "#c0362c",
)
check("fabricated quote produces no boxes", not bogus.found, f"method={bogus.method}")


# ── 6. Navigation between panels ───────────────────────────────────────────
section("Panel navigation")
from integration.contracts import Reference  # noqa: E402

# Pre-seed references for the Citations panel. Once Member 4's real module is
# installed it fetches OpenAlex live, and a rate-limited lookup with
# retries can exceed the AppTest timeout. This check is about the panel
# *rendering*; the fetch path is covered by the failure-isolation section below.
_seeded_refs = (
    Reference(title="Layer Normalization", authors="Ba et al.", year="2016",
              citation_count=9001, purpose="Normalisation scheme used in the encoder."),
    Reference(title="Unresolved reference", from_cache=True),
)

for panel in ("Chat", "Citations", "Reviewer", "Summary"):
    at_panel = loaded_app()
    at_panel.session_state[state.PANEL] = panel
    if panel == "Citations":
        at_panel.session_state[state.REFERENCES] = _seeded_refs
    at_panel = at_panel.run()
    check(f"{panel} panel renders", not at_panel.exception, str(at_panel.exception))


# ── 7. Chat: grounded answer, and the honest refusal ──────────────────────
section("Chat")
# The chat panel has two legitimate states depending on the environment: a live
# chat input, or a "needs an API key" notice when the real retrieval module is
# installed but unconfigured. Both are correct; assert on whichever applies, then
# force the offline retriever so the rest of the chat path is always exercised.
at_chat = loaded_app()
at_chat.session_state[state.PANEL] = "Chat"
at_chat = at_chat.run()

from ui.panels.chat import _LOCAL_MODE  # noqa: E402

has_input = len(at_chat.chat_input) == 1
needs_key = any("api key" in i.value.lower() for i in at_chat.info)
check("chat is either usable or explains why not", has_input or needs_key,
      f"inputs={len(at_chat.chat_input)} infos={[i.value for i in at_chat.info]}")
if needs_key:
    check("offline opt-in offered when credentials are missing", len(at_chat.toggle) >= 1)

# Force the offline retriever so chat behaviour is testable without a key.
at_chat = loaded_app()
at_chat.session_state[state.PANEL] = "Chat"
at_chat.session_state[_LOCAL_MODE] = True
at_chat = at_chat.run()
check("chat input present in offline mode", len(at_chat.chat_input) == 1,
      f"inputs={len(at_chat.chat_input)}")

# Regression: the opt-in used to be stored under the toggle's own widget key.
# Turning it on made the failure branch stop rendering, the toggle vanished, and
# Streamlit discarded the widget state — so the setting flipped straight back off.
def ss(at_instance, key, default=None):
    """AppTest's session_state proxy has no .get()."""
    return at_instance.session_state[key] if key in at_instance.session_state else default


check("offline mode survives the rerun that hides the failure branch",
      ss(at_chat, _LOCAL_MODE) is True,
      f"{_LOCAL_MODE}={ss(at_chat, _LOCAL_MODE)!r}")
check("offline toggle stays on screen so there is a way back",
      len(at_chat.toggle) >= 1, f"toggles={len(at_chat.toggle)}")

# On its own instance, so the turn count below is not perturbed.
_persist = loaded_app()
_persist.session_state[state.PANEL] = "Chat"
_persist.session_state[_LOCAL_MODE] = True
_persist = _persist.run()
_persist = _persist.chat_input[0].set_value("What is proposed?").run()
check("offline mode still set after asking a question",
      ss(_persist, _LOCAL_MODE) is True,
      f"{_LOCAL_MODE}={ss(_persist, _LOCAL_MODE)!r}")

if at_chat.chat_input:
    answered = at_chat.chat_input[0].set_value(
        "What architecture do the authors propose?"
    ).run()
    check("no exception answering a question", not answered.exception, str(answered.exception))
    history = answered.session_state[state.CHAT]
    check("turn recorded", len(history) == 1, f"{len(history)} turns")
    if history:
        turn = history[0]
        check("answer is grounded (not the refusal)", not turn.no_evidence)
        check("answer carries a verification score", turn.evidence.score is not None)

    at_refuse = loaded_app()
    at_refuse.session_state[state.PANEL] = "Chat"
    at_refuse.session_state[_LOCAL_MODE] = True
    at_refuse = at_refuse.run()
    refused = at_refuse.chat_input[0].set_value(
        "What is the capital city of Portugal?"
    ).run()
    check("no exception on an unanswerable question", not refused.exception, str(refused.exception))
    refusal_history = refused.session_state[state.CHAT]
    check(
        "unanswerable question refused honestly",
        bool(refusal_history) and refusal_history[0].no_evidence,
        str(refusal_history[0]) if refusal_history else "no turn recorded",
    )


# ── 8. Scoped degradation (NFR §11) ───────────────────────────────────────
section("Failure isolation")
import types  # noqa: E402


def exploding(*_args, **_kwargs):
    raise RuntimeError("OpenAlex unavailable (simulated)")


# Inject a *real* module that raises. Patching integration.stubs would not work:
# adapters._STUBS captured the original function objects at import time, so
# rebinding the module attribute leaves the registry pointing at the good one.
# Simulating a teammate's module blowing up is also the failure we actually care
# about.
#
# Break every module path the resolver tries, so this holds regardless of which
# one currently wins (explorer is preferred over extractor).
_broken_paths = {
    "citations.explorer": "explore_citations",
    "citations.extractor": "extract_references",
}
_saved = {p: sys.modules.get(p) for p in _broken_paths}
for path, attr in _broken_paths.items():
    module = types.ModuleType(path)
    setattr(module, attr, exploding)
    setattr(module, "analyze_references", exploding)
    sys.modules[path] = module

adapters.resolve.clear()
adapters.run_analyze_references.clear()
try:
    check("broken module is the one resolved", adapters.resolve("analyze_references").is_real)

    at_fail = loaded_app()
    at_fail.session_state[state.PANEL] = "Citations"
    at_fail = at_fail.run()
    check("citations failure does not raise", not at_fail.exception, str(at_fail.exception))
    check(
        "citations failure reported in place",
        any("unavailable" in w.value.lower() for w in at_fail.warning),
        f"warnings={[w.value for w in at_fail.warning]}",
    )

    at_fail.session_state[state.PANEL] = "Summary"
    at_fail = at_fail.run()
    check("summary still works while citations is broken", not at_fail.exception)
    check(
        "summary still renders claims while citations is broken",
        len([b for b in at_fail.button if b.key and b.key.startswith("summary-")]) > 0,
    )
finally:
    for path, previous in _saved.items():
        if previous is None:
            sys.modules.pop(path, None)
        else:
            sys.modules[path] = previous
    adapters.resolve.clear()
    adapters.run_analyze_references.clear()


# ── 9. Real-module pickup and ImportError fallback ────────────────────────
section("Adapter resolution")
# These assertions must hold whichever teammates have landed, so they check the
# *mechanism* rather than a particular mix of real and stubbed modules.
adapters.resolve.clear()
status = adapters.integration_status()
check("all eight contract functions resolve", len(status) == 8, str(sorted(status)))
check("every resolution yields a callable", all(callable(r.fn) for r in status.values()))
check(
    "stub-backed functions report themselves as stubs",
    all(r.is_real or r.source == "integration.stubs" for r in status.values()),
)
print("        resolved: " + ", ".join(
    f"{n}={'real' if r.is_real else 'stub'}" for n, r in status.items()
))

# A name with no real module on disk must fall back. Reviewer Mode is the stable
# case: nobody has written it, whereas every other contract function now resolves
# to a teammate's module. Picking an already-real name would make this test
# depend on who has pushed.
check("absent module falls back to the stub",
      not adapters.resolve("review").is_real)

# Inject a fake real module under that name and confirm it is preferred.
fake = types.ModuleType("reviewer.reviewer")
fake.review = lambda document: {"reproducibility_score": 7.5, "checks": [], "findings": []}
sys.modules["reviewer.reviewer"] = fake
adapters.resolve.clear()
resolved = adapters.resolve("review")
check("real module preferred over stub", resolved.is_real, resolved.source)
check("resolved to the injected module", resolved.source == "reviewer.reviewer.review")

adapters.run_review.clear()
outcome = adapters.run_review("k", None)
check("injected module actually invoked",
      outcome.ok and outcome.value.get("reproducibility_score") == 7.5, str(outcome))

del sys.modules["reviewer.reviewer"]
adapters.resolve.clear()
adapters.run_review.clear()
check("falls back to stub again once removed",
      not adapters.resolve("review").is_real)

# When Member 3's modules are present, confirm we really drive them.
if adapters.resolve("verify_claim").is_real:
    section("Live integration with Member 3")
    real = adapters.resolve("verify_claim")
    check("verify_claim resolves into verification.verifier",
          real.source.startswith("verification.verifier"), real.source)

    doc_real = at.session_state[state.DOCUMENT]
    quote = brief.claims[0].evidence.quote
    got = adapters.run_verify_claim(quote, brief.claims[0].evidence.page,
                                    doc_real.full_text_by_page)
    check("real verifier returns a result", got.ok, got.error)
    if got.ok:
        from integration.contracts import to_evidence

        ev = to_evidence(got.value)
        # Their match_score is 0-100; ours is 0-1. The normaliser must bridge it.
        check("0-100 match_score normalised to 0-1",
              ev.score is not None and 0.0 <= ev.score <= 1.0, f"score={ev.score}")
        check("verbatim quote verifies against the real pipeline",
              ev.status == "verified", f"status={ev.status} score={ev.score}")

        bogus_real = adapters.run_verify_claim(
            "A quantum blockchain eliminates all hallucination.", 1,
            doc_real.full_text_by_page,
        )
        check("real verifier rejects a fabricated quote",
              bogus_real.ok and to_evidence(bogus_real.value).status == "unsupported",
              str(bogus_real.value if bogus_real.ok else bogus_real.error))

    # StructuredSummary is a dataclass — neither dict nor iterable — so to_brief
    # needs an explicit branch or the summary comes back silently empty.
    try:
        from utils.models import StructuredSummary, SummaryClaim

        from integration.contracts import to_brief as _to_brief

        sample = StructuredSummary(
            contributions=[SummaryClaim(claim="c", quote="q", page=1,
                                        match_score=0.95, status="verified")],
            results=[SummaryClaim(claim="r", quote="q", page=2,
                                  match_score=0.7, status="paraphrased")],
        )
        parsed = _to_brief(sample)
        check("StructuredSummary dataclass parses into a Brief",
              len(parsed.claims) == 2, f"{len(parsed.claims)} claims")
        check("its sections map to PRD headings",
              {c.section for c in parsed.claims} == {"Main contribution", "Results"},
              str({c.section for c in parsed.claims}))
    except ImportError:
        pass


# ── 10. Signature-tolerant dispatch ───────────────────────────────────────
section("Signature tolerance")


def positional_doc(question, document):
    return {"answer": f"doc:{question}", "page": 1, "quote": "", "no_evidence": False}


def positional_index(question, index):
    return {"answer": f"index:{question}", "page": 1, "quote": "", "no_evidence": False}


def kwargs_only(*, query, retriever=None, **_):
    return {"answer": f"kw:{query}", "page": 1, "quote": "", "no_evidence": False}


for fn, expected in ((positional_doc, "doc:q"), (positional_index, "index:q"), (kwargs_only, "kw:q")):
    got = adapters.invoke(
        fn,
        {"question": "q", "document": "DOC", "index": "IDX", "context": "IDX"},
        fallback_order=("question", "index", "document"),
    )
    check(f"invoke handles {fn.__name__}", got["answer"] == expected, str(got))


# ── 11. Export ────────────────────────────────────────────────────────────
section("Export")
at_export = loaded_app()
downloads = at_export.download_button
check("export button present", len(downloads) >= 1)

from ui import export  # noqa: E402

markdown = export.summary_markdown(at_export.session_state[state.DOCUMENT], brief)
check("export mentions every claim", all(c.text[:40] in markdown for c in brief.claims))
check("export carries status markers", "[VERIFIED]" in markdown or "[PARAPHRASED]" in markdown)
check("export carries page references", "p." in markdown)


# ── Summary ───────────────────────────────────────────────────────────────
print(f"\n{'=' * 62}")
print(f"{_passed} passed, {len(_failed)} failed")
if _failed:
    for name in _failed:
        print(f"  FAILED: {name}")
    sys.exit(1)
print("All checks passed.")
