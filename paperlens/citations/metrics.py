"""
citations/metrics.py — Evaluation metrics for the Citation Intelligence subsystem.

Computes and reports 8 quantitative metrics with targets from the PRD.
Can be used both during development (unit-test assertions) and at runtime
(displayed in the Streamlit dashboard).
"""

from __future__ import annotations

import statistics
from typing import Any

from citations.models import CitationMetrics, LatencyStats


def compute_metrics(stats: dict[str, Any]) -> CitationMetrics:
    """
    Build a ``CitationMetrics`` report from the raw stats dict produced
    by ``explore_citations()``.

    Parameters
    ----------
    stats : dict
        Raw counters and timings from the explorer pipeline.

    Returns
    -------
    CitationMetrics
        Fully computed metrics with rates and latency percentiles.
    """
    total_raw = stats.get("total_raw_references", 0)
    parsed = stats.get("parsed_references", 0)

    api_calls = stats.get("api_calls", 0)
    api_successes = stats.get("api_successes", 0)
    api_errors = stats.get("api_errors", 0)
    resolved = stats.get("resolved_count", 0)

    cache_hits = stats.get("cache_hits", 0)
    cache_misses = stats.get("cache_misses", 0)

    latencies = stats.get("latencies_ms", [])

    purposes_gen = stats.get("purposes_generated", 0)
    purposes_ok = stats.get("purposes_non_empty", 0)

    e2e = stats.get("end_to_end_latency_s", 0.0)

    ft_attempted = stats.get("family_tree_nodes_attempted", 0)
    ft_resolved = stats.get("family_tree_nodes_resolved", 0)

    return CitationMetrics(
        # Extraction
        total_raw_references=total_raw,
        parsed_references=parsed,
        reference_extraction_rate=_safe_ratio(parsed, total_raw),
        # API resolution
        api_calls=api_calls,
        api_successes=api_successes,
        api_errors=api_errors,
        api_resolution_rate=_safe_ratio(resolved, total_raw),
        api_error_rate=_safe_ratio(api_errors, api_calls),
        # Cache
        cache_hits=cache_hits,
        cache_misses=cache_misses,
        cache_hit_rate=_safe_ratio(cache_hits, cache_hits + cache_misses),
        # Latency
        api_latency=_compute_latency_stats(latencies),
        # Purpose generation
        purposes_generated=purposes_gen,
        purposes_non_empty=purposes_ok,
        purpose_generation_quality=_safe_ratio(purposes_ok, purposes_gen),
        # End-to-end
        end_to_end_latency_s=e2e,
        # Family tree
        family_tree_nodes_attempted=ft_attempted,
        family_tree_nodes_resolved=ft_resolved,
        family_tree_depth_coverage=_safe_ratio(ft_resolved, ft_attempted),
    )


def get_metrics_report(stats: dict[str, Any]) -> dict[str, Any]:
    """
    Generate a human-readable metrics report as a dict.

    Suitable for displaying in Streamlit or logging.
    """
    m = compute_metrics(stats)

    return {
        "Reference Extraction": {
            "Total Raw References": m.total_raw_references,
            "Successfully Parsed": m.parsed_references,
            "Extraction Rate": f"{m.reference_extraction_rate:.1%}",
            "Target": "≥ 85%",
            "Status": "✅ PASS" if m.reference_extraction_rate >= 0.85 else "❌ BELOW TARGET",
        },
        "API Resolution": {
            "API Calls Made": m.api_calls,
            "Successful": m.api_successes,
            "Errors": m.api_errors,
            "Resolution Rate": f"{m.api_resolution_rate:.1%}",
            "Target": "≥ 70%",
            "Status": "✅ PASS" if m.api_resolution_rate >= 0.70 else "❌ BELOW TARGET",
        },
        "Cache Performance": {
            "Cache Hits": m.cache_hits,
            "Cache Misses": m.cache_misses,
            "Hit Rate": f"{m.cache_hit_rate:.1%}",
            "Target": "Track only",
        },
        "API Latency": {
            "p50 (ms)": f"{m.api_latency.p50_ms:.0f}",
            "p95 (ms)": f"{m.api_latency.p95_ms:.0f}",
            "Mean (ms)": f"{m.api_latency.mean_ms:.0f}",
            "Samples": m.api_latency.count,
            "Target": "p50 < 500ms",
            "Status": "✅ PASS" if m.api_latency.p50_ms < 500 else "⚠️ SLOW",
        },
        "Error Rate": {
            "Error Rate": f"{m.api_error_rate:.1%}",
            "Target": "≤ 5%",
            "Status": "✅ PASS" if m.api_error_rate <= 0.05 else "❌ ABOVE TARGET",
        },
        "Purpose Generation": {
            "Generated": m.purposes_generated,
            "Non-Empty": m.purposes_non_empty,
            "Quality": f"{m.purpose_generation_quality:.1%}",
            "Target": "≥ 90%",
            "Status": "✅ PASS" if m.purpose_generation_quality >= 0.90 else "❌ BELOW TARGET",
        },
        "End-to-End Latency": {
            "Total (s)": f"{m.end_to_end_latency_s:.1f}",
            "Target": "< 15s for 30 refs",
            "Status": "✅ PASS" if m.end_to_end_latency_s < 15.0 else "⚠️ SLOW",
        },
        "Family Tree Coverage": {
            "Nodes Attempted": m.family_tree_nodes_attempted,
            "Nodes Resolved": m.family_tree_nodes_resolved,
            "Depth Coverage": f"{m.family_tree_depth_coverage:.1%}",
            "Target": "≥ 60%",
            "Status": (
                "✅ PASS"
                if m.family_tree_depth_coverage >= 0.60
                else ("❌ BELOW TARGET" if m.family_tree_nodes_attempted > 0 else "⏳ NOT RUN")
            ),
        },
    }


def run_evaluation(stats: dict[str, Any]) -> dict[str, Any]:
    """
    Run full evaluation and return both raw metrics and formatted report.

    Returns
    -------
    dict
        ``{"metrics": CitationMetrics, "report": dict, "overall_pass": bool}``
    """
    m = compute_metrics(stats)
    report = get_metrics_report(stats)

    # Overall pass: all critical targets met
    overall_pass = (
        m.reference_extraction_rate >= 0.85
        and m.api_resolution_rate >= 0.70
        and m.api_error_rate <= 0.05
        and m.purpose_generation_quality >= 0.90
    )

    return {
        "metrics": m,
        "report": report,
        "overall_pass": overall_pass,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_ratio(numerator: int | float, denominator: int | float) -> float:
    """Compute a ratio, returning 0.0 if the denominator is zero."""
    if denominator == 0:
        return 0.0
    return numerator / denominator


def _compute_latency_stats(latencies_ms: list[float]) -> LatencyStats:
    """Compute p50, p95, mean from a list of latencies in ms."""
    if not latencies_ms:
        return LatencyStats()

    sorted_lats = sorted(latencies_ms)
    n = len(sorted_lats)

    p50_idx = max(0, int(n * 0.50) - 1)
    p95_idx = max(0, int(n * 0.95) - 1)

    return LatencyStats(
        p50_ms=sorted_lats[p50_idx],
        p95_ms=sorted_lats[p95_idx],
        mean_ms=statistics.mean(sorted_lats),
        count=n,
    )
