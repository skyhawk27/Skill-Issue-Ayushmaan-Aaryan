"""
citations — Citation Intelligence subsystem for PaperLens.

Public API
----------
- ``explore_citations(parsed_doc, ...)`` — Main pipeline: extract → resolve → explain
- ``build_family_tree(paper_id, ...)``   — Research Family Tree builder
- ``extract_references(parsed_doc)``     — Reference extraction only
- ``get_metrics_report(stats)``          — Evaluation metrics report
- ``compute_metrics(stats)``             — Raw metrics computation
"""

from citations.explorer import explore_citations
from citations.extractor import extract_references
from citations.family_tree import build_family_tree
from citations.metrics import compute_metrics, get_metrics_report

__all__ = [
    "explore_citations",
    "extract_references",
    "build_family_tree",
    "compute_metrics",
    "get_metrics_report",
]
