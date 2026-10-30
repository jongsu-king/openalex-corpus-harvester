"""HTTPX OpenAlex client. All retry and usage limits are finite and durable."""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import quote

import httpx
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_random_exponential

from .search import normalize_identifier


class APIError(RuntimeError):
    def __init__(
        self,
        message: str,
        code: str = "API_ERROR",
        retryable: bool = False,
        retry_after: float = 0,
        status_code: int | None = None,
    ):
        # Never include httpx exceptions or complete request URLs: they may contain secrets.
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.retry_after = retry_after
        self.status_code = status_code


class TransientAPIError(APIError):
    def __init__(self, message: str, code: str = "API_TRANSIENT", **kwargs: Any):
        super().__init__(message, code=code, retryable=True, **kwargs)


class BudgetExceeded(APIError):
    def __init__(self, message: str = "Configured request budget exhausted", retry_at: str | None = None):
        super().__init__(message, code="BUDGET_EXCEEDED", retryable=True)
        self.retry_at = retry_at or next_utc_day()


def next_utc_day() -> str:
    now = datetime.now(timezone.utc)
    return (now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).isoformat()


def retry_after_seconds(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            dt = parsedate_to_datetime(value)
            return max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return 0.0


class BudgetJournal:
    """Process-safe write-ahead reservation journal.

    Requests that crash or fail still consume a reservation (conservative).
    Metadata counters reset at UTC midnight. Content counters are lifetime caps
    for this data directory and never reset implicitly.
    """

    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "network_budget.json"
        self.lock_path = self.directory / "network_budget.lock"

    @contextmanager
    def transaction(self) -> Iterator[dict[str, Any]]:
        with self.lock_path.open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                if self.path.exists():
                    try:
                        data = json.loads(self.path.read_text())
                    except (ValueError, OSError) as exc:
                        raise APIError(
                            "Budget journal unreadable; refusing unaccounted network use",
                            "BUDGET_JOURNAL_INVALID",
                        ) from exc
                else:
                    data = {}
                day = datetime.now(timezone.utc).date().isoformat()
                if data.get("day") != day:
                    data.update(
                        day=day,
                        metadata_requests=0,
                        metadata_reserved_usd="0",
                        rate_headers={},
                        blocked_until=None,
                    )
                data.setdefault("content_requests", 0)
                data.setdefault("content_reserved_usd", "0")
                yield data
                tmp = self.path.with_name(self.path.name + f".{os.getpid()}.tmp")
                with tmp.open("w", encoding="utf-8") as output:
                    json.dump(data, output, sort_keys=True, indent=2)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(tmp, self.path)
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def reserve_metadata(self, cost: float, request_limit: int, cost_limit: float) -> None:
        with self.transaction() as data:
            blocked = data.get("blocked_until")
            if blocked and datetime.fromisoformat(blocked) > datetime.now(timezone.utc):
                raise BudgetExceeded("OpenAlex reported exhausted daily allowance", retry_at=blocked)
            reserved = Decimal(data["metadata_reserved_usd"]) + Decimal(str(cost))
            if data["metadata_requests"] >= request_limit or reserved > Decimal(str(cost_limit)):
                raise BudgetExceeded()
            data["metadata_requests"] += 1
            data["metadata_reserved_usd"] = str(reserved)

    def reserve_content(self, cost: float, request_limit: int, cost_limit: float) -> None:
        with self.transaction() as data:
            reserved = Decimal(data["content_reserved_usd"]) + Decimal(str(cost))
            if data["content_requests"] >= request_limit or reserved > Decimal(str(cost_limit)):
                raise BudgetExceeded("Configured content download lifetime cap exhausted")
            data["content_requests"] += 1
            data["content_reserved_usd"] = str(reserved)

    def observe_headers(self, headers: httpx.Headers, estimate: float = 0) -> None:
        observed = {key: value for key, value in headers.items() if key.lower().startswith("x-ratelimit-")}
        if not observed:
            return
        with self.transaction() as data:
            data["rate_headers"] = observed
            try:
                # Prefer the direct USD header observed in live responses. The
                # documented October 2026 fallback is one credit = USD 0.0001.
                usd_header = headers.get("X-RateLimit-Cost-USD")
                cost = (
                    Decimal(usd_header)
                    if usd_header is not None
                    else Decimal(headers.get("X-RateLimit-Credits-Used", "0")) * Decimal("0.0001")
                )
                if cost > Decimal(str(estimate)):
                    data["metadata_reserved_usd"] = str(
                        Decimal(data["metadata_reserved_usd"]) + cost - Decimal(str(estimate))
                    )
                remaining = headers.get(
                    "X-RateLimit-Remaining-USD", headers.get("X-RateLimit-Remaining", "1")
                )
                if Decimal(remaining) <= 0:
                    seconds = float(headers.get("X-RateLimit-Reset", "0"))
                    data["blocked_until"] = (
                        (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()
                        if seconds > 0
                        else next_utc_day()
                    )
            except (ValueError, ArithmeticError):
                data["rate_header_warning"] = (
                    "Unrecognized rate header units/value; configured reservation caps remain enforced"
                )


class OpenAlexClient:
    def __init__(self, settings: Any, state_dir: Path, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.api_key = os.environ.get("OPENALEX_API_KEY") or None
        self.journal = BudgetJournal(state_dir)
        self.transport = transport
        self.client: httpx.AsyncClient | None = None
        self._rate_lock = asyncio.Lock()
        self._last_request = 0.0
        self.last_rate_headers: dict[str, str] = {}
        self.request_count = 0

    async def __aenter__(self) -> "OpenAlexClient":
        headers = {
            "Accept": "application/json",
            "User-Agent": "openalex-corpus/0.1 (research corpus collection)",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        self.client = httpx.AsyncClient(
            base_url=str(self.settings.base_url).rstrip("/") + "/",
            timeout=self.settings.timeout,
            transport=self.transport,
            headers=headers,
            follow_redirects=False,
            limits=httpx.Limits(max_connections=2),
        )
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self.client:
            await self.client.aclose()

    async def _throttle(self) -> None:
        async with self._rate_lock:
            delay = 1 / self.settings.requests_per_second - (time.monotonic() - self._last_request)
            if delay > 0:
                await asyncio.sleep(delay)
            self._last_request = time.monotonic()

    async def _request(self, path: str, params: dict[str, Any], cost: float) -> dict[str, Any]:
        if self.client is None:
            raise RuntimeError("Use OpenAlexClient as an async context manager")
        jitter = wait_random_exponential(multiplier=0.5, max=15)

        def wait(state: Any) -> float:
            error = state.outcome.exception() if state.outcome else None
            return max(float(jitter(state)), getattr(error, "retry_after", 0))

        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self.settings.max_attempts),
            wait=wait,
            retry=retry_if_exception_type(TransientAPIError),
            reraise=True,
        ):
            with attempt:
                self.journal.reserve_metadata(
                    cost, self.settings.daily_request_budget, self.settings.daily_budget_usd
                )
                await self._throttle()
                self.request_count += 1
                try:
                    response = await self.client.get(path, params=params)
                except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
                    code = (
                        "API_TIMEOUT" if isinstance(exc, httpx.TimeoutException) else "API_CONNECTION_ERROR"
                    )
                    raise TransientAPIError("OpenAlex request transport failure", code=code) from None
                self.last_rate_headers = {
                    k: v for k, v in response.headers.items() if k.startswith("x-ratelimit-")
                }
                self.journal.observe_headers(response.headers, cost)
                status = response.status_code
                if status in (401, 403):
                    raise APIError(
                        "OpenAlex rejected authentication or access (check API key and paid filter entitlement)",
                        "API_AUTH_ERROR",
                        status_code=status,
                    )
                if status == 404:
                    raise APIError("OpenAlex entity not found", "API_NOT_FOUND", status_code=status)
                if status == 429 or status >= 500:
                    delay = retry_after_seconds(response.headers.get("Retry-After"))
                    try:
                        remaining = response.headers.get(
                            "X-RateLimit-Remaining-USD", response.headers.get("X-RateLimit-Remaining", "1")
                        )
                        exhausted = float(remaining) <= 0
                    except ValueError:
                        exhausted = False
                    if exhausted:
                        reset = retry_after_seconds(response.headers.get("X-RateLimit-Reset"))
                        when = (
                            (datetime.now(timezone.utc) + timedelta(seconds=reset)).isoformat()
                            if reset
                            else next_utc_day()
                        )
                        raise BudgetExceeded("OpenAlex reported exhausted daily allowance", retry_at=when)
                    if delay > self.settings.max_retry_after_seconds:
                        error = APIError(
                            "Server requested a long pause; resume after retry_at",
                            "RATE_LIMITED",
                            True,
                            delay,
                            status,
                        )
                        error.retry_at = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
                        raise error
                    raise TransientAPIError(
                        f"OpenAlex temporary HTTP {status}",
                        "RATE_LIMITED" if status == 429 else "API_SERVER_ERROR",
                        retry_after=delay,
                        status_code=status,
                    )
                if status != 200:
                    raise APIError(f"OpenAlex returned HTTP {status}", "API_HTTP_ERROR", status_code=status)
                try:
                    result = response.json()
                except ValueError:
                    raise APIError("OpenAlex response was not JSON", "API_INVALID_RESPONSE") from None
                if not isinstance(result, dict):
                    raise APIError("OpenAlex response was not an object", "API_INVALID_RESPONSE")
                return result
        raise AssertionError("Unreachable")

    async def get_page(self, params: dict[str, Any], cursor: str = "*") -> dict[str, Any]:
        if not self.api_key and cursor != "*":
            raise APIError(
                "OpenAlex requires OPENALEX_API_KEY for cursor pages after the first page", "API_KEY_REQUIRED"
            )
        if "api_key" in params or "select" in params:
            raise ValueError("API keys and lossy select projection are not accepted in page parameters")
        request = dict(params, per_page=100, cursor=cursor)
        is_search = any(key.startswith("search") for key in request) or ".search:" in str(
            request.get("filter", "")
        )
        cost = self.settings.estimated_search_cost_usd if is_search else self.settings.estimated_list_cost_usd
        result = await self._request("works", request, cost)
        if not isinstance(result.get("results"), list) or not isinstance(result.get("meta"), dict):
            raise APIError("OpenAlex page missing results or meta", "API_INVALID_RESPONSE")
        if "count" not in result["meta"] or "next_cursor" not in result["meta"]:
            raise APIError("OpenAlex page missing count or next_cursor", "API_INVALID_RESPONSE")
        if any(not isinstance(work, dict) or not work.get("id") for work in result["results"]):
            raise APIError("OpenAlex page contains a work without an ID", "API_INVALID_RESPONSE")
        return result

    async def get_work(self, identifier: str) -> dict[str, Any]:
        normalized = normalize_identifier(identifier)
        result = await self._request("works/" + quote(normalized, safe=":/"), {}, 0)
        if not result.get("id"):
            raise APIError("OpenAlex work response missing ID", "API_INVALID_RESPONSE")
        return result
