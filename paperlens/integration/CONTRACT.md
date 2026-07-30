# What the dashboard consumes

Member 5's side of the Milestone-1 "shared JSON schema" item. This is what the UI
reads, and what it does when a field is missing.

## Who has landed

| Member | Function | Status |
|---|---|---|
| 1 · parser | `process_pdf` | local fallback |
| 2 · summarizer | `generate_brief` | local fallback |
| 3 · retrieval | `build_index` | **live** — `rag.embeddings` |
| 3 · chat | `ask_question` | **live** — `rag.chat` |
| 3 · verification | `verify_claim`, `verify_claims_batch` | **live** — `verification.verifier` |
| 4 · citations | `analyze_references` | **live** — `citations.explorer.explore_citations` |
| 4 · reviewer | `review` | local fallback |

The **Modules** popover in the app's nav rail shows this live. Member 3's real
signatures are documented below rather than guessed at; the others are still the
preferred-shape proposal.

> **Chat needs `OPENAI_API_KEY`.** Now that the real retrieval module is wired up,
> `build_index` calls the OpenAI embeddings API. Without a key the chat panel says
> so and offers an explicit opt-in to offline keyword search — it does **not**
> silently downgrade, because you would then have no way to tell which retriever
> produced an answer. Everything else (summary, PDF, highlighting, citations,
> reviewer, export) works with no key at all.

**You do not have to match this exactly.** `integration/contracts.py` accepts
several spellings of every field and normalises them. This document says what is
*preferred*, and — more usefully — what the UI genuinely **needs** versus what it
degrades gracefully without.

Nothing here requires you to import anything from `ui/`. Export your function
from any of the module paths listed under "Where I look for you" and the
dashboard will pick it up on the next server restart.

---

## Two contradictions in the briefing docs, and how they're resolved

`instructions.txt` and the integration-notes screenshot disagree. Rather than
pick a winner, `adapters.invoke()` reads your function's signature and passes
arguments **by parameter name**, so both wirings work untouched.

| | `instructions.txt` says | Integration notes say | What the dashboard does |
|---|---|---|---|
| Chat | `ask_question(question, document)` | `build_index()` + `ask_question()` per input | Offers `question`, `document`, `index` and `context`; passes whichever your signature declares |
| Layout | flat `parser/ briefing/ chat/` | PRD §13 `parser/ ai/ rag/ verification/` | Looks in **both** |

So: name your parameters something recognisable (`document`, `index`,
`retriever`, `question`, `query`…) and you can wire it however you like. Full
synonym list is in `adapters._SYNONYMS`.

---

## Member 1 — `process_pdf(pdf_path) -> dict`

**Where I look for you:** `parser.pdf_parser`, `parser.process_pdf`, `parser`

```python
{
  "title": "Attention Is All You Need",
  "page_count": 15,
  "full_text_by_page": {1: "Provided proper attribution…", 2: "…"},
  "sections": [{"section": "Methodology", "page": 3}, …],
}
```

### `full_text_by_page` is the critical one

It is the single most important field in the whole contract, because two things
depend on it and neither can be faked:

1. `verify_claim(quote, page, full_text_by_page)` — Feature 4B cannot run without it.
2. The evidence highlighter locates quotes against it.

Requirements:

- **Keys are 1-based page numbers.** `int` keys preferred; string keys (`"1"`)
  are coerced. A list is accepted and treated as 1-indexed.
- **Do not strip the text.** Keep the raw `page.get_text()` output. Collapsing
  whitespace, de-hyphenating or removing headers/footers *lowers* fuzzy-match
  scores and makes genuinely-correct quotes look paraphrased.
- **Every page present**, including references and appendices. Off-by-one page
  attribution is checked against neighbours, so gaps cause spurious misses.

Degradation: without it, verification and highlighting are both off. The PDF is
still browsable. This is the one failure that hurts everywhere.

---

## Member 2 — `generate_brief(document) -> dict`

**Where I look for you:** `ai.summarizer`, `briefing.generate_brief`, `briefing`, `ai`

Preferred — section name to list of claims:

```python
{
  "Main contribution": [
    {
      "text":  "Self-attention replaces recurrence entirely.",   # the claim
      "quote": "…dispensing with recurrence and convolutions entirely.",
      "page":  3,
      "match_score": 0.94,        # optional — I verify if absent
      "status": "verified",       # optional — I derive from score if absent
    },
  ],
  "Methodology": [...], "Results": [...], "Limitations": [...],
}
```

Also accepted: one claim object per section (not a list); a flat
`[{...}, {...}]` list where each claim carries its own `"section"`.

- `text` aliases: `claim`, `statement`, `content`, `body`
- `quote` aliases: `supporting_quote`, `evidence_quote`, `candidate_quote`, `span`
- `page` aliases: `claimed_page`, `page_number`, `page_no`
- Nesting evidence one level down (`"evidence": {"quote":…, "page":…}`) works too.

