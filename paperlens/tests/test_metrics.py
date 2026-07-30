"""
tests/test_metrics.py — Unit tests for evaluation metrics computation.
"""

from citations.metrics import compute_metrics, get_metrics_report
import pytest


def test_compute_metrics_all_zeroes():
    stats = {}
    m = compute_metrics(stats)
    
    assert m.total_raw_references == 0
    assert m.reference_extraction_rate == 0.0
    assert m.api_resolution_rate == 0.0
    assert m.api_error_rate == 0.0
    assert m.purpose_generation_quality == 0.0


def test_compute_metrics_calculations():
    stats = {
        "total_raw_references": 10,
        "parsed_references": 8,
        
        "api_calls": 12,
        "api_successes": 11,
        "api_errors": 1,
        "resolved_count": 7,
        
        "cache_hits": 2,
        "cache_misses": 10,
        
        "latencies_ms": [100.0, 200.0, 300.0, 400.0, 500.0],
        
        "purposes_generated": 8,
        "purposes_non_empty": 7,
        
        "end_to_end_latency_s": 5.5,
        
        "family_tree_nodes_attempted": 20,
        "family_tree_nodes_resolved": 12,
    }
    
    m = compute_metrics(stats)
    
    assert m.reference_extraction_rate == 0.8  # 8 / 10
    assert m.api_resolution_rate == 0.7        # 7 / 10
    assert m.api_error_rate == pytest.approx(1 / 12)
    assert m.cache_hit_rate == pytest.approx(2 / 12)
    assert m.purpose_generation_quality == pytest.approx(7 / 8)
    assert m.family_tree_depth_coverage == 0.6 # 12 / 20
    
    assert m.api_latency.mean_ms == 300.0
    assert m.end_to_end_latency_s == 5.5


def test_get_metrics_report():
    stats = {
        "total_raw_references": 10,
        "parsed_references": 9,
        "api_calls": 10,
        "api_successes": 10,
        "api_errors": 0,
        "resolved_count": 8,
        "purposes_generated": 10,
        "purposes_non_empty": 9,
        "end_to_end_latency_s": 10.0,
        "latencies_ms": [100.0, 150.0],
    }
    
    report = get_metrics_report(stats)
    
    # Check that keys exist and formatting is correct
    assert report["Reference Extraction"]["Extraction Rate"] == "90.0%"
    assert report["Reference Extraction"]["Status"] == "✅ PASS"
    
    assert report["API Resolution"]["Resolution Rate"] == "80.0%"
    assert report["API Resolution"]["Status"] == "✅ PASS"
    
    assert report["Error Rate"]["Error Rate"] == "0.0%"
    assert report["Error Rate"]["Status"] == "✅ PASS"
    
    assert report["Purpose Generation"]["Quality"] == "90.0%"
    assert report["Purpose Generation"]["Status"] == "✅ PASS"
