"""
Shared data models for PaperLens.

All modules import from here so that the contract between members is explicit.
Add new dataclasses as needed — do NOT remove existing ones, other members
depend on them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Member 1 contract — Document Intelligence
# ---------------------------------------------------------------------------

@dataclass
class Chunk:
    """One text chunk from the parsed PDF, ready for embedding."""
    chunk_id: str
    text: str
    page: int
    section: str


@dataclass
class ParsedDocument:
    """
    The output of Member 1's process_pdf().

    chunks:            list of Chunk objects ready for RAG indexing.
    full_text_by_page: mapping from 1-based page number → full page text,
                       used by the verification pipeline to check quotes.
    """
    chunks: list[Chunk] = field(default_factory=list)
    full_text_by_page: dict[int, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Member 3 — RAG + Verification
# ---------------------------------------------------------------------------

@dataclass
class RetrievedChunk:
    """A chunk returned from FAISS search, with its similarity score."""
    chunk: Chunk
    score: float


@dataclass
class VerifiedClaim:
    """
    Result of running a candidate quote through the evidence verification
    pipeline (Feature 4B).
    """
    quote: str
    page: Optional[int]
    match_score: float
    status: str            # "verified" | "paraphrased" | "unsupported"
    matched_text: str = ""  # the actual substring found in the page text
    char_start: int = -1   # character offset in the page text (for highlighting)
    char_end: int = -1


@dataclass
class ChatAnswer:
    """
    The public return type of ask_question().
    Serialisable to the dict shape other members expect.
    """
    answer: str
    quote: str
    page: Optional[int]
    confidence: str        # "High" | "Medium" | "Low"
    status: str            # "verified" | "paraphrased" | "unsupported"
    match_score: float

    def to_dict(self) -> dict:
        return {
            "answer": self.answer,
            "quote": self.quote,
            "page": self.page,
            "confidence": self.confidence,
            "status": self.status,
            "match_score": self.match_score,
        }


# ---------------------------------------------------------------------------
# Member 2 — Summarization (stub — Member 2 will extend)
# ---------------------------------------------------------------------------

@dataclass
class SummaryClaim:
    """One claim inside a structured summary section."""
    claim: str
    quote: str
    page: Optional[int]
    match_score: float = 0.0
    status: str = "unverified"


@dataclass
class StructuredSummary:
    """Full structured summary of a paper, one list of claims per section."""
    contributions: list[SummaryClaim] = field(default_factory=list)
    methodology: list[SummaryClaim] = field(default_factory=list)
    results: list[SummaryClaim] = field(default_factory=list)
    limitations: list[SummaryClaim] = field(default_factory=list)
    prerequisites: list[SummaryClaim] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Member 4 — Citations (stub — Member 4 will extend)
# ---------------------------------------------------------------------------

@dataclass
class Reference:
    """A single bibliographic reference extracted from the paper."""
    title: str
    authors: str = ""
    year: Optional[int] = None


@dataclass
class PaperMetadata:
    """Enriched metadata from Semantic Scholar (or cache)."""
    title: str
    authors: str = ""
    year: Optional[int] = None
    citation_count: int = 0
    abstract: str = ""
    paper_id: str = ""


# ---------------------------------------------------------------------------
# Member 5 — Reviewer Mode (stub — Member 5 will extend)
# ---------------------------------------------------------------------------

@dataclass
class ReviewResult:
    """Output of the academic reviewer analysis."""
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    missing_experiments: list[str] = field(default_factory=list)
    missing_baselines: list[str] = field(default_factory=list)
    reproducibility_score: float = 0.0
    reproducibility_details: dict = field(default_factory=dict)


@dataclass
class ConsistencyFlag:
    """A single consistency mismatch flagged between paper sections."""
    abstract_claim: str
    results_finding: str
    explanation: str
    abstract_verified: Optional[VerifiedClaim] = None
    results_verified: Optional[VerifiedClaim] = None
