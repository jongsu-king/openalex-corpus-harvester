"""Bounded async PDF downloads; original bytes and distinct versions survive.

Only explicitly supplied PDF URLs are requested. No access control bypass,
HTML scraping, CAPTCHA handling, or guessed publisher endpoints are used.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx

from ..api.client import BudgetExceeded, BudgetJournal, retry_after_seconds
from .validator import validate_pdf


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def safe_url(url: str) -> str:
    parsed = urlsplit(url)
    query = [
        (
            key,
            "[REDACTED]"
            if key.lower()
            in {
                "api_key",
                "apikey",
                "access_token",
                "token",
                "key",
                "signature",
                "x-amz-signature",
                "x-amz-credential",
                "x-amz-security-token",
            }
            else value,
        )
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
    ]
    host = parsed.hostname or ""
    if parsed.port:
        host += f":{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path, urlencode(query), ""))


def _content_url(url: str) -> bool:
    return (urlsplit(url).hostname or "").lower() == "content.openalex.org"


def pdf_candidates(work: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    content = work.get("content_urls") or {}
    if isinstance(content, dict) and isinstance(content.get("pdf"), str):
        best = work.get("best_oa_location") or {}
        candidates.append(
            {
                "url": content["pdf"],
                "source": "openalex_content",
                "license": best.get("license"),
                "version": None,
                "is_oa": (work.get("open_access") or {}).get("is_oa"),
                "license_source": "best_oa_location; archived version license unverified",
            }
        )
    for key in ("best_oa_location", "primary_location"):
        location = work.get(key) or {}
        if isinstance(location, dict) and location.get("pdf_url"):
            candidates.append(
                {
                    "url": location["pdf_url"],
                    "source": key,
                    "license": location.get("license"),
                    "version": location.get("version"),
                    "is_oa": location.get("is_oa"),
                    "location": location,
                }
            )
    for index, location in enumerate(work.get("locations") or []):
        if isinstance(location, dict) and location.get("is_oa") is True and location.get("pdf_url"):
            candidates.append(
                {
                    "url": location["pdf_url"],
                    "source": f"locations[{index}]",
                    "license": location.get("license"),
                    "version": location.get("version"),
                    "is_oa": True,
                    "location": location,
                }
            )
    # Retain order; equal bytes from distinct URLs are recorded as separate provenance.
    result: list[dict[str, Any]] = []
    for candidate in candidates:
        url = candidate["url"]
        if isinstance(url, str) and url not in seen:
            seen.add(url)
            result.append(candidate)
    return result


class _DownloadError(RuntimeError):
    def __init__(self, status: str, reason: str, retryable: bool = False, delay: float = 0):
        self.status, self.reason, self.retryable, self.delay = status, reason, retryable, delay
        super().__init__(reason)


class PDFDownloader:
    def __init__(
        self,
        settings: Any,
        api_key: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        state_dir: Path | None = None,
    ):
        self.settings = settings
        self.api_key = api_key or os.environ.get("OPENALEX_API_KEY") or None
        self.transport = transport
        self.state_dir = Path(state_dir) if state_dir else None
        self.client: httpx.AsyncClient | None = None
        self.semaphore = asyncio.Semaphore(settings.concurrency)
        self._count_lock = asyncio.Lock()
        self._work_locks: dict[str, asyncio.Lock] = {}
        self.download_count = 0

    async def __aenter__(self) -> "PDFDownloader":
        self.client = httpx.AsyncClient(
            timeout=self.settings.timeout,
            transport=self.transport,
            follow_redirects=False,
            headers={
                "User-Agent": self.settings.user_agent,
                "Accept": "application/pdf",
                "Accept-Encoding": "identity",
            },
            limits=httpx.Limits(max_connections=self.settings.concurrency),
        )
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self.client:
            await self.client.aclose()

    def _journal(self, paper_dir: Path) -> BudgetJournal:
        return BudgetJournal(self.state_dir or paper_dir.parent.parent / "network_state")

    async def download(self, work: dict[str, Any], paper_dir: Path) -> dict[str, Any]:
        paper_dir = Path(paper_dir).resolve()
        lock = self._work_locks.setdefault(str(paper_dir), asyncio.Lock())
        async with lock, self.semaphore:
            return await self._download(work, paper_dir)

    async def _download(self, work: dict[str, Any], paper_dir: Path) -> dict[str, Any]:
        if self.client is None:
            raise RuntimeError("Use PDFDownloader as an async context manager")
        paper_dir.mkdir(parents=True, exist_ok=True)
        candidates = pdf_candidates(work)
        attempts: list[dict[str, Any]] = []
        warnings: list[str] = []
        manifest_path = paper_dir / "download_manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"versions": []}
        except (ValueError, OSError):
            return self._failure(
                "DOWNLOAD_FAILED", attempts, ["download_manifest_unreadable; original preserved"], False
            )
        refresh_existing = getattr(self.settings, "refresh_existing", False)
        # Retrieval records are append-only. Prefer the latest successful
        # version at a known URL after an explicit refresh; historical bytes
        # remain available in the manifest and content-addressed directory.
        cached_versions = [] if refresh_existing else reversed(manifest.get("versions", []))
        for version in cached_versions:
            if any(
                version.get("url_sha256") == hashlib.sha256(c["url"].encode()).hexdigest() for c in candidates
            ):
                path = paper_dir / version["relative_path"]
                valid = validate_pdf(path)
                if valid["status"] == "VALID" and valid["sha256"] == version.get("sha256"):
                    return {
                        **valid,
                        **version,
                        "status": "REUSED",
                        "path": str(path),
                        "attempts": attempts,
                        "warnings": warnings,
                        "retrieval_status": "reused",
                        "retryable": False,
                    }
                warnings.append("previous_version_checksum_or_integrity_failed:" + version["relative_path"])
        original = paper_dir / "original.pdf"
        if original.exists() and not manifest.get("versions") and not refresh_existing:
            valid = validate_pdf(original)
            if valid["status"] == "VALID":
                # An imported PDF without download provenance is retained, never falsely assigned a URL.
                warnings.append("existing_pdf_has_no_download_provenance")
                return {
                    **valid,
                    "status": "REUSED",
                    "path": str(original),
                    "source_url": None,
                    "license": None,
                    "version": None,
                    "attempts": attempts,
                    "warnings": warnings,
                    "retrieval_status": "unverified",
                    "retryable": False,
                }
        if not candidates:
            return self._failure("PDF_NOT_AVAILABLE", attempts, warnings, False)
        async with self._count_lock:
            if self.download_count >= self.settings.max_downloads:
                return self._failure(
                    "RATE_LIMITED", attempts, warnings + ["configured_download_count_cap"], True
                )
            self.download_count += 1
        for candidate in candidates:
            url = candidate["url"]
            if urlsplit(url).scheme not in {"http", "https"}:
                attempts.append(
                    {
                        "source_url": safe_url(url),
                        "status": "ACCESS_RESTRICTED",
                        "reason": "unsupported_url_scheme",
                    }
                )
                continue
            if candidate.get("is_oa") is False and not _content_url(url):
                attempts.append(
                    {
                        "source_url": safe_url(url),
                        "status": "ACCESS_RESTRICTED",
                        "reason": "source_explicitly_not_open_access",
                    }
                )
                continue
            allowed = self.settings.allowed_licenses
            if allowed is not None and candidate.get("license") not in allowed:
                attempts.append(
                    {
                        "source_url": safe_url(url),
                        "status": "LICENSE_RESTRICTED",
                        "reason": "license_not_in_allowlist",
                    }
                )
                continue
            url_hash = hashlib.sha256(url.encode()).hexdigest()
            partial_dir = paper_dir / ".downloads"
            partial_dir.mkdir(exist_ok=True)
            part = partial_dir / f"{url_hash}.part"
            receipt = partial_dir / f"{url_hash}.json"
            for attempt_index in range(1, self.settings.max_attempts + 1):
                attempt = {
                    "source_url": safe_url(url),
                    "source": candidate["source"],
                    "license": candidate.get("license"),
                    "version": candidate.get("version"),
                    "attempt": attempt_index,
                    "started_at": _now(),
                }
                try:
                    details = await self._stream(url, part, receipt, self._journal(paper_dir))
                    attempt.update(details)
                    validation = validate_pdf(part)
                    if validation["status"] != "VALID":
                        # Retain rejected bytes as .part and record why; never publish an invalid .pdf.
                        raise _DownloadError(
                            validation["status"], validation.get("missing_reason", "invalid_pdf")
                        )
                    sha = validation["sha256"]
                    versions_dir = paper_dir / "pdf_versions"
                    versions_dir.mkdir(exist_ok=True)
                    destination = versions_dir / f"{sha}.pdf"
                    if destination.exists():
                        prior = validate_pdf(destination)
                        if prior["status"] != "VALID" or prior["sha256"] != sha:
                            # Keep the corrupted bytes as evidence, then restore the
                            # expected content-addressed path from validated bytes.
                            quarantine = destination.with_name(
                                sha + ".corrupt." + str(prior.get("sha256")) + ".bin"
                            )
                            if not quarantine.exists():
                                os.replace(destination, quarantine)
                            else:
                                destination.unlink()
                            os.replace(part, destination)
                            warnings.append("corrupted_cached_version_quarantined_and_redownloaded")
                        else:
                            part.unlink(missing_ok=True)
                    else:
                        os.replace(part, destination)
                    if not original.exists():
                        tmp = paper_dir / "original.pdf.part"
                        shutil.copyfile(destination, tmp)
                        os.replace(tmp, original)
                    elif validate_pdf(original)["status"] != "VALID":
                        warnings.append("original_pdf_corrupted_preserved; validated_version_available")
                    record = {
                        **validation,
                        "source_url": safe_url(url),
                        "final_url": details.get("final_url"),
                        "url_sha256": url_hash,
                        "relative_path": str(destination.relative_to(paper_dir)),
                        "license": candidate.get("license"),
                        "version": candidate.get("version"),
                        "source": candidate["source"],
                        "downloaded_at": _now(),
                        "http_status": details.get("http_status"),
                        "content_type": details.get("content_type"),
                        "validation_status": "passed",
                    }
                    if candidate.get("license_source"):
                        record["license_source"] = candidate["license_source"]
                        warnings.append("archived_pdf_version_and_license_unverified")
                    if candidate.get("license") is None:
                        warnings.append("license_not_provided_by_source")
                    attempt.update(status="DOWNLOADED", sha256=sha, finished_at=_now())
                    attempts.append(attempt)
                    manifest.setdefault("versions", []).append(record)
                    manifest.setdefault("attempts", []).extend(attempts)
                    _atomic_json(manifest_path, manifest)
                    receipt.unlink(missing_ok=True)
                    return {
                        **record,
                        "status": "DOWNLOADED",
                        "path": str(destination),
                        "attempts": attempts,
                        "warnings": warnings,
                        "retrieval_status": "downloaded",
                        "retryable": False,
                    }
                except BudgetExceeded:
                    error = _DownloadError("RATE_LIMITED", "content_budget_exhausted_or_disabled", False)
                except _DownloadError as exc:
                    error = exc
                except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
                    error = _DownloadError("DOWNLOAD_FAILED", type(exc).__name__, True)
                except OSError as exc:
                    error = _DownloadError("DOWNLOAD_FAILED", "filesystem_error:" + type(exc).__name__, True)
                attempt.update(
                    status=error.status,
                    reason=error.reason,
                    retryable=error.retryable,
                    retry_after_seconds=error.delay,
                    finished_at=_now(),
                )
                attempts.append(attempt)
                if not error.retryable or attempt_index >= self.settings.max_attempts:
                    break
                if error.delay > self.settings.max_retry_after_seconds:
                    break
                await asyncio.sleep(
                    max(error.delay, random.uniform(0, min(10, 0.5 * 2 ** (attempt_index - 1))))
                )
        manifest.setdefault("attempts", []).extend(attempts)
        _atomic_json(manifest_path, manifest)
        statuses = {a.get("status") for a in attempts}
        # Report the actionable cause; all individual failures remain in attempts.
        status = next(
            (
                s
                for s in (
                    "RATE_LIMITED",
                    "DOWNLOAD_FAILED",
                    "INVALID_PDF",
                    "ENCRYPTED_PDF",
                    "ACCESS_RESTRICTED",
                    "LICENSE_RESTRICTED",
                )
                if s in statuses
            ),
            "PDF_NOT_AVAILABLE",
        )
        retryable = any(a.get("retryable") for a in attempts) or status == "RATE_LIMITED"
        return self._failure(status, attempts, warnings, retryable)

    @staticmethod
    def _failure(
        status: str, attempts: list[dict[str, Any]], warnings: list[str], retryable: bool
    ) -> dict[str, Any]:
        delay = max((a.get("retry_after_seconds", 0) for a in attempts), default=0)
        return {
            "status": status,
            "path": None,
            "sha256": None,
            "page_count": None,
            "source_url": None,
            "license": None,
            "version": None,
            "attempts": attempts,
            "warnings": warnings,
            "missing_reason": status,
            "retrieval_status": "failed" if attempts else "not_available",
            "validation_status": "not_run",
            "retryable": retryable,
            "retry_at": (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
            if delay
            else None,
        }

    async def _stream(self, url: str, part: Path, receipt: Path, journal: BudgetJournal) -> dict[str, Any]:
        assert self.client is not None
        prior = {}
        if receipt.exists() and part.exists():
            try:
                prior = json.loads(receipt.read_text())
            except (ValueError, OSError):
                pass
        offset = part.stat().st_size if part.exists() and prior.get("validator") else 0
        headers = {}
        if offset:
            headers.update(Range=f"bytes={offset}-", **{"If-Range": prior["validator"]})
        current = url
        for _ in range(6):
            request_headers = dict(headers)
            if _content_url(current):
                if urlsplit(current).scheme != "https":
                    raise _DownloadError("ACCESS_RESTRICTED", "content_credentials_require_https")
                if not self.api_key:
                    raise _DownloadError("ACCESS_RESTRICTED", "openalex_content_api_key_required")
                journal.reserve_content(
                    self.settings.content_cost_usd,
                    self.settings.max_content_downloads,
                    self.settings.max_content_cost_usd,
                )
                request_headers["Authorization"] = "Bearer " + self.api_key
            async with self.client.stream("GET", current, headers=request_headers) as response:
                status = response.status_code
                if status in (301, 302, 303, 307, 308):
                    location = response.headers.get("Location")
                    if not location:
                        raise _DownloadError("DOWNLOAD_FAILED", "redirect_missing_location")
                    target = urljoin(current, location)
                    if urlsplit(target).scheme not in {"http", "https"}:
                        raise _DownloadError("ACCESS_RESTRICTED", "redirect_scheme_rejected")
                    if urlsplit(current).scheme == "https" and urlsplit(target).scheme == "http":
                        raise _DownloadError("ACCESS_RESTRICTED", "insecure_redirect_rejected")
                    current = target
                    continue
                if status in (401, 403, 451):
                    raise _DownloadError("ACCESS_RESTRICTED", f"http_{status}")
                if status == 429:
                    raise _DownloadError(
                        "RATE_LIMITED",
                        "http_429",
                        True,
                        retry_after_seconds(response.headers.get("Retry-After")),
                    )
                if status >= 500:
                    raise _DownloadError(
                        "DOWNLOAD_FAILED",
                        f"http_{status}",
                        True,
                        retry_after_seconds(response.headers.get("Retry-After")),
                    )
                if status == 416 and offset:
                    # Partial could be stale or already complete; restart safely in the next bounded attempt.
                    receipt.unlink(missing_ok=True)
                    raise _DownloadError("DOWNLOAD_FAILED", "range_unsatisfiable_restart", True)
                if status not in (200, 206):
                    raise _DownloadError("DOWNLOAD_FAILED", f"http_{status}")
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower().strip()
                if content_type in {"text/html", "application/xhtml+xml", "application/json", "text/plain"}:
                    raise _DownloadError("INVALID_PDF", "non_pdf_content_type:" + content_type)
                append = status == 206 and offset > 0
                expected_total = None
                if status == 206:
                    match = re.fullmatch(
                        r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", "")
                    )
                    if not match or int(match[1]) != offset or int(match[2]) < int(match[1]):
                        receipt.unlink(missing_ok=True)
                        raise _DownloadError("DOWNLOAD_FAILED", "invalid_content_range_restart", True)
                    expected_total = int(match[3])
                    validator = response.headers.get("ETag") or response.headers.get("Last-Modified")
                    if offset and validator and validator != prior.get("validator"):
                        receipt.unlink(missing_ok=True)
                        raise _DownloadError("DOWNLOAD_FAILED", "range_validator_changed_restart", True)
                initial = offset if append else 0
                length = response.headers.get("Content-Length")
                if length and length.isdigit() and initial + int(length) > self.settings.max_file_bytes:
                    raise _DownloadError("DOWNLOAD_FAILED", "max_file_bytes_exceeded")
                if expected_total and expected_total > self.settings.max_file_bytes:
                    raise _DownloadError("DOWNLOAD_FAILED", "max_file_bytes_exceeded")
                etag = response.headers.get("ETag")
                validator = (
                    etag if etag and not etag.startswith("W/") else response.headers.get("Last-Modified")
                )
                _atomic_json(
                    receipt,
                    {"validator": validator, "source_url": safe_url(url), "final_url": safe_url(current)},
                )
                size, received = initial, 0
                with part.open("ab" if append else "wb") as stream:
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        received += len(chunk)
                        if size > self.settings.max_file_bytes:
                            raise _DownloadError("DOWNLOAD_FAILED", "max_file_bytes_exceeded")
                        stream.write(chunk)
                    stream.flush()
                    os.fsync(stream.fileno())
                if (
                    length
                    and length.isdigit()
                    and response.headers.get("Content-Encoding", "identity") == "identity"
                    and received != int(length)
                ):
                    raise _DownloadError("DOWNLOAD_FAILED", "content_length_mismatch", True)
                if expected_total is not None and size != expected_total:
                    raise _DownloadError("DOWNLOAD_FAILED", "range_total_mismatch", True)
                return {
                    "http_status": status,
                    "content_type": content_type,
                    "size_bytes": size,
                    "final_url": safe_url(current),
                    "resumed_from": initial,
                    "retrieval_time": _now(),
                    "rate_headers": {
                        k: v for k, v in response.headers.items() if k.startswith("x-ratelimit-")
                    },
                }
        raise _DownloadError("DOWNLOAD_FAILED", "too_many_redirects")
