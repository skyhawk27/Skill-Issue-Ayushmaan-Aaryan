"""
citations/cache.py — JSON-file-based cache for OpenAlex responses.

Provides demo reliability by allowing pre-populated caches, and avoids
redundant API calls for papers already looked up.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from citations.config import CACHE_DIR

logger = logging.getLogger(__name__)


class JsonFileCache:
    """
    Simple JSON-file-backed cache keyed by string identifiers.

    Each entry is stored as ``<cache_dir>/<safe_key>.json``.  The cache is
    read-through: ``get`` loads from disk on demand, ``set`` writes
    immediately so data survives process restarts.

    For demo reliability, you can drop pre-populated ``.json`` files into
    the cache directory before running the app.
    """

    def __init__(self, cache_dir: Path | str | None = None) -> None:
        self._dir = Path(cache_dir) if cache_dir else CACHE_DIR
        self._dir.mkdir(parents=True, exist_ok=True)
        self._memory: dict[str, Any] = {}
        self._load_existing()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, key: str) -> Optional[dict]:
        """Return cached value or ``None``."""
        return self._memory.get(self._normalise(key))

    def set(self, key: str, value: dict) -> None:
        """Store *value* under *key*, persisting to disk immediately."""
        nkey = self._normalise(key)
        self._memory[nkey] = value
        self._write(nkey, value)

    def has(self, key: str) -> bool:
        """Check if *key* exists in cache."""
        return self._normalise(key) in self._memory

    @property
    def size(self) -> int:
        """Number of cached entries."""
        return len(self._memory)

    def clear(self) -> None:
        """Remove all cached entries (memory + disk)."""
        self._memory.clear()
        for f in self._dir.glob("*.json"):
            f.unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _normalise(key: str) -> str:
        """Lowercase and strip whitespace for consistent lookups."""
        return key.strip().lower()

    def _safe_filename(self, key: str) -> Path:
        """
        Convert an arbitrary key into a safe filename.

        Replaces non-alphanumeric characters with underscores and
        truncates to 200 chars to stay within filesystem limits.
        """
        safe = "".join(c if c.isalnum() else "_" for c in key)[:200]
        return self._dir / f"{safe}.json"

    def _write(self, key: str, value: dict) -> None:
        path = self._safe_filename(key)
        try:
            path.write_text(json.dumps({"_key": key, "data": value}, indent=2), encoding="utf-8")
        except OSError:
            logger.warning("Cache write failed for key=%s", key, exc_info=True)

    def _load_existing(self) -> None:
        """Load all ``.json`` files already present in the cache dir."""
        for path in self._dir.glob("*.json"):
            try:
                content = json.loads(path.read_text(encoding="utf-8"))
                key = content.get("_key", path.stem)
                self._memory[self._normalise(key)] = content.get("data", content)
            except (json.JSONDecodeError, OSError):
                logger.warning("Skipping corrupted cache file: %s", path)
