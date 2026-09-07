"""Transport layer for the Paulsjob API.

Knows about HTTP, auth, retries and pagination. Knows nothing about recruiting:
no notion of jobs, candidates or decisions appears here. That separation is what
lets the analysis layer be tested without a network.

Standard library only, so the tool runs on a bare Python install.
"""
from __future__ import annotations

import json
import logging
import random
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

log = logging.getLogger(__name__)

USER_AGENT = "screening-accuracy-analyzer/0.1"

#: Retried with backoff. 429 is included defensively: the API documents no rate
#: limit at all -- no 429 response, no Retry-After, no rate-limit headers -- so
#: we cannot know the real policy and must not assume there is none.
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class ApiError(Exception):
    """Base for every failure this client raises."""


@dataclass
class HttpError(ApiError):
    """The API answered, and the answer was an error.

    `message` is the API's own message where one could be parsed, so callers can
    tell the user what to check instead of printing a stack trace.
    """

    status: int
    message: str
    path: str
    body: str = ""

    def __str__(self) -> str:
        text = f"{self.path} -> HTTP {self.status}: {self.message}"
        # Never let an error say nothing useful. If the message degraded to the
        # bare status code, show the response body so the caller can act.
        if self.body and self.message.strip() in ("", f"HTTP {self.status}"):
            text += f"\n  response body: {self.body[:500]}"
        return text


@dataclass
class BlockedError(ApiError):
    """Something between us and the API refused the request.

    Distinct from HttpError on purpose. A Cloudflare 403 means "fix your
    client"; an API 403 means "your key lacks a permission". Reporting both as
    "403 forbidden" would produce exactly the unactionable error message the
    brief warns against.
    """

    status: int
    path: str
    detail: str

    def __str__(self) -> str:
        return (
            f"{self.path} -> blocked before reaching the API (HTTP {self.status}). "
            f"{self.detail} Check the User-Agent header and network egress."
        )


@dataclass
class TransportError(ApiError):
    """Timeout, DNS failure, connection reset -- no HTTP response at all."""

    path: str
    detail: str

    def __str__(self) -> str:
        return f"{self.path} -> no response from server: {self.detail}"


@dataclass
class RequestLog:
    """Per-run counters, surfaced in the report's data-quality section."""

    requests: int = 0
    retries: int = 0
    failures: list[str] = field(default_factory=list)


def _urlopen(request: urllib.request.Request, timeout: float):
    """Seam for tests to swap in a fake transport."""
    return urllib.request.urlopen(request, timeout=timeout)


class PaulsjobClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = 30.0,
        max_retries: int = 4,
        opener: Callable[..., Any] = _urlopen,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._key = api_key
        self._timeout = timeout
        self._max_retries = max_retries
        self._opener = opener
        self._sleep = sleep
        #: One correlation id per run, as the API docs recommend, so a whole
        #: run can be traced server-side from a single id.
        self._correlation_id = str(uuid.uuid4())
        self.stats = RequestLog()

    # -- headers -----------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        return {
            "x-company-api-key": self._key,
            "Accept": "application/json",
            # The API sits behind Cloudflare, which rejects urllib's default
            # User-Agent with a 1010 "browser signature" block before the
            # request ever reaches the API.
            "User-Agent": USER_AGENT,
            "paul-correlation-id": self._correlation_id,
            "paul-request-id": str(uuid.uuid4()),
        }

    # -- single request ----------------------------------------------------
    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any | None = None,
        body: bytes | None = None,
        content_type: str | None = None,
    ) -> Any:
        """Perform one request, retrying transient failures.

        Returns the parsed `data` envelope. Raises an ApiError subclass whose
        message says what to check.
        """
        url = self._base + path
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                url += "?" + urllib.parse.urlencode(clean)

        headers = self._headers()
        payload = body
        if json_body is not None:
            payload = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        elif content_type:
            headers["Content-Type"] = content_type

        last_detail = ""
        redirects_left = 5
        attempt = 0
        while attempt <= self._max_retries:
            # Headers are never logged: they carry the API key.
            log.debug("%s %s (attempt %d)", method, path, attempt + 1)
            self.stats.requests += 1
            request = urllib.request.Request(url, data=payload, headers=headers, method=method)
            try:
                with self._opener(request, self._timeout) as response:
                    raw = response.read()
                    return self._envelope(raw, path)
            except urllib.error.HTTPError as exc:
                raw = exc.read()
                text = raw[:2000].decode("utf-8", "replace")
                if self._is_edge_block(exc, text):
                    raise BlockedError(exc.code, path, self._edge_detail(text)) from exc
                # urllib will not follow a redirect for a POST, so a 307/308
                # surfaces as an error. This API redirects some write endpoints
                # (e.g. trailing-slash normalisation), and 307/308 require the
                # method and body to be preserved.
                location = exc.headers.get("Location") if exc.headers else None
                if exc.code in (301, 302, 303, 307, 308) and location and redirects_left > 0:
                    redirects_left -= 1
                    url = urllib.parse.urljoin(url, location)
                    if exc.code in (301, 302, 303) and method != "GET":
                        # Legacy redirects downgrade to GET; 307/308 do not.
                        method, payload = "GET", None
                        headers.pop("Content-Type", None)
                    log.debug("following HTTP %d redirect to %s", exc.code, url)
                    continue
                if exc.code in RETRY_STATUSES and attempt < self._max_retries:
                    self.stats.retries += 1
                    self._sleep(self._backoff(attempt, exc))
                    last_detail = f"HTTP {exc.code}"
                    attempt += 1
                    continue
                raise HttpError(exc.code, self._api_message(text, exc.code), path, text) from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_detail = f"{type(exc).__name__}: {exc}"
                if attempt < self._max_retries:
                    self.stats.retries += 1
                    self._sleep(self._backoff(attempt, None))
                    attempt += 1
                    continue
                raise TransportError(path, last_detail) from exc

        raise TransportError(path, last_detail or "exhausted retries")

    # -- helpers -----------------------------------------------------------
    def _backoff(self, attempt: int, exc: urllib.error.HTTPError | None) -> float:
        """Honour Retry-After when present, else exponential with jitter.

        Jitter matters because the fetch phase issues one request per candidate;
        without it a burst of failures would retry in lockstep.
        """
        if exc is not None:
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            if retry_after:
                try:
                    return max(0.0, float(retry_after))
                except ValueError:
                    pass
        return (2**attempt) + random.uniform(0, 0.5)

    @staticmethod
    def _is_edge_block(exc: urllib.error.HTTPError, text: str) -> bool:
        """Distinguish an edge/WAF rejection from a genuine API response."""
        if "cloudflare" in text.lower() or "error_code" in text and "1010" in text:
            return True
        server = (exc.headers.get("Server") or "").lower() if exc.headers else ""
        return server == "cloudflare" and exc.code in (403, 503) and "\"status\"" not in text

    @staticmethod
    def _edge_detail(text: str) -> str:
        try:
            parsed = json.loads(text)
            return str(parsed.get("detail") or parsed.get("title") or "").strip()
        except (ValueError, AttributeError):
            return "The request was rejected by the edge, not by the API."

    @staticmethod
    def _api_message(text: str, status: int) -> str:
        """Pull the API's own error message out of its envelope.

        Key lookup is case-insensitive: this API uses PascalCase throughout
        (`JobPositionTitle`, `PaulDecision`), and its error envelopes are not
        consistently lowercase either.
        """
        try:
            parsed = json.loads(text)
        except ValueError:
            return (text.strip().splitlines() or [f"HTTP {status}"])[0][:200]
        if not isinstance(parsed, dict):
            return f"HTTP {status}"

        lowered = {str(k).lower(): v for k, v in parsed.items()}
        parts: list[str] = []
        for key in ("message", "detail", "error", "title", "reason"):
            value = lowered.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
                break
            # This API returns `message` as a LIST of validation failures on
            # some endpoints and as a plain string on others.
            if isinstance(value, list) and value:
                parts.append("; ".join(str(item) for item in value[:5]))
                break

        # Validation failures usually carry the useful part in a nested list:
        # which field failed and why. That is what the operator needs to see.
        for key in ("errors", "validationerrors", "details", "fields"):
            value = lowered.get(key)
            if isinstance(value, list) and value:
                parts.append("; ".join(str(item) for item in value[:5]))
                break
            if isinstance(value, dict) and value:
                parts.append("; ".join(f"{k}: {v}" for k, v in list(value.items())[:5]))
                break

        return " | ".join(parts) if parts else f"HTTP {status}"

    @staticmethod
    def _envelope(raw: bytes, path: str) -> Any:
        """Unwrap the {status, message, data} envelope.

        Tolerant by design: the spec and the live API disagree about response
        shapes often enough that we return whatever we got rather than assert.
        """
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except ValueError as exc:
            raise HttpError(200, f"response was not JSON: {exc}", path, raw[:200].decode("utf-8", "replace")) from exc
        if isinstance(parsed, dict) and "data" in parsed:
            return parsed["data"]
        return parsed

    # -- verbs -------------------------------------------------------------
    def get(self, path: str, **params: Any) -> Any:
        return self.request("GET", path, params=params)

    def post(self, path: str, json_body: Any | None = None, **params: Any) -> Any:
        return self.request("POST", path, params=params, json_body=json_body)

    # -- pagination --------------------------------------------------------
    # The API uses two different styles and the client hides the difference.
    def paginate_cursor(
        self,
        path: str,
        items_key: str,
        *,
        per_page: int = 100,
        max_pages: int = 1000,
        **params: Any,
    ) -> Iterator[dict]:
        """DynamoDB-style: follow LastEvaluatedKey until it comes back empty."""
        cursor = None
        for _ in range(max_pages):
            data = self.get(path, PerPage=per_page, LastEvaluatedKey=cursor, **params)
            if not isinstance(data, dict):
                return
            yield from (data.get(items_key) or [])
            cursor = data.get("LastEvaluatedKey")
            if not cursor:
                return
        log.warning("%s: stopped after %d pages", path, max_pages)

    def paginate_pages(
        self,
        path: str,
        items_key: str,
        *,
        payload: dict | None = None,
        per_page: int = 100,
        max_pages: int = 1000,
    ) -> Iterator[dict]:
        """Page-number style: walk Page until a short or empty page arrives."""
        page = 1
        while page <= max_pages:
            body = dict(payload or {})
            body.update({"PerPage": per_page, "Page": page})
            data = self.post(path, json_body=body)
            if not isinstance(data, dict):
                return
            items = data.get(items_key) or []
            yield from items
            total_pages = data.get("TotalPage")
            if isinstance(total_pages, int) and page >= total_pages:
                return
            if len(items) < per_page:
                return
            page += 1
        log.warning("%s: stopped after %d pages", path, max_pages)
