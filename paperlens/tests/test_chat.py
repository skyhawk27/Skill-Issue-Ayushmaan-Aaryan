"""
Tests for the chat orchestrator (ask_question).

Covers:
- Grounded answer path with verification
- Unsupported query path (no LLM call when retrieval is empty)
- LLM self-reported unsupported (skip verification)
- Malformed / non-JSON LLM output handling
- Retry on first JSON failure

All OpenAI calls and retrieval are mocked — no real API calls in the test suite.
"""

from __future__ import annotations

import json
import sys
import os
import unittest
from unittest.mock import MagicMock, patch, PropertyMock

import numpy as np
import faiss

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rag.chat import ask_question, _parse_json_response
from rag.embeddings import DocIndex, EMBEDDING_DIM
from utils.models import Chunk, RetrievedChunk


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

FIXTURE_CHUNKS = [
    Chunk(
        chunk_id="c0",
        text="We fine-tuned BERT-base on the SQuAD 2.0 dataset for 3 epochs "
             "with a learning rate of 2e-5.",
        page=7,
        section="Methodology",
    ),
    Chunk(
        chunk_id="c1",
        text="The model achieved an F1 score of 88.5% on the test set.",
        page=12,
        section="Results",
    ),
]

FIXTURE_FULL_TEXT_BY_PAGE = {
    7: "We fine-tuned BERT-base on the SQuAD 2.0 dataset for 3 epochs "
       "with a learning rate of 2e-5. The batch size was 32.",
    12: "The model achieved an F1 score of 88.5% on the test set, "
        "outperforming the previous state-of-the-art by 2.3 points.",
}


def _make_doc_index() -> DocIndex:
    """Build a minimal DocIndex for testing (FAISS index won't actually be searched)."""
    n = len(FIXTURE_CHUNKS)
    vecs = np.random.default_rng(42).standard_normal((n, EMBEDDING_DIM)).astype(np.float32)
    faiss.normalize_L2(vecs)
    index = faiss.IndexFlatIP(EMBEDDING_DIM)
    index.add(vecs)
    return DocIndex(
        index=index,
        chunks=list(FIXTURE_CHUNKS),
        full_text_by_page=dict(FIXTURE_FULL_TEXT_BY_PAGE),
    )


