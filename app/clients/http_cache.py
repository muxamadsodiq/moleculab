"""On-disk HTTP response cache and per-provider rate limiting.

Both exist so that a screening or repurposing run, which fans out to many external calls, stays fast and
stays inside published usage policies. Deliberately a plain directory of JSON files: no Redis, no server.

Cached entries are keyed by the full request (method, URL, sorted params, body) so two different queries can
never collide. A cache hit is still reported as a cache hit in provenance, so a reader can tell that a value
was not re-fetched at that moment; `retrieved_at` keeps the time of the ORIGINAL retrieval, never the
time it was read back from disk.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path

from app.config import ROOT, settings

CACHE_DIR = Path(ROOT) / ".cache" / "http"


def payload_hash(data) -> str:
    """Stable hash of a provider payload, for detecting that a record changed upstream."""
    blob = json.dumps(data, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _key(method: str, url: str, params: dict | None, body=None) -> str:
    canonical = json.dumps(
        {"m": method.upper(), "u": url,
         "p": sorted((str(k), str(v)) for k, v in (params or {}).items()),
         "b": body},
        sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ResponseCache:
    """TTL cache on disk. Never raises: a broken cache file is a miss, not an error."""

    def __init__(self, directory: Path | None = None, ttl_seconds: float | None = None):
        self.dir = Path(directory or CACHE_DIR)
        self.ttl = settings.http_cache_ttl if ttl_seconds is None else ttl_seconds
        self._lock = threading.Lock()

    def _path(self, key: str) -> Path:
        # Two-character shard keeps directories small on large runs.
        return self.dir / key[:2] / f"{key}.json"

    def get(self, method: str, url: str, params: dict | None = None, body=None) -> dict | None:
        if not settings.http_cache_enabled or self.ttl <= 0:
            return None
        path = self._path(_key(method, url, params, body))
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if self.ttl > 0 and time.time() - entry.get("stored_at", 0) > self.ttl:
            return None
        return entry

    def set(self, method: str, url: str, entry: dict, params: dict | None = None, body=None) -> None:
        if not settings.http_cache_enabled or self.ttl <= 0:
            return
        path = self._path(_key(method, url, params, body))
        record = {**entry, "stored_at": time.time()}
        try:
            with self._lock:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".tmp")
                tmp.write_text(json.dumps(record, default=str, ensure_ascii=False), encoding="utf-8")
                tmp.replace(path)  # atomic: a reader never sees a half-written entry
        except OSError:
            pass  # a cache that cannot be written must not break a request


class RateLimiter:
    """Minimum interval between requests to one provider, shared across threads.

    Simple by design: the published limits for these services are per-second request rates, which a
    minimum spacing satisfies without a token bucket's bookkeeping.
    """

    def __init__(self, min_interval: float):
        self.min_interval = max(0.0, min_interval)
        self._last = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        if self.min_interval <= 0:
            return
        with self._lock:
            delay = self.min_interval - (time.monotonic() - self._last)
            if delay > 0:
                time.sleep(delay)
            self._last = time.monotonic()
