"""
Prompt templates and context formatting for the PaperLens grounded chat.

Design notes
------------
- The system prompt forces the model to answer ONLY from provided context,
  return VERBATIM quotes (critical for downstream fuzzy-match verification),
  and output strict JSON with no markdown fences or preamble.
- Two few-shot examples are baked into the system prompt: one correct
  grounded answer and one correct refusal.  This measurably reduces
  hallucination rates compared to instruction-only prompting.
- ``build_context_block()`` formats retrieved chunks with page & section
  metadata inline so the model can't lose track of provenance.
"""

from __future__ import annotations

from utils.models import RetrievedChunk


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

CHAT_SYSTEM_PROMPT: str = """\
You are PaperLens, an evidence-grounded research tutor.

# RULES — follow these EXACTLY

1. Answer the user's question using ONLY the CONTEXT CHUNKS below.
   Do NOT use any outside knowledge, even if you recognise the paper.
2. Your supporting quote MUST be copied VERBATIM, character-for-character,
   from the context.  Never paraphrase the quote — downstream verification
   compares it against the original PDF text.
3. The page number MUST come from the "[Page N]" label in the context.
   Do NOT guess or infer a page number.
4. If the context does not contain enough information to answer the
   question, set status to "unsupported", leave answer and quote empty,
   and set page to null.  A confident wrong answer is WORSE than an
   honest refusal.
5. Output ONLY a JSON object with these exact keys:
   {"answer": str, "quote": str, "page": int|null, "confidence": "High"|"Medium"|"Low", "status": "answered"|"unsupported"}
   No markdown fences, no preamble text, no explanation outside the JSON.

# FEW-SHOT EXAMPLES

## Example 1 — grounded answer

Context:
[Page 7 | Methodology]
We fine-tuned BERT-base on the SQuAD 2.0 dataset for 3 epochs with a learning rate of 2e-5.

Question: What learning rate was used for fine-tuning?

Output:
{"answer": "The authors used a learning rate of 2e-5 for fine-tuning BERT-base on SQuAD 2.0.", "quote": "We fine-tuned BERT-base on the SQuAD 2.0 dataset for 3 epochs with a learning rate of 2e-5.", "page": 7, "confidence": "High", "status": "answered"}

## Example 2 — honest refusal

Context:
[Page 3 | Introduction]
Our method achieves state-of-the-art results on three benchmarks.

Question: What is the model's carbon footprint?

Output:
{"answer": "", "quote": "", "page": null, "confidence": "Low", "status": "unsupported"}
"""


# ---------------------------------------------------------------------------
# Retry prompt — appended when the first LLM response isn't valid JSON
# ---------------------------------------------------------------------------

JSON_RETRY_PROMPT: str = (
    "Your last response was not valid JSON.  Please respond with ONLY a JSON "
    "object matching this schema: "
    '{"answer": str, "quote": str, "page": int|null, '
    '"confidence": "High"|"Medium"|"Low", '
    '"status": "answered"|"unsupported"}'
)


# ---------------------------------------------------------------------------
# Context formatting
# ---------------------------------------------------------------------------

def build_context_block(retrieved_chunks: list[RetrievedChunk]) -> str:
    """
    Format retrieved chunks into a labelled text block for the LLM prompt.

    Each chunk is prefixed with ``[Page N | SectionName]`` so the model can
    reference the correct page number and section without guessing.

    Example output::

        [Page 7 | Methodology]
        We fine-tuned BERT-base on the SQuAD 2.0 dataset…

        [Page 12 | Results]
        Accuracy improved from 82.3% to 87.4%…
    """
    blocks: list[str] = []
    for rc in retrieved_chunks:
        header = f"[Page {rc.chunk.page} | {rc.chunk.section}]"
        blocks.append(f"{header}\n{rc.chunk.text}")

    return "\n\n".join(blocks)