Section names are matched case-insensitively against the PRD Feature 3 set
(Main contribution / Methodology / Results / Limitations / Prerequisites) and
sorted into that order; anything else renders after them.

**`quote` must be text that actually appears on `page`.** The whole product rests
on this. If you paraphrase into the `quote` field it will score 0.6-0.9 and badge
as Paraphrased, which is the honest outcome — put your prose in `text` and a
verbatim span in `quote`.

Degradation: summary panel shows a scoped "unavailable" message. Everything else
works.

---

## Member 3 — chat and verification ✅ LANDED

Documented from the shipped code, not proposed. Three things here surprised the
dashboard and are worth knowing if you touch this seam.

### `build_index(chunks, full_text_by_page=None, *, client=None) -> DocIndex`

Live at `rag.embeddings`. Wrapped in `st.cache_resource` per your note, keyed on
the document content hash.

**It takes `chunks`, not a document.** That tripped the first version of the
adapter, which offered only the parsed document. `run_build_index` now passes
`chunks` and `full_text_by_page` separately.

Its annotation is `chunks: list[dict]` with keys
`{"chunk_id", "text", "page", "section"}`, but the shared
`utils.models.ParsedDocument.chunks` is a list of `Chunk` **dataclasses**. Someone
has to bridge that; `contracts.to_chunks()` does it on the way in. Worth agreeing
with Member 1 which one is authoritative.

