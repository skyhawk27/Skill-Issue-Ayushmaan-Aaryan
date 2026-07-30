"""
citations/models.py — Pydantic v2 data models for the Citation Intelligence subsystem.

All models use Pydantic v2 syntax (ConfigDict, field_validator, model_validator).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Optional


# ---------------------------------------------------------------------------
# Semantic Scholar / Paper Metadata
# ---------------------------------------------------------------------------

class Author(BaseModel):
    """A paper author from Semantic Scholar."""

    model_config = ConfigDict(frozen=True)

    author_id: Optional[str] = Field(None, alias="authorId")
    name: str = ""


class OpenAccessPdf(BaseModel):
    """Open access PDF link from Semantic Scholar."""

    model_config = ConfigDict(frozen=True)

    url: Optional[str] = None
    status: Optional[str] = None


class PaperMetadata(BaseModel):
    """
    Enriched paper metadata from Semantic Scholar.

    Fields align with the Semantic Scholar Academic Graph API response
    when requesting: title, authors, year, abstract, citationCount, url,
    venue, fieldsOfStudy, openAccessPdf.
    """

    model_config = ConfigDict(populate_by_name=True)

    paper_id: Optional[str] = Field(None, alias="paperId")
    title: str = ""
    authors: list[Author] = Field(default_factory=list)
    year: Optional[int] = None
    abstract: Optional[str] = None
    citation_count: Optional[int] = Field(None, alias="citationCount")
    url: Optional[str] = None
    venue: Optional[str] = None
    fields_of_study: Optional[list[str]] = Field(None, alias="fieldsOfStudy")
    open_access_pdf: Optional[OpenAccessPdf] = Field(None, alias="openAccessPdf")


# ---------------------------------------------------------------------------
# Raw Reference (extracted from PDF)
# ---------------------------------------------------------------------------

class RawReference(BaseModel):
    """
    A single reference entry extracted from the PDF's References section.

    `ref_id` is the in-paper identifier (e.g. "1", "2", "Smith2020").
    `raw_text` is the full unparsed reference string.
    Parsed fields (title, authors_raw, year) are best-effort heuristics.
    """

    model_config = ConfigDict(frozen=True)

    ref_id: str
    raw_text: str
    title: Optional[str] = None
    authors_raw: Optional[str] = None
    year: Optional[int] = None

    @field_validator("year", mode="before")
    @classmethod
    def _coerce_year(cls, v: object) -> Optional[int]:
        """Accept string years like '2020' and coerce to int."""
        if v is None:
            return None
        try:
            year = int(v)
            if 1900 <= year <= 2100:
                return year
            return None
        except (ValueError, TypeError):
            return None


# ---------------------------------------------------------------------------
# Citation Purpose (LLM-generated)
# ---------------------------------------------------------------------------

class CitationPurpose(BaseModel):
    """
    LLM-generated explanation of *why* a reference was cited.

    `relationship` is a short label such as "foundational work",
    "direct comparison", "dataset source", "methodology extension", etc.
    """

    ref_id: str
    purpose: str = "Purpose unavailable."
    relationship: str = "unknown"


# ---------------------------------------------------------------------------
# Enriched Citation (combined result for the UI)
# ---------------------------------------------------------------------------

class EnrichedCitation(BaseModel):
    """
    A fully enriched citation combining raw extraction, API metadata,
    and LLM-generated purpose — ready to render in the Citation Explorer.
    """

    ref_id: str
    raw_text: str
    metadata: Optional[PaperMetadata] = None
    purpose: Optional[CitationPurpose] = None
    resolved: bool = False  # True if Semantic Scholar lookup succeeded


# ---------------------------------------------------------------------------
# Research Family Tree
# ---------------------------------------------------------------------------

class FamilyTreeNode(BaseModel):
    """A node in the Research Family Tree graph."""

    paper_id: str
    title: str
    year: Optional[int] = None
    citation_count: Optional[int] = None
    is_current_paper: bool = False


class FamilyTreeEdge(BaseModel):
    """A directed edge: source_id cites target_id."""

    source_id: str
    target_id: str
    relationship: str = "cites"


class FamilyTree(BaseModel):
    """Complete family tree graph for visualization."""

    nodes: list[FamilyTreeNode] = Field(default_factory=list)
    edges: list[FamilyTreeEdge] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Evaluation Metrics
# ---------------------------------------------------------------------------

class LatencyStats(BaseModel):
    """Latency statistics in milliseconds."""

    p50_ms: float = 0.0
    p95_ms: float = 0.0
    mean_ms: float = 0.0
    count: int = 0


class CitationMetrics(BaseModel):
    """
    Evaluation metrics for the Citation Intelligence subsystem.

    Targets (from PRD):
    - reference_extraction_rate  ≥ 85%
    - api_resolution_rate        ≥ 70%
    - api_error_rate             ≤ 5%
    - purpose_generation_quality ≥ 90%
    - end_to_end_latency_s       < 15s for 30 refs
    - family_tree_depth_coverage ≥ 60%
    """

    total_raw_references: int = 0
    parsed_references: int = 0
    reference_extraction_rate: float = 0.0

    api_calls: int = 0
    api_successes: int = 0
    api_errors: int = 0
    api_resolution_rate: float = 0.0
    api_error_rate: float = 0.0

    cache_hits: int = 0
    cache_misses: int = 0
    cache_hit_rate: float = 0.0

    api_latency: LatencyStats = Field(default_factory=LatencyStats)

    purposes_generated: int = 0
    purposes_non_empty: int = 0
    purpose_generation_quality: float = 0.0

    end_to_end_latency_s: float = 0.0

    family_tree_nodes_attempted: int = 0
    family_tree_nodes_resolved: int = 0
    family_tree_depth_coverage: float = 0.0