def _mock_llm_response(content: str) -> MagicMock:
    """Create a mock OpenAI chat completion response."""
    mock_message = MagicMock()
    mock_message.content = content
    mock_choice = MagicMock()
    mock_choice.message = mock_message
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    return mock_response


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestAskQuestion(unittest.TestCase):
    """Integration-level tests for the ask_question() orchestrator."""

    @patch("rag.chat.retrieve")
    @patch("rag.chat.OpenAI")
    def test_grounded_answer_gets_verified(self, MockOpenAI, mock_retrieve):
        """
        When retrieval finds relevant chunks and the LLM produces a valid
        grounded answer, the response should include a verification status
        based on fuzzy matching — not the LLM's self-reported status.
        """
        # Arrange: retrieval returns chunk 0
        mock_retrieve.return_value = [
            RetrievedChunk(chunk=FIXTURE_CHUNKS[0], score=0.9)
        ]

        # LLM returns a valid grounded answer with a verbatim quote
        llm_answer = json.dumps({
            "answer": "The learning rate used was 2e-5.",
            "quote": "We fine-tuned BERT-base on the SQuAD 2.0 dataset for 3 epochs "
                     "with a learning rate of 2e-5.",
            "page": 7,
            "confidence": "High",
            "status": "answered",
        })
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _mock_llm_response(llm_answer)
        MockOpenAI.return_value = mock_client

        # Act
        doc_index = _make_doc_index()
        result = ask_question("What learning rate was used?", doc_index)

        # Assert: verification should override LLM's "answered" → "verified"
        self.assertIn(result["status"], ("verified", "paraphrased"))
        self.assertGreater(result["match_score"], 0)
        self.assertEqual(result["page"], 7)
        self.assertIn("2e-5", result["answer"])

    @patch("rag.chat.retrieve")
    def test_empty_retrieval_returns_unsupported_no_llm_call(self, mock_retrieve):
        """
        When retrieval returns nothing (below threshold), ask_question()
        should return "unsupported" WITHOUT calling the LLM at all.
        """
        mock_retrieve.return_value = []

        doc_index = _make_doc_index()
        # We don't even pass a real OpenAI client — if it tried to call
        # the LLM, it would crash.  That's the point of this test.
        result = ask_question(
            "What is the meaning of life?",
            doc_index,
            client=MagicMock(),  # won't be used
        )

        self.assertEqual(result["status"], "unsupported")
        self.assertEqual(result["quote"], "")
        self.assertIsNone(result["page"])
        self.assertEqual(result["match_score"], 0.0)

    @patch("rag.chat.retrieve")
    @patch("rag.chat.OpenAI")
    def test_llm_self_reported_unsupported_skips_verification(
        self, MockOpenAI, mock_retrieve
    ):
        """
        When the LLM itself says "unsupported", we trust that and skip
        verification (there's no quote to verify).
        """
        mock_retrieve.return_value = [
            RetrievedChunk(chunk=FIXTURE_CHUNKS[0], score=0.9)
        ]

        llm_answer = json.dumps({
            "answer": "",
            "quote": "",
            "page": None,
            "confidence": "Low",
            "status": "unsupported",
        })
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _mock_llm_response(llm_answer)
        MockOpenAI.return_value = mock_client

        doc_index = _make_doc_index()
        result = ask_question("What is the carbon footprint?", doc_index)

        self.assertEqual(result["status"], "unsupported")
        self.assertEqual(result["match_score"], 0.0)

    @patch("rag.chat.retrieve")
    @patch("rag.chat.OpenAI")
    def test_malformed_json_triggers_retry(self, MockOpenAI, mock_retrieve):
        """
        If the LLM returns non-JSON on the first attempt, the orchestrator
        should retry once with a nudge.
        """
        mock_retrieve.return_value = [
            RetrievedChunk(chunk=FIXTURE_CHUNKS[0], score=0.9)
        ]

        # First call: garbage.  Second call: valid JSON.
        valid_json = json.dumps({
            "answer": "The learning rate was 2e-5.",
            "quote": "with a learning rate of 2e-5",
            "page": 7,
            "confidence": "High",
            "status": "answered",
        })
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = [
            _mock_llm_response("Sure! The learning rate was..."),  # bad
            _mock_llm_response(valid_json),  # good on retry
        ]
        MockOpenAI.return_value = mock_client

        doc_index = _make_doc_index()
        result = ask_question("What learning rate?", doc_index)

        # Should have called the LLM twice.
        self.assertEqual(mock_client.chat.completions.create.call_count, 2)
        # And should have a real answer, not the fallback.
        self.assertNotEqual(result["status"], "unsupported")

    @patch("rag.chat.retrieve")
    @patch("rag.chat.OpenAI")
    def test_double_malformed_json_returns_fallback(self, MockOpenAI, mock_retrieve):
        """
        If both attempts produce non-JSON, return a safe fallback rather
        than crashing.
        """
        mock_retrieve.return_value = [
            RetrievedChunk(chunk=FIXTURE_CHUNKS[0], score=0.9)
        ]

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = [
            _mock_llm_response("I don't know how to JSON"),
            _mock_llm_response("Still not JSON :("),
        ]
        MockOpenAI.return_value = mock_client

        doc_index = _make_doc_index()
        result = ask_question("test?", doc_index)

        self.assertEqual(result["status"], "unsupported")
        self.assertIn("unable", result["answer"].lower())


class TestParseJsonResponse(unittest.TestCase):
    """Unit tests for the defensive JSON parser."""

    def test_clean_json(self):
        raw = '{"answer": "test", "status": "answered"}'
        result = _parse_json_response(raw)
        self.assertIsNotNone(result)
        self.assertEqual(result["answer"], "test")

    def test_json_with_markdown_fences(self):
        raw = '```json\n{"answer": "test", "status": "answered"}\n```'
        result = _parse_json_response(raw)
        self.assertIsNotNone(result)
        self.assertEqual(result["answer"], "test")

    def test_json_with_preamble(self):
        raw = 'Here is the answer:\n{"answer": "test", "status": "answered"}'
        result = _parse_json_response(raw)
        self.assertIsNotNone(result)
        self.assertEqual(result["answer"], "test")

    def test_complete_garbage_returns_none(self):
        result = _parse_json_response("This is not JSON at all.")
        self.assertIsNone(result)

    def test_empty_string_returns_none(self):
        result = _parse_json_response("")
        self.assertIsNone(result)

    def test_none_input_returns_none(self):
        result = _parse_json_response(None)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
