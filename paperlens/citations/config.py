"""
citations/config.py — Centralised configuration for the Citation Intelligence subsystem.

All tuneable values live here so they can be overridden via environment
variables without touching code.
"""

from __future__ import annotations

import os
from pathlib import Path


# ---------------------------------------------------------------------------
# Semantic Scholar API
# ---------------------------------------------------------------------------

S2_API_BASE: str = "https://api.semanticscholar.org/graph/v1"
S2_API_KEY: str | None = os.getenv("S2_API_KEY")

# Fields we request from Semantic Scholar for paper details
S2_PAPER_FIELDS: str = (
    "title,authors,year,abstract,citationCount,url,"
    "venue,fieldsOfStudy,openAccessPdf"
)

# Rate-limit / resilience
S2_TIMEOUT_S: float = 10.0
S2_MAX_RETRIES: int = 3
S2_RETRY_BACKOFF_S: float = 1.0  # base for exponential backoff
S2_CONCURRENCY_LIMIT: int = 5  # max parallel requests (semaphore)

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

CACHE_DIR: Path = Path(__file__).resolve().parent / ".cache"

# ---------------------------------------------------------------------------
# OpenAI / LLM
# ---------------------------------------------------------------------------

OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4.1")
OPENAI_TEMPERATURE: float = 0.3
OPENAI_MAX_TOKENS: int = 1024
PURPOSE_BATCH_SIZE: int = 5  # references per LLM call

# ---------------------------------------------------------------------------
# Family Tree
# ---------------------------------------------------------------------------

FAMILY_TREE_MAX_REFS: int = 10  # top-N most-cited refs per level
FAMILY_TREE_MAX_DEPTH: int = 2
