"""Conclusions drawn from a reference list.

Deliberately free of Streamlit so it can be tested directly: given references in,
facts out. The panel's job is then only to render them.

Every field is ``None`` when its input is absent rather than a zero or a placeholder.
That distinction carries the panel: a missing conclusion is *dropped* rather than
printed as "unknown", so nothing on screen is filler.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from integration.contracts import Reference

#: A reference counts as "recent" if published within this many years of the
#: newest thing cited — a proxy for the paper's own vintage, which is more robust
#: than today's date for an old paper.
_RECENT_WINDOW = 3

#: Bars in the influence chart. Past this a horizontal bar chart in a narrow pane
#: turns into unreadable slivers.
TOP_N = 8


@dataclass(frozen=True)
class CitationStats:
    """What can honestly be concluded about a paper's reference list."""

    total: int = 0
    resolved: int = 0
    most_cited: Reference | None = None
    median_year: int | None = None
    span: tuple[int, int] | None = None
    recent_share: float | None = None
    year_histogram: dict[int, int] = field(default_factory=dict)
    top_by_citations: tuple[Reference, ...] = ()

    @property
    def has_citation_counts(self) -> bool:
        """Whether the influence chart has anything to draw."""
        return bool(self.top_by_citations)

    @property
    def has_years(self) -> bool:
        return bool(self.year_histogram)


def _year_of(ref: Reference) -> int | None:
    try:
        year = int(str(ref.year).strip()[:4])
    except (TypeError, ValueError):
        return None
    return year if 1800 <= year <= 2100 else None


def summarise(references: tuple[Reference, ...]) -> CitationStats:
    """Derive every conclusion the panel can state from these references."""
    if not references:
        return CitationStats()

    years = [y for y in (_year_of(r) for r in references) if y is not None]
    histogram: dict[int, int] = {}
    for year in years:
        histogram[year] = histogram.get(year, 0) + 1

    with_counts = [r for r in references if r.citation_count is not None]
    ranked = tuple(
        sorted(with_counts, key=lambda r: -(r.citation_count or 0))[:TOP_N]
    )

    recent_share: float | None = None
    if years:
        newest = max(years)
        recent = sum(1 for y in years if y >= newest - _RECENT_WINDOW)
        recent_share = recent / len(years)

    return CitationStats(
        total=len(references),
        # "Resolved" means a lookup actually returned something, which is what
        # distinguishes fetched metadata from locally parsed text.
        resolved=sum(
            1 for r in references
            if r.citation_count is not None or r.abstract or r.authors
        ),
        most_cited=ranked[0] if ranked else None,
        median_year=int(statistics.median(years)) if years else None,
        span=(min(years), max(years)) if years else None,
        recent_share=recent_share,
        year_histogram=dict(sorted(histogram.items())),
        top_by_citations=ranked,
    )


def headline_points(stats: CitationStats) -> list[str]:
    """Short, conclusive sentences — each omitted when its input is missing.

    Prose rather than labelled fields because these are meant to be *read as
    conclusions*, which is the thing a list of forty references cannot do.
    """
    points: list[str] = []

    if stats.most_cited is not None and stats.most_cited.citation_count:
        ref = stats.most_cited
        year = f" ({ref.year})" if ref.year else ""
        points.append(
            f"Leans hardest on **{ref.title}**{year} — "
            f"{ref.citation_count:,} citations."
        )

    if stats.span is not None:
        oldest, newest = stats.span
        if oldest != newest:
            points.append(
                f"Draws on work from **{oldest}–{newest}**"
                + (f", median {stats.median_year}." if stats.median_year else ".")
            )

    if stats.recent_share is not None and stats.total > 3:
        pct = round(stats.recent_share * 100)
        if pct >= 50:
            points.append(
                f"**{pct}%** of dated references are from the {_RECENT_WINDOW} years "
                "before the newest one — this builds on current work."
            )
        elif pct <= 25:
            points.append(
                f"Only **{pct}%** of dated references are recent — this leans on "
                "established rather than current work."
            )

    if stats.total and stats.resolved == 0:
        points.append(
            "No reference metadata could be fetched, so citation counts are "
            "unavailable. Titles and years below are parsed from the paper itself."
        )

    return points
