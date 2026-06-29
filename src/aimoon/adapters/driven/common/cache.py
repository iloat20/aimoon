"""Disk-based TTL cache for HTTP responses and API data.

Stores JSON-serialisable data on disk with a TTL (time-to-live).
Supports namespacing by source/adapter to avoid key collisions.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

from aimoon.adapters.driven.config.settings import get_settings

logger = logging.getLogger(__name__)


def _safe_key(key: str) -> str:
    """Convert an arbitrary key to a safe filename."""
    return hashlib.sha256(key.encode()).hexdigest()[:32]


class DiskTtlCache:
    """Disk-backed TTL cache with JSON serialisation.

    Each entry is stored as ``<cache_dir>/<namespace>/<hash>.json``
    containing ``{"ts": <unix_ts>, "data": <payload>}``.
    """

    def __init__(
        self,
        namespace: str = "default",
        ttl_seconds: int = 3600,
        cache_dir: str | Path | None = None,
    ) -> None:
        self.namespace = namespace
        self.ttl_seconds = ttl_seconds
        if cache_dir is None:
            cache_dir = get_settings().cache_path
        self._base = Path(cache_dir)
        self._dir = self._base / namespace
        self._dir.mkdir(parents=True, exist_ok=True)

    # -- public API ------------------------------------------------

    def get(self, key: str) -> Any | None:
        """Return cached data if present and not expired, else ``None``."""
        path = self._path_for(key)
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            ts = raw.get("ts", 0)
            if time.time() - ts > self.ttl_seconds:
                path.unlink(missing_ok=True)
                return None
            return raw.get("data")
        except (OSError, json.JSONDecodeError, KeyError) as e:
            logger.debug("[%s] cache read error: %s", self.namespace, e)
            path.unlink(missing_ok=True)
            return None

    def set(self, key: str, data: Any) -> None:
        """Persist *data* under *key* with the current timestamp."""
        path = self._path_for(key)
        try:
            payload = json.dumps(
                {"ts": time.time(), "data": data},
                ensure_ascii=False,
                default=str,
            )
            path.write_text(payload, encoding="utf-8")
        except (OSError, TypeError) as e:
            logger.debug("[%s] cache write error: %s", self.namespace, e)

    def invalidate(self, key: str) -> None:
        """Remove a single entry."""
        self._path_for(key).unlink(missing_ok=True)

    def clear(self) -> int:
        """Remove all entries in this namespace. Returns count removed."""
        count = 0
        for f in self._dir.glob("*.json"):
            f.unlink()
            count += 1
        return count

    # -- internals -------------------------------------------------

    def _path_for(self, key: str) -> Path:
        return self._dir / f"{_safe_key(key)}.json"
