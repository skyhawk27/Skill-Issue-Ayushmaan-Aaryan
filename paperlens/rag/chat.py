"""
Chat orchestrator for PaperLens grounded Q&A.

This is the main entry point other members call:

    answer = ask_question(query, doc_index)
    # answer is a dict matching the ChatAnswer contract

Flow
----
1. Retrieve top-k relevant chunks via FAISS.
2. If nothing above MIN_SIMILARITY_THRESHOLD → return "unsupported"
   immediately, no LLM call.
3. Build context block, call GPT-4o with the grounded-chat system prompt.
4. Parse JSON response defensively (strip markdown fences, retry once on
   malformed output).
5. If LLM self-reports "unsupported" → return as-is, skip verification.
6. Otherwise → verify the LLM's quote against actual page text via
   Feature 4B.  The fuzzy-match verdict OVERRIDES the LLM's self-reported
   status, since the model cannot be trusted to know whether its own quote
   is accurate.
7. Return the final ChatAnswer dict.
"""

from __future__ import annotations

import json
import re
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from typing import Any

from openai import OpenAI

from rag.embeddings import DocIndex
from rag.retriever import retrieve
from rag.prompts import CHAT_SYSTEM_PROMPT, JSON_RETRY_PROMPT, build_context_block
from verification.verifier import verify_claim
from utils.models import ChatAnswer


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CHAT_MODEL: str = "gpt-4o"
MAX_RETRIES: int = 2           # retries on OpenAI rate-limit / transient errors
RETRY_BACKOFF_S: float = 1.0   # seconds between retries
RETRIEVAL_K: int = 5           # top-k chunks to feed to the LLM


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def ask_question(
    query: str,
    doc_index: DocIndex,
    *,
    client: OpenAI | None = None,
) -> dict:
    """
    Answer a user question with grounded, verified evidence.

    Parameters
    ----------
    query:
        Natural-language question from the user.
    doc_index:
        The ``DocIndex`` produced by ``build_index()``.
    client:
        Optional pre-configured ``OpenAI`` client.

    Returns
    -------
    Dict matching the ChatAnswer contract::

        {
            "answer": str,
            "quote": str,
            "page": int | None,
            "confidence": "High" | "Medium" | "Low",
            "status": "verified" | "paraphrased" | "unsupported",
            "match_score": float,
        }
    """
    if client is None:
        client = OpenAI()

    # ------------------------------------------------------------------
    # Step 1: Retrieve
    # ------------------------------------------------------------------
    retrieved = retrieve(query, doc_index, k=RETRIEVAL_K, client=client)

    # ------------------------------------------------------------------
    # Step 2: Early exit if retrieval found nothing relevant
    # ------------------------------------------------------------------
    if not retrieved:
        return ChatAnswer(
            answer="The uploaded paper does not provide enough evidence "
                   "to answer this question.",
            quote="",
            page=None,
            confidence="Low",
            status="unsupported",
            match_score=0.0,
        ).to_dict()

    # ------------------------------------------------------------------
    # Step 3: Build prompt and call LLM
    # ------------------------------------------------------------------
    context_block = build_context_block(retrieved)
    user_message = (
        f"CONTEXT CHUNKS:\n{context_block}\n\n"
        f"QUESTION:\n{query}"
    )

    raw_response = _call_llm(client, user_message)

    # ------------------------------------------------------------------
    # Step 4: Parse JSON defensively
    # ------------------------------------------------------------------
    parsed = _parse_json_response(raw_response)

    if parsed is None:
        # Retry once with an explicit nudge.
        retry_msg = f"{user_message}\n\n{JSON_RETRY_PROMPT}"
        raw_retry = _call_llm(client, retry_msg)
        parsed = _parse_json_response(raw_retry)

    if parsed is None:
        # Two failures — return a safe fallback.
        return ChatAnswer(
            answer="I was unable to generate a properly formatted answer. "
                   "Please try rephrasing your question.",
            quote="",
            page=None,
            confidence="Low",
            status="unsupported",
            match_score=0.0,
        ).to_dict()

    # ------------------------------------------------------------------
    # Step 5: If LLM says "unsupported", trust that and skip verification
    # ------------------------------------------------------------------
    llm_status = parsed.get("status", "answered")
    if llm_status == "unsupported":
        return ChatAnswer(
            answer=parsed.get("answer", "The uploaded paper does not "
                              "provide enough evidence to answer this question."),
            quote="",
            page=None,
            confidence="Low",
            status="unsupported",
            match_score=0.0,
        ).to_dict()

    # ------------------------------------------------------------------
    # Step 6: Verify the quote against actual page text (Feature 4B)
    # ------------------------------------------------------------------
    candidate_quote = parsed.get("quote", "")
    claimed_page = parsed.get("page")

    verified = verify_claim(
        candidate_quote=candidate_quote,
        claimed_page=claimed_page,
        full_text_by_page=doc_index.full_text_by_page,
    )

    # The verification verdict OVERRIDES the LLM's self-reported status.
    return ChatAnswer(
        answer=parsed.get("answer", ""),
        quote=candidate_quote,
        page=verified.page if verified.page is not None else claimed_page,
        confidence=parsed.get("confidence", "Medium"),
        status=verified.status,
        match_score=verified.match_score,
    ).to_dict()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _call_llm(
    client: OpenAI,
    user_message: str,
) -> str:
    """
    Call GPT-4o with retry/backoff for rate-limit resilience during a live
    demo.
    """
    last_err: Exception | None = None

    for attempt in range(MAX_RETRIES + 1):
        try:
            response = client.chat.completions.create(
                model=CHAT_MODEL,
                messages=[
                    {"role": "system", "content": CHAT_SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                temperature=0.0,  # deterministic for grounded answers
                max_tokens=1024,
            )
            return response.choices[0].message.content or ""
        except Exception as exc:
            last_err = exc
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_S * (attempt + 1))

    # All retries exhausted.
    raise RuntimeError(
        f"OpenAI API call failed after {MAX_RETRIES + 1} attempts: {last_err}"
    ) from last_err


def _parse_json_response(raw: str) -> dict[str, Any] | None:
    """
    Attempt to parse the LLM's raw text as JSON.

    Handles the common failure mode where the model wraps JSON in markdown
    code fences despite being told not to.
    """
    if not raw:
        return None

    # Strip markdown code fences if present.
    cleaned = raw.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    cleaned = cleaned.strip()

    try:
        obj = json.loads(cleaned)
        if isinstance(obj, dict):
            return obj
    except (json.JSONDecodeError, ValueError):
        pass

    # Try to find a JSON object anywhere in the string.
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        try:
            obj = json.loads(match.group())
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, ValueError):
            pass

    return None
