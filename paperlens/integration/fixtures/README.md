# Test fixtures

The dashboard's tests and the recommended demo run against a real academic paper.
PDFs are **not committed** — they are third-party works, and redistributing them
through this repo is not ours to do. Download it once:

```bash
curl -L -o paperlens/integration/fixtures/attention-is-all-you-need.pdf \
  https://arxiv.org/pdf/1706.03762v7
```

That is *Attention Is All You Need* (Vaswani et al., 2017) — 15 pages, a clean
two-column layout with a numbered reference list, which exercises section
detection, quote highlighting across line breaks, and reference extraction.

`paperlens/tests/test_dashboard.py` expects it at exactly that path.

Any PDF works for manual testing — drop one on the upload screen. The
repository's own `PaperLens_PRD_v2.md.pdf` is a useful second case, since it is a
converted Markdown document rather than a typeset paper and therefore stresses
different parsing paths.
