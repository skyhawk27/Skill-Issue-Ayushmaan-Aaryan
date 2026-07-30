"""
Vector index builder for PaperLens RAG.

Embeds document chunks via OpenAI text-embedding-3-small and stores them in a
FAISS IndexFlatIP index.  Every vector is L2-normalised before insertion so
that inner-product search is equivalent to cosine similarity.

Usage
-----
    doc_index = build_index(chunks, full_text_by_page)
    # doc_index is safe to cache with st.cache_resource — no mutable global state.
"""

from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dataclasses import dataclass, field

import numpy as np
import faiss
from openai import OpenAI

from utils.models import Chunk


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

EMBEDDING_MODEL: str = "text-embedding-3-small"
EMBEDDING_DIM: int = 1536  # dimension for text-embedding-3-small
EMBEDDING_BATCH_SIZE: int = 64  # max chunks per API call


# ---------------------------------------------------------------------------
# DocIndex — the object returned by build_index()
# ---------------------------------------------------------------------------

@dataclass
class DocIndex:
    """
    Holds everything needed for retrieval + verification after initial
    processing.

    Designed to be immutable after creation so it's safe for
    ``st.cache_resource``.
    """
    index: faiss.Index                         # FAISS IndexFlatIP
    chunks: list[Chunk]                        # parallel list; chunks[i] ↔ index row i
    full_text_by_page: dict[int, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_index(
    chunks: list[dict],
    full_text_by_page: dict[int, str] | None = None,
    *,
    client: OpenAI | None = None,
) -> DocIndex:
    """
    One-time setup.  Embeds *chunks*, builds a FAISS index, returns a
    ``DocIndex`` ready for retrieval.

    Parameters
    ----------
    chunks:
        List of chunk dicts matching the Member 1 contract::

            {"chunk_id": str, "text": str, "page": int, "section": str}

    full_text_by_page:
        Mapping ``{page_number: full_page_text}`` used by the verification
        pipeline.  If ``None``, an empty dict is stored (verification will
        degrade gracefully).
    client:
        Optional pre-configured ``OpenAI`` client.  If ``None`` a default
        client is created (reads ``OPENAI_API_KEY`` from env).
    """
    if client is None:
        client = OpenAI()

    # Materialise Chunk dataclasses from raw dicts.
    chunk_objects = [
        Chunk(
            chunk_id=c.get("chunk_id", f"chunk_{i}"),
            text=c["text"],
            page=c["page"],
            section=c.get("section", ""),
        )
        for i, c in enumerate(chunks)
    ]

    # Embed in batches.
    all_embeddings: list[list[float]] = []
    for batch_start in range(0, len(chunk_objects), EMBEDDING_BATCH_SIZE):
        batch = chunk_objects[batch_start : batch_start + EMBEDDING_BATCH_SIZE]
        texts = [ck.text for ck in batch]
        response = client.embeddings.create(model=EMBEDDING_MODEL, input=texts)
        all_embeddings.extend([d.embedding for d in response.data])

    # Convert to numpy, L2-normalise, add to FAISS.
    matrix = np.array(all_embeddings, dtype=np.float32)
    faiss.normalize_L2(matrix)  # in-place; mandatory for cosine-like search

    index = faiss.IndexFlatIP(matrix.shape[1])
    index.add(matrix)

    return DocIndex(
        index=index,
        chunks=chunk_objects,
        full_text_by_page=full_text_by_page or {},
    )


def embed_query(
    query: str,
    *,
    client: OpenAI | None = None,
) -> np.ndarray:
    """
    Embed a single query string and return an L2-normalised vector.

    Separated from ``build_index`` so the retriever can call it without
    re-importing the whole embedding pipeline.
    """
    if client is None:
        client = OpenAI()

    response = client.embeddings.create(model=EMBEDDING_MODEL, input=[query])
    vec = np.array([response.data[0].embedding], dtype=np.float32)
    faiss.normalize_L2(vec)  # same normalisation as corpus
    return vec
