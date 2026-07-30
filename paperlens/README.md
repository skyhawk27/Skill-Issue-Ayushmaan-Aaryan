# PaperLens — dashboard (Member 5)

The Streamlit interface: two-pane layout, verification badges, PDF page-jump with
evidence highlighting, grounded chat, citation explorer, reviewer mode, and
verified-summary export.

**It runs right now.** Member 3's retrieval and verification modules are wired up
live; the other three fall back to local stand-ins that do real work. See
[Running without the rest of the team](#running-without-the-rest-of-the-team).

## Quick start

```bash
python3.13 -m venv .venv                        # 3.13, not 3.14 — see Environment
.venv/bin/pip install -r requirements.txt
.venv/bin/streamlit run paperlens/app.py
```

Upload any PDF. To use the recommended demo paper:

```bash
curl -L -o paperlens/integration/fixtures/attention-is-all-you-need.pdf \
  https://arxiv.org/pdf/1706.03762v7
```

### Chat needs an API key; nothing else does

`OPENAI_API_KEY` is required for chat only, because Member 3's `build_index` calls
the embeddings API:

```bash
export OPENAI_API_KEY=sk-...
```

Without it the chat panel says so and offers an explicit opt-in to offline keyword
search. It does **not** silently downgrade — you would have no way to tell which
retriever produced an answer, in a product whose whole point is knowing what to
trust. Summary, PDF navigation, highlighting, citations, reviewer and export all
work with no key.

## What's here

```
app.py                     entry point: upload → pipeline → dashboard
.streamlit/config.toml     the entire visual design (no custom CSS anywhere)
ui/
  dashboard.py             render_dashboard(document, brief, citations)  ← contract fn
  theme.py                 status semantics + the few tokens Python needs
  components.py            badge, claim card, tally, empty/error states
  pdf_pane.py              viewer, page nav, highlight overlay
  export.py                verified summary export (Feature 10)
  panels/                  summary · chat · citations · reviewer
integration/
  CONTRACT.md              ★ what the dashboard consumes — read this first
  adapters.py              import-or-fallback, signature-tolerant dispatch, error isolation
  contracts.py             UI dataclasses + tolerant normalisers
  highlight.py             quote → PDF bounding boxes
  pipeline.py              upload → parsed → verified orchestration
  stubs.py                 local stand-ins for the other four modules
tests/dashboard_checks.py  60 headless checks over the PRD success criteria
```

## Design

`.streamlit/config.toml` carries the whole look, derived from `DESIGN.md`
(the Notion analysis) and rendered monochrome. **There is no custom CSS in this
codebase** — no `unsafe_allow_html`, no injected `<style>`. Everything is native
Streamlit theming plus native containers, so the UI inherits Streamlit's own
accessibility and responsive behaviour instead of fighting it.

One rule governs the palette:

> **The verification badge is the only colour in the application.**

Chrome, nav, cards, type and dividers are warm paper `#f6f5f4`, white `#ffffff`
and near-black `#000000`. Because nothing else competes, a green/amber/red badge
reads from the back of a room, and colour carries information rather than
decoration. Feature 4B is the product; the palette says so.

Every status indicator is **glyph + word + score** (`✓ Verified · 0.94`), never
colour alone, so it survives a washed-out projector, a greyscale screenshot and
colour-blindness. All text pairs are measured at WCAG AA or better.

## Running without the rest of the team

Current state:

| Function | Owner | Status |
|---|---|---|
| `process_pdf` | Member 1 | **live** (`parser.document_processor`) |
| `build_index`, `ask_question` | Member 3 | **live** (`rag.embeddings`, `rag.chat`) |
| `verify_claim`, `verify_claims_batch` | Member 3 | **live** (`verification.verifier`) |
| `analyze_references` | Member 4 | **live** (`citations.explorer.explore_citations`) |
| `generate_brief` | Member 2 | local fallback — see note |
| `review` | Member 4 | local fallback |

> **Member 2's summarizer cannot be imported without credentials.**
> `summarization/briefing.py` constructs an `AsyncOpenAI` client at *module
> level* (line 18), so `import summarization.briefing` raises `OpenAIError`
> without `NVIDIA_API_KEY`. The resolver catches it and falls back, but the real
> summarizer is unreachable on any machine without that key. Building the client
> lazily inside `generate_brief` (as `rag/chat.py` does) would fix it.

`integration/stubs.py` provides the fallbacks, and they do **real work**, not
canned mocks:

| | stub does | real module adds |
|---|---|---|
| `generate_brief` | picks real sentences from the real paper | abstractive LLM summarisation |
| `review` | keyword-probes reproducibility signals | LLM review + consistency |

So **claim → page-jump → highlighted evidence works end to end today**, with no
`OPENAI_API_KEY` at all. Quotes are lifted verbatim from the paper, so they
genuinely locate and genuinely verify — through Member 3's real Feature 4B
pipeline.

They are not a substitute for the real thing: the brief is extractive rather than
abstractive and retrieval is lexical rather than semantic. The UI **says so**, in a
"How this output was produced" expander that describes what is generated locally.
Passing local heuristics off as verified AI output would be exactly the dishonesty
PaperLens exists to prevent.

### Dropping your module in

1. Put your file where PRD §13 says (e.g. `verification/verifier.py`).
2. Overwrite the placeholder `__init__.py` in your package — it holds no logic.
3. **Restart the server** (import resolution is cached in `st.cache_resource`).
4. Confirm it is live: `adapters.integration_status()` reports the resolved
   source for every contract function.

No file in `ui/` changes. Details and accepted field spellings: `integration/CONTRACT.md`.

## Tests

```bash
.venv/bin/python paperlens/tests/dashboard_checks.py   # dashboard — 60 checks
.venv/bin/python -m pytest paperlens/tests -q          # the team's pytest suite
```

The dashboard suite covers: upload, panel navigation, claim → highlighted
evidence, highlight recall across every claim, no false highlight on a fabricated
quote, verification coverage, the honest no-evidence refusal, scoped degradation
when a module raises, real-module pickup and `ImportError` fallback,
signature-tolerant dispatch, export contents, and — when Member 3's modules are
importable — live integration against their real verifier.

The assertions check the *mechanism*, not a particular mix of landed modules, so
they stay meaningful as teammates arrive.

`dashboard_checks.py` is deliberately **not** named `test_*`: its body runs on
import, so pytest collecting it would execute the whole dashboard during
`pytest tests/`. Converting it to proper pytest functions is a good follow-up.

## Notes for whoever touches this next

**Mismatches found while wiring up teammates' real modules.** All of these failed
*silently* before being handled — worth knowing, because that is the dangerous kind:

- `citations` exports `explore_citations`, not the contract's `analyze_references`.
  Until the resolver knew both names, the dashboard used its own stub while the
  real module sat right there.
- `explore_citations` is `async def` and returns `(list, stats_dict)`, not a list.
  Iterating that tuple produced two junk references.
- `verify_claim`'s `match_score` is 0–100; the PRD's examples are 0–1.
- `build_index` takes `chunks`, not a document.
- `utils.models.StructuredSummary` is a dataclass — neither dict nor iterable — so
  the summary came back empty until `to_brief` grew an explicit branch.

**Two things in the PRD/briefing were underspecified, and are resolved here.**

1. **Quote → highlight geometry.** Feature 4B stores *character offsets*, but the
   PDF viewer draws from *bounding boxes*, and nothing in the team contract owns
   the conversion. `integration/highlight.py` does it geometrically:
   `page.search_for()` first, then a `rapidfuzz` word-run fallback over
   `page.get_text("words")`. The fallback is load-bearing, not defensive —
   `search_for` silently fails on exactly the long, re-wrapped, ligature-stripped
   quotes LLMs produce, and the failure mode is a page jump with no highlight,
   i.e. the demo's key moment landing flat.

2. **`ask_question`'s signature.** `instructions.txt` says
   `(question, document)`; the integration notes imply `build_index()` +
   `ask_question()`. `adapters.invoke()` reads the callee's signature and passes
   by parameter name, so both work untouched.

**Verified against the viewer's frontend bundle, because its docstrings mislead:**
`scroll_to_page` and `annotations[].page` are **absolute** 1-based PDF page
numbers (the docstring's "positional value" reads as an index into
`pages_to_render`, but the bundle assigns canvas ids from the absolute number).
That is what makes windowed rendering safe.

**Performance:** the PDF pane renders a 3-page window by default, not the whole
document. Every claim click is a Streamlit rerun, which re-renders the component;
painting 40 canvases per click makes the app feel broken. "All pages" toggle in
the pane header when you need free scrolling.

**Environment:** Python **3.13**, not 3.14. Every native dep ships a real cp313
wheel; on 3.14, PyMuPDF 1.28 publishes only `cp314-cp314t` (free-threaded) plus a
less-exercised `cp313-abi3`. Streamlit is pinned `>=1.59` — 1.41–1.58 have an
iframe-scroll regression that resets page position and breaks the page-jump.
