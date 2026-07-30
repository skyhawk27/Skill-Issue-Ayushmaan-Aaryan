"""
citations/family_tree.py — Research Family Tree builder.

Builds a citation-ancestry graph by recursively fetching references
from OpenAlex, limited to the top-N most-cited papers at each
level (max depth=2) to keep the graph manageable.

Produces a ``FamilyTree`` (nodes + edges) suitable for NetworkX / Plotly
visualisation.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from citations.cache import JsonFileCache
from citations.config import FAMILY_TREE_MAX_DEPTH, FAMILY_TREE_MAX_REFS
from citations.models import FamilyTree, FamilyTreeEdge, FamilyTreeNode
from citations.openalex import OpenAlexClient

logger = logging.getLogger(__name__)


async def build_family_tree(
    paper_id: str,
    paper_title: str = "Current Paper",
    depth: int = FAMILY_TREE_MAX_DEPTH,
    max_refs_per_level: int = FAMILY_TREE_MAX_REFS,
    cache: JsonFileCache | None = None,
) -> FamilyTree:
    """
    Build a citation ancestry graph for a paper.

    Parameters
    ----------
    paper_id : str
        OpenAlex paper ID of the current (uploaded) paper.
    paper_title : str
        Title of the current paper (used as the root node label).
    depth : int
        Maximum depth to traverse (default 2).
    max_refs_per_level : int
        Number of top-cited references to include at each level.
    cache : JsonFileCache | None
        Optional shared cache instance.

    Returns
    -------
    FamilyTree
        A graph of nodes and edges ready for visualisation.
    """
    client = OpenAlexClient(cache=cache)

    nodes: dict[str, FamilyTreeNode] = {}
    edges: list[FamilyTreeEdge] = []
    visited: set[str] = set()

    # Add root node
    root_node = FamilyTreeNode(
        paper_id=paper_id,
        title=paper_title,
        is_current_paper=True,
    )
    nodes[paper_id] = root_node

    await _traverse(
        client=client,
        paper_id=paper_id,
        current_depth=0,
        max_depth=depth,
        max_refs=max_refs_per_level,
        nodes=nodes,
        edges=edges,
        visited=visited,
    )

    logger.info(
        "Family tree built: %d nodes, %d edges", len(nodes), len(edges)
    )
    return FamilyTree(nodes=list(nodes.values()), edges=edges)


# ---------------------------------------------------------------------------
# Recursive traversal
# ---------------------------------------------------------------------------


async def _traverse(
    client: OpenAlexClient,
    paper_id: str,
    current_depth: int,
    max_depth: int,
    max_refs: int,
    nodes: dict[str, FamilyTreeNode],
    edges: list[FamilyTreeEdge],
    visited: set[str],
) -> None:
    """Recursively fetch references and build the graph."""
    if current_depth >= max_depth or paper_id in visited:
        return

    visited.add(paper_id)

    try:
        refs = await client.get_references(paper_id, limit=100)
    except Exception as exc:
        logger.warning("Failed to get references for %s: %s", paper_id, exc)
        return

    # Extract cited papers, filtering nulls
    cited_papers: list[dict[str, Any]] = []
    for ref_item in refs:
        cited = ref_item.get("citedPaper")
        if cited and cited.get("paperId"):
            cited_papers.append(cited)

    # Sort by citation count (descending) and take top-N
    cited_papers.sort(
        key=lambda p: p.get("citationCount") or 0, reverse=True
    )
    top_refs = cited_papers[:max_refs]

    # Add nodes and edges
    child_tasks = []
    for ref_paper in top_refs:
        ref_id = ref_paper["paperId"]
        ref_title = ref_paper.get("title", "Unknown")
        ref_year = ref_paper.get("year")
        ref_citations = ref_paper.get("citationCount")

        if ref_id not in nodes:
            nodes[ref_id] = FamilyTreeNode(
                paper_id=ref_id,
                title=ref_title,
                year=ref_year,
                citation_count=ref_citations,
            )

        edges.append(
            FamilyTreeEdge(source_id=paper_id, target_id=ref_id)
        )

        # Queue deeper traversal
        if current_depth + 1 < max_depth:
            child_tasks.append(
                _traverse(
                    client=client,
                    paper_id=ref_id,
                    current_depth=current_depth + 1,
                    max_depth=max_depth,
                    max_refs=max_refs,
                    nodes=nodes,
                    edges=edges,
                    visited=visited,
                )
            )

    # Run child traversals concurrently
    if child_tasks:
        await asyncio.gather(*child_tasks, return_exceptions=True)


# ---------------------------------------------------------------------------
# Helper: convert FamilyTree to NetworkX graph dict (for Plotly/PyVis)
# ---------------------------------------------------------------------------


def family_tree_to_networkx_data(tree: FamilyTree) -> dict:
    """
    Convert a ``FamilyTree`` into a dict that can be fed to
    ``networkx.node_link_graph()`` or used directly by Plotly.

    Returns
    -------
    dict
        ``{"nodes": [...], "edges": [...]}`` where each node has
        ``id``, ``label``, ``year``, ``citation_count``, ``is_root``.
    """
    nx_nodes = []
    for n in tree.nodes:
        label = n.title
        if len(label) > 40:
            label = label[:37] + "..."
        nx_nodes.append(
            {
                "id": n.paper_id,
                "label": label,
                "title": n.title,
                "year": n.year,
                "citation_count": n.citation_count,
                "is_root": n.is_current_paper,
            }
        )

    nx_edges = [
        {"source": e.source_id, "target": e.target_id, "relationship": e.relationship}
        for e in tree.edges
    ]

    return {"nodes": nx_nodes, "edges": nx_edges}
