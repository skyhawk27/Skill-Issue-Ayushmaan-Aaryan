"""
Semantic retriever for PaperLens RAG.

Searches the FAISS index for the top-k chunks most similar to a user query.
Includes a MIN_SIMILARITY_THRESHOLD gate — if nothing relevant is found,
returns an empty list so that the chat layer can give an honest "not enough
evidence" answer *without even calling the LLM*.
"""

from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from openai import OpenAI

from rag.embeddings import DocIndex, embed_query
from utils.models import RetrievedChunk


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# If the best result scores below this threshold the retrieval is considered
# empty.  On a normalised inner-product index (cosine similarity), 0.25 is a
# reasonable "probably irrelevant" cutoff.  Tune if needed during integration.
MIN_SIMILARITY_THRESHOLD: float = 0.25

DEFAULT_K: int = 5


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def retrieve(
    query: str,
    doc_index: DocIndex,
    *,
    k: int = DEFAULT_K,
    min_threshold: float = MIN_SIMILARITY_THRESHOLD,
    client: OpenAI | None = None,
) -> list[RetrievedChunk]:
    """
    Embed *query*, search the FAISS index, return top-k chunks above the
    similarity threshold.

    Parameters
    ----------
    query:
        Natural-language question from the user.
    doc_index:
        The ``DocIndex`` produced by ``build_index()``.
    k:
        Maximum number of results to return.
    min_threshold:
        Minimum cosine similarity for the *top-1* result.  If the best
        match is below this, the entire result list is empty — the chat
        layer should treat this as "no relevant evidence found".
    client:
        Optional OpenAI client (passed through to ``embed_query``).

    Returns
    -------
    List of ``RetrievedChunk`` objects, sorted by descending similarity.
    May be empty if nothing exceeds ``min_threshold``.
    """
    if not doc_index.chunks:
        return []

    # Clamp k to actual corpus size.
    k = min(k, len(doc_index.chunks))

    query_vec = embed_query(query, client=client)
    scores, indices = doc_index.index.search(query_vec, k)

    # scores and indices are 2-D arrays of shape (1, k).
    scores_flat = scores[0]
    indices_flat = indices[0]

    # Gate: if best result is below threshold, return nothing.
    if scores_flat[0] < min_threshold:
        return []

    results: list[RetrievedChunk] = []
    for score, idx in zip(scores_flat, indices_flat):
        if idx < 0:
            # FAISS returns -1 when there are fewer vectors than k.
            continue
        if score < min_threshold:
            # Only include results above the threshold.
            break
        results.append(
            RetrievedChunk(
                chunk=doc_index.chunks[idx],
                score=float(score),
            )
        )

    return results