When no chunks exist yet (Member 1 hasn't landed), `to_chunks` synthesises one per
page from `full_text_by_page` so real semantic retrieval still runs. Whole-page
chunks are coarse, but better than a dead chat panel.

### `ask_question(query, doc_index, *, client=None) -> dict`

Live at `rag.chat`. Returns the `ChatAnswer` contract:
`{answer, quote, page, confidence, status, match_score}`. The dashboard reads all
six.

One request: **please add `no_evidence: bool`** to that dict. It is currently
inferred from an empty answer, which works but is fragile — and the PRD's demo
deliberately asks an unanswerable question, so that path gets its own designed
state (a calm "not enough evidence" card, not an error).

### `verify_claim(candidate_quote, claimed_page, full_text_by_page, *, fallback_search=True) -> VerifiedClaim`

Live at `verification.verifier`. Returns `utils.models.VerifiedClaim` with
`.status`, `.match_score`, `.page`, `.quote`, `.matched_text`, `.char_start`,
`.char_end`.

**`.match_score` is on a 0–100 scale**, while the PRD's worked examples are 0–1
(`"match_score": 0.94`). `contracts._as_float` normalises anything > 1 by dividing
by 100, so both work — but the two documents disagree and it is worth settling.

`.page` is read and **preferred over the claimed page**: if you locate the text one
page off, the viewer follows you rather than the claim.

`.char_start` / `.char_end` are populated, which is what the PRD asked for. The
dashboard does **not** use them — `integration/highlight.py` locates the span
geometrically with PyMuPDF instead, because offsets into extracted text cannot be
converted to page coordinates without re-deriving the layout anyway. No action
needed; just so you know they aren't wasted effort being ignored silently.

`verify_claims_batch(claims, full_text_by_page)` is used when present. The
dashboard calls `verify_claim` as a **backstop** for any claim or chat answer that
arrives unscored, so nothing unbadged can reach the screen; if you've already
verified, it does not re-verify.

---

## Accepted field spellings (applies to everyone)

Whatever you return — dict, dataclass, or object — these are all read
interchangeably, so you rarely need to match a name exactly.

| Meaning | Names accepted |
|---|---|
| claim text | `text`, `claim`, `statement`, `content`, `body`, `summary` |
| quote | `quote`, `supporting_quote`, `evidence_quote`, `candidate_quote`, `span` |
| page | `page`, `claimed_page`, `page_number`, `page_no` |
| score | `match_score`, `score`, `similarity`, `confidence_score` (0–1 or 0–100) |
| status | `status`, `verification`, `verification_status`, `badge` |
| section | `section`, `heading`, `title`, `name`, `label` |
| answer | `answer`, `response`, `text`, `content` |
| page text | `full_text_by_page`, `text_by_page`, `pages_text`, `pages`, `page_text` |

Status values are matched loosely: `verified`/`confirmed`/`exact`/`True`,
`paraphrased`/`partial`, `unsupported`/`not_found`/`False`, plus the ✅/⚠/❌
glyphs. Unrecognised values fall back to the score. An explicit status **wins over**
the derived one, so a deliberate downgrade (the PRD's "re-attempt retrieval once,
then downgrade and label clearly") is respected.

Evidence may also be nested one level down (`"evidence": {"quote": …, "page": …}`).

Full lists: `contracts.py` (`_TEXT_KEYS` etc.) and `adapters._SYNONYMS` for
parameter names.

---

## Member 4 — citations ✅ LANDED

Documented from the shipped code. Four things needed adapter work; noting them
because they are the kind of mismatch that fails *silently*.

### `explore_citations(parsed_doc, paper_context="", cache=None)`

Live at `citations.explorer`. Three surprises:

1. **The name.** The team contract says `analyze_references`; the module exports
   `explore_citations`. Both are now in the resolver's lookup list — but until they
   were, the dashboard silently used its own stub while your real module sat right
   there. Worth a look at whether the contract or the code should move.
2. **It's `async def`.** Streamlit's script runner is synchronous, so
   `adapters._call` detects a coroutine and bridges it (`asyncio.run`, or a
   worker thread if a loop is already running). No panel needs to know.
3. **It returns `(list[EnrichedCitation], stats_dict)`** — a two-tuple, not a
   list. Iterating it directly yields two junk references (one from the list, one
   from the stats dict), which is exactly what happened first time.
   `contracts._unwrap_result_and_stats` now unwraps it.

`EnrichedCitation` nests the interesting fields one level down —
`metadata: PaperMetadata`, `purpose: CitationPurpose` — so `contracts._to_reference`
flattens them. Nested values win over top-level ones, since a resolved Semantic
Scholar title beats the raw string it was parsed from. When a lookup does not
resolve, the UI falls back to `raw_text`, trimmed to roughly the title clause.

**One thing to consider:** `RawReference` carries `title`, `authors_raw` and `year`
from local parsing, but `EnrichedCitation` does not surface them — so when
OpenAlex rate-limits, the year and a clean title are lost even though the
extractor found them. Carrying them onto `EnrichedCitation` would make the
degraded state noticeably better.

**And a performance note:** `explore_citations` fans out live lookups during a
render pass. Rate-limited, with retries, that can take a while; the dashboard
caches per document and shows a spinner, but the first open of the Citations panel
is the slowest thing in the app.

### `review(document) -> dict` and the reference shape below

Still the preferred-shape proposal — `reviewer` has not landed.

**A request for Member 1:** please include a `references_text` key (the whole
reference block as one string) or give each section entry a `"text"` field. The
extractor looks for a section literally named "References" and reads its `text`;
handing over only the page the heading sits on truncates a multi-page reference
list badly. The local fallback parser emits `references_text`, which took
extraction on the demo paper from 4 references to 40.

### `analyze_references(document) -> list[dict]` (alternative shape, still accepted)

```python
[{"title": …, "authors": "Vaswani et al.", "year": "2017",
  "citation_count": 103000, "abstract": …, "purpose": "why it was cited",
  "url": …, "from_cache": True}]
```

`authors` may be a list of strings or of `{"name": …}` dicts. A bare list of
title strings works. `citation_count` aliases: `citationCount`, `cited_by`.

Set `from_cache: True` when metadata came from your local fallback cache — the UI
labels it, which keeps us honest about a live-API hiccup instead of silently
presenting stale data as fresh.

Missing `citation_count`/`abstract`/`purpose` render as explicitly unfetched.
**Never substitute a placeholder number** — a fabricated citation count in a tool
about verifiability is worse than a blank.

Degradation: scoped "Citation data unavailable". Everything else works.

### `review(document) -> dict` — optional

**Where I look for you:** `reviewer.reviewer` (`review`, `review_paper`, or
`reviewer_mode`), `reviewer`

```python
{
  "reproducibility_score": 8.1,
  "checks": [{"name": "Dataset", "present": True}, …],
  "findings": [{"kind": "weakness", "text": …, "quote": …, "page": 9}],
  "consistency": [{"text": "Abstract claims SOTA; results show…",
                   "quote": …, "page": 9}],
}
```

`kind` is one of `strength`, `weakness`, `missing`, `consistency`. Findings may
carry `quote`/`page`, and get a badge and a jump-to-evidence button like any other
claim.

Not in the five-function contract and first in the PRD's cut order, so the panel
says "not available" and nothing else notices.

---

## Running before anyone else has landed

`integration/stubs.py` provides local, offline stand-ins for all of the above.
They do real work rather than returning canned data — real PyMuPDF parsing, real
`rapidfuzz` verification, real extractive retrieval — so **claim → page-jump →
highlight works end to end today with no other module present and no
`OPENAI_API_KEY`.**

They are not a substitute for your work: the brief is extractive rather than
abstractive, retrieval is lexical rather than semantic, and no citation metadata
is fetched. The UI discloses this in a "Running with local fallbacks" expander,
and the **Modules** popover in the nav rail shows exactly which functions are
live versus stubbed.

### Dropping your module in

1. Put your file where the PRD §13 layout says (e.g. `verification/verifier.py`).
2. Overwrite the placeholder `__init__.py` in your package — it exists only so
   imports resolve before you arrive, and holds no logic.
3. **Restart the Streamlit server.** Import resolution is cached in
   `st.cache_resource`, so a hot reload will not pick up a newly-importable
   module.
4. Check the **Modules** popover: your name should flip from grey to green.

No file in `ui/` needs to change.
