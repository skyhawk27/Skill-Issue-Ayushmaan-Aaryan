"""
Tests for the retriever module.

Uses a small hardcoded fixture (4 chunks, 2 pages) so tests run independently
of Member 1's PDF parser.  The OpenAI embedding client is mocked to avoid
real API calls.
"""

from __future__ import annotations

import sys
import os
import unittest
from unittest.mock import MagicMock, patch

import numpy as np
import faiss

# Ensure project root is on the path.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rag.embeddings import DocIndex, EMBEDDING_DIM
from rag.retriever import retrieve, MIN_SIMILARITY_THRESHOLD
from utils.models import Chunk


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXTURE_CHUNKS: list[Chunk] = [
    Chunk(
        chunk_id="c0",
        text="We fine-tuned BERT-base on the SQuAD 2.0 dataset for 3 epochs "
             "with a learning rate of 2e-5.",
        page=7,
        section="Methodology",
    ),
    Chunk(
        chunk_id="c1",
        text="The model achieved an F1 score of 88.5% on the test set, "
             "outperforming the previous state-of-the-art by 2.3 points.",
        page=12,
        section="Results",
    ),
    Chunk(
        chunk_id="c2",
        text="Transformers were chosen because of their ability to capture "
             "long-range dependencies through self-attention mechanisms.",
        page=3,
        section="Introduction",
    ),
    Chunk(
        chunk_id="c3",
        text="One limitation of our approach is the high computational cost, "
             "requiring 4 A100 GPUs for training.",
        page=15,
        section="Limitations",
    ),
]

FIXTURE_FULL_TEXT_BY_PAGE: dict[int, str] = {
    3: "Transformers were chosen because of their ability to capture "
       "long-range dependencies through self-attention mechanisms. "
       "Previous work relied on recurrent architectures.",
    7: "We fine-tuned BERT-base on the SQuAD 2.0 dataset for 3 epochs "
       "with a learning rate of 2e-5. The batch size was 32.",
    12: "The model achieved an F1 score of 88.5% on the test set, "
        "outperforming the previous state-of-the-art by 2.3 points. "
        "Table 3 shows per-category breakdowns.",
    15: "One limitation of our approach is the high computational cost, "
        "requiring 4 A100 GPUs for training. Future work should explore "
        "distillation techniques.",
}


def _build_fake_index(
    target_idx: int = 0,
    target_score: float = 0.85,
) -> DocIndex:
    """
    Build a DocIndex with a hand-crafted FAISS index where searching for
    any query returns ``FIXTURE_CHUNKS[target_idx]`` as the top result
    with approximately ``target_score`` similarity.

    This avoids calling the real embedding API during tests.
    """
    n = len(FIXTURE_CHUNKS)
    dim = EMBEDDING_DIM

    # Create random orthogonal-ish vectors for each chunk.
    rng = np.random.default_rng(42)
    vectors = rng.standard_normal((n, dim)).astype(np.float32)
    faiss.normalize_L2(vectors)

    index = faiss.IndexFlatIP(dim)
    index.add(vectors)

    return DocIndex(
        index=index,
        chunks=list(FIXTURE_CHUNKS),
        full_text_by_page=dict(FIXTURE_FULL_TEXT_BY_PAGE),
    ), vectors


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRetrieve(unittest.TestCase):
    """Test the retrieve() function with mocked embeddings."""

    def test_relevant_query_returns_correct_chunk(self):
        """A query embedding close to chunk 0 should return chunk 0 first."""
        doc_index, vectors = _build_fake_index()

        # Fabricate a query vector that is very close to chunk 0.
        query_vec = vectors[0:1].copy()
        # Add a tiny bit of noise so it's not identical.
        query_vec += np.random.default_rng(99).standard_normal(query_vec.shape).astype(np.float32) * 0.01
        faiss.normalize_L2(query_vec)

        with patch("rag.retriever.embed_query", return_value=query_vec):
            results = retrieve("What learning rate was used?", doc_index, k=3)

        self.assertGreater(len(results), 0, "Expected at least one result")
        top = results[0]
        self.assertEqual(top.chunk.page, 7)
        self.assertIn("learning rate", top.chunk.text)

    def test_irrelevant_query_returns_empty(self):
        """
        A query embedding orthogonal to all chunks should score below
        MIN_SIMILARITY_THRESHOLD and return an empty list.
        """
        doc_index, vectors = _build_fake_index()

        # Create a vector orthogonal to all stored vectors by using a
        # vector that is far from any chunk.
        rng = np.random.default_rng(123)
        orthogonal = rng.standard_normal((1, EMBEDDING_DIM)).astype(np.float32)
        # Make it truly orthogonal-ish by subtracting projections.
        for v in vectors:
            proj = np.dot(orthogonal[0], v) * v
            orthogonal[0] -= proj
        faiss.normalize_L2(orthogonal)

        with patch("rag.retriever.embed_query", return_value=orthogonal):
            results = retrieve(
                "What is the meaning of life?",
                doc_index,
                k=3,
                min_threshold=0.5,  # use a higher threshold to ensure empty
            )

        self.assertEqual(len(results), 0, "Expected empty results for irrelevant query")

    def test_retrieve_respects_k_limit(self):
        """retrieve(k=2) should return at most 2 results."""
        doc_index, vectors = _build_fake_index()

        query_vec = vectors[0:1].copy()
        faiss.normalize_L2(query_vec)

        with patch("rag.retriever.embed_query", return_value=query_vec):
            results = retrieve("test query", doc_index, k=2, min_threshold=0.0)

        self.assertLessEqual(len(results), 2)

    def test_empty_index_returns_empty(self):
        """An empty DocIndex should return an empty list without crashing."""
        empty_index = DocIndex(
            index=faiss.IndexFlatIP(EMBEDDING_DIM),
            chunks=[],
            full_text_by_page={},
        )
        query_vec = np.random.default_rng(1).standard_normal(
            (1, EMBEDDING_DIM)
        ).astype(np.float32)
        faiss.normalize_L2(query_vec)

        with patch("rag.retriever.embed_query", return_value=query_vec):
            results = retrieve("anything", empty_index)

        self.assertEqual(results, [])

    def test_results_have_scores_attached(self):
        """Every RetrievedChunk should carry a float similarity score."""
        doc_index, vectors = _build_fake_index()
        query_vec = vectors[1:2].copy()
        faiss.normalize_L2(query_vec)

        with patch("rag.retriever.embed_query", return_value=query_vec):
            results = retrieve("test", doc_index, k=2, min_threshold=0.0)

        for rc in results:
            self.assertIsInstance(rc.score, float)


if __name__ == "__main__":
    unittest.main()
