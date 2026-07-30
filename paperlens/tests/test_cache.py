t"""
tests/test_cache.py — Unit tests for the JSON file cache.
"""

import json
import pytest
from pathlib import Path

from citations.cache import JsonFileCache


@pytest.fixture
def tmp_cache(tmp_path: Path) -> JsonFileCache:
    """Create a cache backed by a temporary directory."""
    return JsonFileCache(cache_dir=tmp_path / "test_cache")


class TestJsonFileCache:
    def test_set_and_get(self, tmp_cache: JsonFileCache):
        tmp_cache.set("paper_123", {"title": "Test Paper", "year": 2023})
        result = tmp_cache.get("paper_123")
        assert result is not None
        assert result["title"] == "Test Paper"

    def test_get_missing_key(self, tmp_cache: JsonFileCache):
        assert tmp_cache.get("nonexistent") is None

    def test_has(self, tmp_cache: JsonFileCache):
        assert not tmp_cache.has("key")
        tmp_cache.set("key", {"data": 1})
        assert tmp_cache.has("key")

    def test_case_insensitive(self, tmp_cache: JsonFileCache):
        tmp_cache.set("Paper_ABC", {"title": "ABC"})
        assert tmp_cache.get("paper_abc") is not None
        assert tmp_cache.get("PAPER_ABC") is not None

    def test_whitespace_normalisation(self, tmp_cache: JsonFileCache):
        tmp_cache.set("  key  ", {"v": 1})
        assert tmp_cache.get("key") is not None

    def test_size(self, tmp_cache: JsonFileCache):
        assert tmp_cache.size == 0
        tmp_cache.set("a", {"v": 1})
        tmp_cache.set("b", {"v": 2})
        assert tmp_cache.size == 2

    def test_clear(self, tmp_cache: JsonFileCache):
        tmp_cache.set("x", {"v": 1})
        tmp_cache.clear()
        assert tmp_cache.size == 0
        assert tmp_cache.get("x") is None

    def test_persistence_across_instances(self, tmp_path: Path):
        cache_dir = tmp_path / "persist_cache"
        c1 = JsonFileCache(cache_dir=cache_dir)
        c1.set("persist_key", {"data": "hello"})

        # New instance loads from disk
        c2 = JsonFileCache(cache_dir=cache_dir)
        result = c2.get("persist_key")
        assert result is not None
        assert result["data"] == "hello"

    def test_pre_populated_cache(self, tmp_path: Path):
        """Simulate a pre-populated cache for demo reliability."""
        cache_dir = tmp_path / "prepop_cache"
        cache_dir.mkdir(parents=True)

        # Manually write a cache file
        entry = {"_key": "demo_paper", "data": {"title": "Demo Paper", "year": 2024}}
        (cache_dir / "demo_paper.json").write_text(json.dumps(entry))

        cache = JsonFileCache(cache_dir=cache_dir)
        result = cache.get("demo_paper")
        assert result is not None
        assert result["title"] == "Demo Paper"

    def test_corrupted_file_skipped(self, tmp_path: Path):
        cache_dir = tmp_path / "bad_cache"
        cache_dir.mkdir(parents=True)
        (cache_dir / "bad.json").write_text("NOT VALID JSON {{{")

        # Should not raise — just skips the bad file
        cache = JsonFileCache(cache_dir=cache_dir)
        assert cache.size == 0
