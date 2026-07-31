# Known shortcomings — what a demo audience will actually notice

Written from the dashboard side, by running the app rather than reading the code.
Everything here is **visible on the surface** — things a judge or first-time user
hits within a couple of minutes. Internal code debt is deliberately out of scope.

Ordered by how likely someone is to notice, and how bad it looks when they do.

Last verified: 31 July 2026, on *Attention Is All You Need* (15 pages, 40 refs),
with `OPENALEX_API_KEY` and `NVIDIA_API_KEY` set.

---

## 1. Three advertised features do not work

The homepage catalogue lists nine features. Three of them do not do what the card
says, and all three are one click away.

| Card says | What actually happens |
|---|---|
| **Reviewer mode** — "An academic reviewer's read: strengths, weaknesses, missing baselines and a reproducibility score" | Keyword search. It greps the paper for "dataset", "github.com", "random seed", "gpu", "p-value" and scores 10 × hits ÷ 6. No model is involved. Nobody has written `reviewer/reviewer.py`. |
| **Consistency checker** — "Flags contradictions between what a paper claims and what its results actually show" | Returns an empty list, always. The panel says "No cross-section contradiction check has run for this paper." |
| **Research family tree** — "The paper's intellectual lineage, drawn as a graph" | Not rendered. Building it needs *this* paper resolved to an OpenAlex id, which does not happen for an arbitrary uploaded PDF. The panel explains this in a caption. |

**This is the biggest exposure.** A judge who opens Reviewer Mode sees a
reproducibility score that looks computed and is actually a keyword count.

**Options before demoing:** remove those three cards from the homepage, mark them
"planned", or don't open those panels. The first is a five-minute change.

---

## 2. Every citation count is missing

The Citations panel shows **"With metadata: 0"** on every paper, so the
most-cited-references chart never appears — you always get the year-histogram
fallback.

**Cause, confirmed by direct API testing:** OpenAlex uses `,` as the AND-separator
between filters, and the lookup interpolates the title straight into the filter:

```python
params = {"filter": f"title.search:{title}"}   # citations/openalex.py:124
```

Any title containing a comma is parsed as two filters, the second invalid, and
rejected at the API edge with HTTP 400. Reference titles contain commas
constantly, so this fails for essentially every reference.

| Title | Result |
|---|---|
| `Layer normalization` | 200 |
| `BERT: pre-training of deep…` | 200 (colons are fine) |
| `Adam, a method for stochastic optimization` | **400** |

**Fix:** strip or encode commas before interpolation — one line. The whole
citation-influence view lights up the moment it lands. The API key is valid; this
is not a quota problem.

---

## 3. Reference titles look scruffy

Because metadata never resolves (see above), titles fall back to text parsed out
of the PDF. They often carry author fragments:

> `Le. Massive exploration of neural machine translation architectures`

Readable, clearly not clean. Fixes itself once #2 is fixed, since OpenAlex returns
proper titles.

---

## 4. Some claims jump to a page with no highlight

Clicking a claim always moves the PDF, but roughly **1 in 6** shows *"This quote
could not be located on the page"* instead of a highlight.

**This is the product working, not failing.** The summarizer now writes
*abstractive* claims, so its quotes are not always verbatim in the paper — and
catching that is exactly what the verification pipeline is for. Those claims are
badged Paraphrased or Unsupported rather than Verified.

It still *looks* like a miss on stage. If you are demoing the click-to-evidence
moment, click a claim badged ✅ Verified — those locate reliably.

---

## 4b. A badge can say Verified while the viewer says "could not locate"

Occasionally a claim is badged ✅ Verified but the PDF pane reports it could not
find the quote on the page.

Both are telling the truth about different questions. Verification fuzzy-matches
the quote against the page's **extracted text**; highlighting has to find the span
**geometrically** on the rendered page. A quote inside a table, spanning two
columns, or carrying mangled ligatures can pass the first and fail the second.

Nothing is being overclaimed — the text really is on that page — but the pairing
looks inconsistent if you notice it. **How often varies a lot between runs**,
because the summarizer is a live model: measured runs have ranged from every
verified claim highlighting cleanly to under half of them. The test suite reports
the ratio each run rather than asserting a threshold, precisely because it is not
stable enough to gate on.

---

## 5. The PDF pane reloads on every claim click

Clicking a claim visibly re-renders the whole document. On a 15-page paper it is
a flicker; on a 60-page paper it is a noticeable pause.

**Cause:** the viewer component declares watchers on only `binary`, `zoom_level`
and `viewer_align` — nothing on `scroll_to_page`, `scroll_to_annotation` or
`annotations`. Passing a new scroll target for the same document does nothing at
all, so the only reliable way to make it move is to change the component key,
which forces a remount.

**Trade-off taken deliberately:** a working jump that flickers beats a smooth one
that does not move. Fixing it properly means patching the component upstream.

---

## 6. Chat needs a key, and pauses under load

- Without `NVIDIA_API_KEY` (or another provider), chat offers offline keyword
  search instead. Answers are then quoted from the paper rather than composed,
  and it misses questions phrased differently from the text.
- With a key, expect **1–2s** per question, occasionally longer: the free tier is
  40 requests/minute and the app paces itself to 28/min to stay under it. The
  summarizer alone fires 16 requests at once (one per page), so an early question
  right after upload can wait a beat.

---

## 7. Smaller things

- **Locked to light mode.** No dark toggle — a deliberate choice for a
  predictable look on a projector, but someone will ask.
- **Long papers:** the page index shows 20 thumbnails at a time behind a range
  selector, rather than all of them.
- **Prerequisites are usually empty** in the abstract unless the summarizer
  supplies them; the local fallback deliberately shows none rather than passing
  truncated sentences off as concept names.
- **`git clone` prints a warning.** A nested clone of this repo was committed as a
  gitlink (`Skill-Issue-Ayushmaan-Aaryan`, mode `160000`). Harmless, looks untidy.
  One `git rm --cached` to remove.

---

## What is genuinely solid

For balance — these were measured, not assumed:

- **Upload → dashboard in 13.4s** on a 15-page paper, against a 20–35s target.
- **Verification is real.** Every claim is fuzzy-matched against the actual page
  text; a fabricated quote is correctly badged Unsupported and produces no
  highlight. No claim is ever badged Verified while its quote cannot be located —
  that invariant is asserted in the test suite.
- **Honest refusals.** Ask something the paper does not cover and it says so
  rather than inventing an answer.
- **Graceful degradation.** Any module can fail and only its own panel goes down;
  the app runs end to end with no API keys at all.
- **178 automated checks** (132 dashboard + 46 team pytest) currently passing.
