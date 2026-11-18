"""Shared HTTP behaviour for external scientific sources.

Every client call returns a SourceResult that carries provenance (source name,
request URL, retrieval time) so callers can store where a value came from.
Failures never raise into the API layer: they come back as ok=False with an
error message, which keeps the rest of the application usable offline.

MOLECULAB 1.0 additions, kept backward compatible with the v0.1-v0.5 clients:

* `_post` for providers whose documented API is POST-only (Open Targets is GraphQL).
* Opt-in on-disk response caching (`cacheable = True` on the subclass).
* Per-provider rate limiting (`min_interval`), so published usage policies are respected
  even when a screening run fans out to many calls.
* Richer provenance: provider record id, source release/version, payload hash and a
  transformation version, so a stored record can be traced to the exact response it came from.

Caching is deliberately skipped whenever an httpx client is injected. Tests inject mock transports,
and a cached real response must never be able to satisfy a mocked test.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from app.clients.http_cache import ResponseCache, RateLimiter, payload_hash
from app.config import settings

RETRYABLE = (429, 500, 502, 503, 504)


@dataclass
class SourceResult:
    source: str
    ok: bool
    data: Any = None
    error: str | None = None
    request_url: str | None = None
    status_code: int | None = None
    elapsed_ms: float | None = None
    retrieved_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    # --- 1.0 provenance ---
    provider_record_id: str | None = None
    source_release: str | None = None
    raw_payload_hash: str | None = None
    transformation_version: str | None = None
    from_cache: bool = False

    def provenance(self) -> dict:
        """Provenance for storage. Only non-empty 1.0 fields are added, so v0.5 callers
        that compare this dict keep seeing exactly the four keys they already handled."""
        prov = {"source": self.source, "request_url": self.request_url, "retrieved_at": self.retrieved_at,
                "http_status": self.status_code}
        for key, value in (("provider_record_id", self.provider_record_id),
                           ("source_release", self.source_release),
                           ("raw_payload_hash", self.raw_payload_hash),
                           ("transformation_version", self.transformation_version)):
            if value is not None:
                prov[key] = value
        if self.from_cache:
            prov["from_cache"] = True
        return prov


class SourceClient:
    name = "source"
    base_url = ""
    #: Subclasses set this to opt into on-disk response caching.
    cacheable = False
    #: Minimum seconds between requests to this provider (0 = no limiting).
    min_interval = 0.0
    #: Bumped when normalisation logic changes, so stored records stay traceable to how they were parsed.
    transformation_version = "1.0"

    def __init__(self, client: httpx.Client | None = None, timeout: float | None = None,
                 max_retries: int | None = None, backoff: float = 0.8,
                 cache: ResponseCache | None = None, use_cache: bool | None = None):
        injected = client is not None
        self._client = client or httpx.Client(
            timeout=timeout or settings.http_timeout,
            headers={"User-Agent": settings.user_agent},
            follow_redirects=True,
        )
        self.max_retries = settings.http_max_retries if max_retries is None else max_retries
        self.backoff = backoff
        # An injected client means a test transport or a caller-managed session: never serve it from cache.
        if use_cache is None:
            use_cache = self.cacheable and not injected
        self._cache = (cache or ResponseCache()) if use_cache else None
        self._limiter = RateLimiter(self.min_interval)

    # ------------------------------------------------------------------ helpers

    def _result_from_body(self, body, url: str, status: int, elapsed: float, cached: bool) -> SourceResult:
        return SourceResult(self.name, True, data=body, request_url=url, status_code=status,
                            elapsed_ms=elapsed, raw_payload_hash=payload_hash(body),
                            transformation_version=self.transformation_version, from_cache=cached)

    def _cached(self, method: str, url: str, params: dict | None, body=None) -> SourceResult | None:
        if self._cache is None:
            return None
        entry = self._cache.get(method, url, params, body)
        if entry is None:
            return None
        res = self._result_from_body(entry["data"], entry.get("request_url", url),
                                     entry.get("status_code", 200), entry.get("elapsed_ms", 0.0), True)
        # Keep the ORIGINAL retrieval time: provenance must not claim a fresh fetch.
        res.retrieved_at = entry.get("retrieved_at", res.retrieved_at)
        return res

    def _store(self, method: str, url: str, res: SourceResult, params: dict | None, body=None) -> None:
        if self._cache is None or not res.ok:
            return
        self._cache.set(method, url, {"data": res.data, "request_url": res.request_url,
                                      "status_code": res.status_code, "elapsed_ms": res.elapsed_ms,
                                      "retrieved_at": res.retrieved_at}, params, body)

    @staticmethod
    def _body(resp: httpx.Response):
        return resp.json() if "json" in resp.headers.get("content-type", "") else resp.text

    def _request(self, method: str, path: str, params: dict | None = None, json_body=None) -> SourceResult:
        url = path if path.startswith("http") else self.base_url + path
        hit = self._cached(method, url, params, json_body)
        if hit is not None:
            return hit

        last_error, last_status = "unknown error", None
        started = time.perf_counter()
        elapsed = lambda: round((time.perf_counter() - started) * 1000, 1)  # noqa: E731
        for attempt in range(self.max_retries + 1):
            try:
                self._limiter.wait()
                if method == "POST":
                    resp = self._client.post(url, params=params, json=json_body)
                else:
                    resp = self._client.get(url, params=params)
                request_url, last_status = str(resp.request.url), resp.status_code
                if resp.status_code == 404:
                    return SourceResult(self.name, False, error="not found", request_url=request_url,
                                        status_code=404, elapsed_ms=elapsed())
                if resp.status_code in RETRYABLE:
                    last_error = ("rate limited (HTTP 429)" if resp.status_code == 429
                                  else f"HTTP {resp.status_code}")
                elif resp.status_code >= 400:
                    return SourceResult(self.name, False, error=f"HTTP {resp.status_code}",
                                        request_url=request_url, status_code=resp.status_code,
                                        elapsed_ms=elapsed())
                else:
                    res = self._result_from_body(self._body(resp), request_url, resp.status_code,
                                                 elapsed(), False)
                    self._store(method, url, res, params, json_body)
                    return res
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            except ValueError as exc:  # malformed JSON
                return SourceResult(self.name, False, error=f"invalid response: {exc}", request_url=url,
                                    status_code=last_status, elapsed_ms=elapsed())
            if attempt < self.max_retries:
                time.sleep(self.backoff * (2 ** attempt))
        return SourceResult(self.name, False, error=last_error, request_url=url, status_code=last_status,
                            elapsed_ms=elapsed())

    def _get(self, path: str, params: dict | None = None) -> SourceResult:
        return self._request("GET", path, params)

    def _post(self, path: str, json_body=None, params: dict | None = None) -> SourceResult:
        return self._request("POST", path, params, json_body)


class GraphQLClient(SourceClient):
    """POST-only GraphQL provider. GraphQL reports errors in a 200 body, so a response carrying
    an `errors` array is translated into ok=False instead of being passed on as data."""

    def _graphql(self, query: str, variables: dict | None = None) -> SourceResult:
        res = self._post("", {"query": query, "variables": variables or {}})
        if not res.ok:
            # A malformed GraphQL query comes back as HTTP 400 whose body explains exactly what is wrong.
            # Surfacing that text instead of a bare "HTTP 400" is the difference between a debuggable
            # error and a dead end, so re-read the body when the transport reports one.
            detail = self._graphql_error_detail(query, variables)
            if detail:
                res.error = f"{self.name} GraphQL error: {detail}"
            return res
        body = res.data
        if not isinstance(body, dict):
            res.ok, res.data, res.error = False, None, f"unexpected {self.name} response shape"
            return res
        if body.get("errors"):
            messages = "; ".join(str(e.get("message", e))[:160] for e in body["errors"][:3])
            res.ok, res.data, res.error = False, None, f"{self.name} GraphQL error: {messages}"
            return res
        res.data = body.get("data")
        return res

    def _graphql_error_detail(self, query: str, variables: dict | None) -> str | None:
        """Best-effort read of the `errors` array from a non-2xx GraphQL response."""
        try:
            resp = self._client.post(self.base_url, json={"query": query, "variables": variables or {}})
            errors = resp.json().get("errors") or []
        except Exception:
            return None
        return "; ".join(str(e.get("message", e))[:160] for e in errors[:3]) or None
