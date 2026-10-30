"""Bounded queues, transactional crawl checkpoints, and immutable extraction runs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import multiprocessing
import os
import re
import time
import unicodedata
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import psutil
import yaml
from filelock import FileLock

from .config import Settings
from .database.repository import Repository
from .metadata import normalize_work
from .quality.checker import check_extraction, verify_manifest
from .quality.recovery import recovery_options, retry_at
from .utils import atomic_json, atomic_text, redact, sha256_file, utcnow

log = logging.getLogger("openalex_corpus")
SUCCESS = {"COMPLETE", "COMPLETE_WITH_WARNINGS"}
RANK = {"FAILED": 0, "PENDING_RETRY": 0, "PARTIAL": 1, "COMPLETE_WITH_WARNINGS": 2, "COMPLETE": 3}


def _parser_worker(pdf: str, destination: str, options: dict):
    # A disposable process contains native-library crashes, leaks and model RAM.
    # Native libraries also print directly to file descriptors. Preserve their
    # diagnostics per run while keeping CLI stdout machine-readable JSON.
    output_dir = Path(destination)
    with (output_dir / "parser_stdout.log").open("w") as stream:
        os.dup2(stream.fileno(), 1)
    with (output_dir / "parser_stderr.log").open("w") as stream:
        os.dup2(stream.fileno(), 2)
    try:
        from .parsers import parse_pdf

        result = parse_pdf(Path(pdf), Path(destination), options)
        atomic_json(Path(destination) / "worker_result.json", result)
    except BaseException as error:
        atomic_json(
            Path(destination) / "worker_error.json",
            {"code": type(error).__name__, "message": redact(str(error))},
        )


@contextmanager
def corpus_lock(settings: Settings):
    """One writer per corpus; SQLite still supports concurrent read-only queries."""
    settings.prepare()
    with FileLock(str(settings.data_dir / "state" / "writer.lock"), timeout=0):
        yield


class Pipeline:
    def __init__(self, settings: Settings, repository: Repository | None = None):
        self.settings = settings.prepare()
        self.repo = repository or Repository(settings.db_path)
        self._owns_repo = repository is None
        self.parse_slots = asyncio.Semaphore(settings.parsing.workers)
        self.work_locks = {}

    def close(self):
        if self._owns_repo:
            self.repo.conn.close()

    def paper_dir(self, work_id: str) -> Path:
        if not work_id.startswith("W") or not work_id[1:].isdigit():
            raise ValueError("Only valid OpenAlex Work IDs are accepted as filesystem keys")
        return self.settings.data_dir / "papers" / work_id

    def ingest(self, raw: dict) -> dict:
        encoded = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        digest = hashlib.sha256(encoded).hexdigest()
        # Preserve even a malformed/unidentified singleton before normalization.
        response_path = self.settings.data_dir / "raw_responses" / f"{digest}.json"
        if not response_path.exists():
            atomic_json(response_path, raw)
        try:
            normalized = normalize_work(raw)
            wid = normalized["openalex_id"].rsplit("/", 1)[-1]
            directory = self.paper_dir(wid)
        except Exception as error:
            atomic_json(
                response_path.with_suffix(".status.json"),
                {
                    "status": "FAILED",
                    "source": "openalex",
                    "retrieval_status": "retrieved",
                    "validation_status": "parse_failed",
                    "missing_reason": redact(str(error)),
                    "raw_path": str(response_path),
                },
            )
            self._error(None, "metadata", error, retryable=False)
            raise
        directory.mkdir(parents=True, exist_ok=True)
        raw_path = directory / "metadata_history" / f"{digest}.json"
        if raw_path.exists():
            try:
                retained = json.loads(raw_path.read_text())
            except (ValueError, OSError) as error:
                raise ValueError("RAW_METADATA_CORRUPT: immutable revision cannot be read") from error
            if retained != raw:
                raise ValueError(
                    "RAW_METADATA_CORRUPT: content-addressed revision differs from received payload"
                )
            trusted = self.repo.conn.execute(
                "SELECT sha256 FROM artifacts WHERE path=? AND kind='metadata_raw'",
                (str(raw_path.resolve()),),
            ).fetchone()
            if trusted and trusted["sha256"] != sha256_file(raw_path):
                raise ValueError("RAW_METADATA_CHECKSUM_MISMATCH: retained bytes changed")
        else:
            atomic_json(raw_path, raw)
        atomic_json(directory / "metadata_pending.json", {"raw_path": str(raw_path)})
        # The immutable revision is durable before the DB pointer is changed.
        info = self.repo.ingest_work(raw, normalized, raw_path)
        atomic_json(directory / "metadata_raw.json", raw)
        atomic_json(directory / "metadata.json", normalized)
        (directory / "metadata_pending.json").unlink(missing_ok=True)
        self.repo.upsert_artifact(
            wid, "metadata_raw", raw_path, sha256_file(raw_path), {"source": "openalex"}
        )
        return {"work_id": wid, "paper_dir": directory, "normalized": normalized, **info}

    def _error(self, wid: str | None, stage: str, error, attempt=1, retryable=True):
        code = getattr(error, "code", type(error).__name__)
        message = redact(str(error))
        self.repo.record_error(wid, stage, code, message, retryable, attempt)
        if wid and retryable:
            self.repo.enqueue_retry(
                wid, stage, code, attempt, getattr(error, "retry_at", None) or retry_at(attempt)
            )
        log.error(
            message,
            extra={
                "context": {
                    "work_id": wid,
                    "stage": stage,
                    "code": code,
                    "attempt": attempt,
                    "retryable": retryable,
                }
            },
        )

    async def _run_parser(self, pdf: Path, staging: Path, options: dict) -> dict:
        async with self.parse_slots:
            context = multiprocessing.get_context("spawn")
            process = context.Process(target=_parser_worker, args=(str(pdf), str(staging), options))
            started = time.monotonic()
            process.start()
            limit = self.settings.parsing.worker_memory_mb * 1024**2
            reason = None
            try:
                while process.is_alive():
                    if time.monotonic() - started > self.settings.parsing.timeout_seconds:
                        reason = "PARSER_TIMEOUT"
                        break
                    try:
                        proc = psutil.Process(process.pid)
                        rss = proc.memory_info().rss + sum(
                            child.memory_info().rss for child in proc.children(recursive=True)
                        )
                        if rss > limit:
                            reason = "PARSER_MEMORY_LIMIT"
                            break
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                    await asyncio.sleep(0.2)
                if reason:
                    raise RuntimeError(reason)
                process.join(timeout=1)
                result_path = staging / "worker_result.json"
                if not result_path.exists() or process.exitcode:
                    error_path = staging / "worker_error.json"
                    detail = (
                        error_path.read_text() if error_path.exists() else f"exit code {process.exitcode}"
                    )
                    raise RuntimeError(f"PARSER_PROCESS_FAILED: {detail}")
                return json.loads(result_path.read_text())
            finally:
                if process.is_alive():
                    try:
                        for child in psutil.Process(process.pid).children(recursive=True):
                            child.terminate()
                    except psutil.Error:
                        pass
                    process.terminate()
                    process.join(timeout=2)
                    if process.is_alive():
                        process.kill()
                        process.join(timeout=2)
                process.close()

    def _validate_run(self, directory: Path, run_dir: Path, manifest: dict):
        run_dir = run_dir.resolve()
        if not run_dir.is_relative_to(directory.resolve()):
            raise ValueError("Unsafe extraction pointer")
        manifest_path = run_dir / "manifest.json"
        trusted = self.repo.conn.execute(
            "SELECT sha256 FROM artifacts WHERE kind='extraction_manifest' AND path=?", (str(manifest_path),)
        ).fetchone()
        if trusted and trusted["sha256"] != sha256_file(manifest_path):
            raise ValueError("EXTRACTION_MANIFEST_CHECKSUM_MISMATCH")
        check = verify_manifest(manifest, run_dir)
        if not check["valid"]:
            raise ValueError(f"Selected extraction is damaged: {check['failures']}")
        source = Path(manifest["pdf_path"])
        if not source.is_file() or sha256_file(source) != manifest["pdf_sha256"]:
            raise ValueError("SOURCE_PDF_CHECKSUM_MISMATCH")
        original = self.repo.conn.execute(
            "SELECT path,sha256 FROM artifacts WHERE work_id=? AND kind='original_pdf'", (directory.name,)
        ).fetchone()
        if original and (
            not Path(original["path"]).is_file() or sha256_file(original["path"]) != original["sha256"]
        ):
            raise ValueError("ORIGINAL_PDF_CHECKSUM_MISMATCH")

    def _publish(self, directory: Path, run_dir: Path, manifest: dict):
        self._validate_run(directory, run_dir, manifest)
        # current.json is the sole authoritative promotion commit. Convenience
        # links can be reconciled after an interruption at any intermediate step.
        pointer = {
            "run": str(run_dir.relative_to(directory)),
            "status": manifest["status"],
            "pdf_sha256": manifest["pdf_sha256"],
            "selected_at": utcnow(),
        }
        atomic_json(directory / "current.json", pointer)
        names = {
            "paper.md",
            "document.json",
            "pages.json",
            "references.json",
            "quality_report.json",
            "manifest.json",
            "figures",
            "tables",
            "equations",
            "page_images",
        }
        names.update(child.name for child in run_dir.iterdir() if child.is_dir())
        for name in names:
            target = run_dir / name
            if target.exists():
                link = directory / ("." + name + ".link")
                link.unlink(missing_ok=True)
                link.symlink_to(target.relative_to(directory), target_is_directory=target.is_dir())
                existing = directory / name
                # Never delete a pre-existing directory belonging to the user.
                if existing.is_dir() and not existing.is_symlink():
                    log.warning(
                        "Convenience link conflicts with existing directory",
                        extra={"context": {"path": str(existing)}},
                    )
                    link.unlink()
                else:
                    os.replace(link, existing)
            else:
                existing = directory / name
                if existing.is_symlink() and str(os.readlink(existing)).startswith("extractions/"):
                    existing.unlink()
        reason = {"PARTIAL": "unresolved_extraction_elements", "FAILED": "extraction_failed"}.get(
            manifest["status"]
        )
        self.repo.set_work_status(directory.name, manifest["status"], reason)

    async def parse(self, wid: str, pdf: Path, force=False, download: dict | None = None) -> dict:
        from .download.validator import validate_pdf

        validation = validate_pdf(pdf)
        if validation.get("status") not in {"VALID", "VALID_PDF", "OK"}:
            # Validator contracts additionally expose valid=True where supported.
            if not validation.get("valid"):
                raise ValueError(f"Cannot parse invalid PDF: {validation}")
        digest = sha256_file(pdf)
        directory = self.paper_dir(wid)
        pointer_file = directory / "current.json"
        previous = None
        if pointer_file.exists():
            try:
                candidate = json.loads(pointer_file.read_text())
                previous_dir = directory / candidate["run"]
                previous_manifest = json.loads((previous_dir / "manifest.json").read_text())
                self._validate_run(directory, previous_dir, previous_manifest)
                previous = {**candidate, "status": previous_manifest["status"]}
            except (ValueError, OSError, KeyError) as error:
                self.repo.set_work_status(wid, "PARTIAL", "selected_extraction_integrity_failed")
                self._error(wid, "verify", error, retryable=False)
        if (
            not force
            and previous
            and previous.get("pdf_sha256") == digest
            and previous.get("status") in SUCCESS
        ):
            return {"status": previous["status"], "reused": True, "run": previous["run"]}
        self.repo.set_job("parse", wid, "RUNNING")
        options = self.settings.parsing.model_dump()
        attempts = 0
        best = None
        while options is not None:
            run_name = utcnow().replace(":", "-") + "-" + uuid.uuid4().hex[:8]
            staging = directory / "extractions" / (".staging-" + run_name)
            staging.mkdir(parents=True)
            atomic_json(
                staging / "intent.json",
                {
                    "work_id": wid,
                    "pdf_path": str(pdf),
                    "pdf_sha256": digest,
                    "started_at": utcnow(),
                    "options": options,
                },
            )
            try:
                result = await self._run_parser(pdf, staging, options)
                source_title = self.repo.get_work(wid)["metadata"].get("title")
                tei_title = result.get("tei_metadata", {}).get("title")
                if isinstance(source_title, str) and isinstance(tei_title, str):

                    def title_key(value):
                        return re.sub(r"\W+", " ", unicodedata.normalize("NFKC", value).casefold()).strip()

                    if title_key(source_title) != title_key(tei_title):
                        comparison = {
                            "field": "title",
                            "validation_status": "unverified",
                            "openalex": source_title,
                            "tei_extracted": tei_title,
                            "source": "openalex_and_tei",
                            "action": "retain_both_no_substitution",
                        }
                        result.setdefault("metadata_comparisons", []).append(comparison)
                        result.setdefault("warnings", []).append(
                            {
                                "code": "METADATA_TITLE_DISAGREEMENT",
                                "message": "OpenAlex title and TEI title differ; inspect source, both retained without replacement.",
                                **comparison,
                            }
                        )
                quality = check_extraction(result, validation["page_count"], staging)
            except Exception as error:
                self._error(wid, "parse", error, attempts + 1)
                result = {
                    "engine": None,
                    "pages": [],
                    "markdown": "",
                    "status": "FAILED",
                    "warnings": [],
                    "errors": [{"code": type(error).__name__, "message": redact(str(error))}],
                }
                quality = check_extraction(result, validation["page_count"], staging)
            row = self.repo.get_work(wid)
            result.update(run_id=run_name, pdf_path=str(pdf), pdf_sha256=digest)
            meta = row.get("metadata", row.get("metadata_json", {}))
            raw = row.get("raw", row.get("raw_json", {}))
            front = {
                "openalex_id": wid,
                "doi": meta.get("doi"),
                "title": meta.get("title"),
                "publication_year": meta.get("publication_year"),
                "authors": raw.get("authorships"),
                "source": raw.get("primary_location"),
                "pdf_source": (download or {}).get("source_url"),
                "pdf_sha256": digest,
                "parse_status": quality["status"],
                "parser_version": result.get("parser_versions"),
                "extracted_at": utcnow(),
            }
            atomic_text(
                staging / "paper.md",
                "---\n"
                + yaml.safe_dump(front, allow_unicode=True, sort_keys=False)
                + "---\n\n"
                + result.get("markdown", ""),
            )
            for name, value in [("quality_report.json", quality), ("result.json", result)]:
                atomic_json(staging / name, value)
            for name, value in [
                ("document.json", result),
                ("pages.json", result.get("pages", [])),
                ("references.json", result.get("references", [])),
            ]:
                if not (staging / name).exists():
                    atomic_json(staging / name, value)
            files = [
                {
                    "path": str(p.relative_to(staging)),
                    "sha256": sha256_file(p),
                    "size_bytes": p.stat().st_size,
                }
                for p in sorted(staging.rglob("*"))
                if p.is_file()
            ]
            manifest = {
                "work_id": wid,
                "status": quality["status"],
                "pdf_path": str(pdf),
                "pdf_sha256": digest,
                "created_at": utcnow(),
                "files": files,
                "options": options,
            }
            atomic_json(staging / "manifest.json", manifest)
            final = staging.with_name(run_name)
            os.replace(staging, final)
            self.repo.record_extraction(wid, result, final, quality)
            self.repo.upsert_artifact(
                wid,
                "extraction_manifest",
                final / "manifest.json",
                sha256_file(final / "manifest.json"),
                {"status": quality["status"]},
            )
            with self.repo.transaction():
                for artifact in manifest["files"]:
                    self.repo.upsert_artifact(
                        wid,
                        "extraction_file",
                        final / artifact["path"],
                        artifact["sha256"],
                        {"run_id": run_name},
                    )
            if best is None or RANK.get(quality["status"], 0) > RANK.get(best["status"], 0):
                best = {
                    "status": quality["status"],
                    "run": str(final.relative_to(directory)),
                    "manifest": manifest,
                }
            if quality["status"] in SUCCESS:
                break
            next_options = recovery_options(options, quality, attempts)
            attempts += 1
            options = next_options
        old_rank = RANK.get(previous.get("status"), -1) if previous else -1
        # A failed/lower-quality run cannot replace an earlier successful result.
        if RANK.get(best["status"], 0) >= old_rank:
            self._publish(directory, directory / best["run"], best["manifest"])
        elif previous:
            self.repo.set_work_status(wid, previous["status"])
            best["previous_result_preserved"] = previous["run"]
        self.repo.set_job("parse", wid, "COMPLETED" if best["status"] in SUCCESS else "FAILED")
        if best["status"] in SUCCESS:
            self.repo.conn.execute(
                "UPDATE retry_queue SET status='COMPLETED',updated_at=? WHERE work_id=? AND stage IN ('parse','reconcile')",
                (utcnow(), wid),
            )
        if best["status"] not in SUCCESS:
            self.repo.enqueue_retry(wid, "parse", "QUALITY_FAILED", 0, retry_at(0))
        return {k: v for k, v in best.items() if k != "manifest"}

    async def process_work(self, raw: dict, downloader=None, parse_markdown=False, force=False) -> dict:
        key = str(raw.get("id"))
        lock = self.work_locks.setdefault(key, asyncio.Lock())
        async with lock:
            return await self._process_work(raw, downloader, parse_markdown, force)

    async def _process_work(self, raw: dict, downloader=None, parse_markdown=False, force=False) -> dict:
        info = self.ingest(raw)
        wid = info["work_id"]
        directory = self.paper_dir(wid)
        try:
            if downloader is None:
                row = self.repo.get_work(wid)
                if row["status"] == "PARTIAL" and not row["missing_reason"]:
                    self.repo.set_work_status(wid, "PARTIAL", "pdf_not_requested")
                return {"work_id": wid, "status": self.repo.get_work(wid)["status"], "metadata_only": True}
            self.repo.set_job("download", wid, "RUNNING")
            result = await downloader.download(raw, directory)
            atomic_json(directory / "download_report.json", result)
            for attempt in result.get("attempts", []):
                if isinstance(attempt, dict) and attempt.get("status") not in {
                    None,
                    "DOWNLOADED",
                    "REUSED",
                    "VALID",
                    200,
                    206,
                }:
                    log.info(
                        "PDF candidate attempt",
                        extra={"context": {"work_id": wid, "stage": "download", **redact(attempt)}},
                    )
            if result["status"] not in {"DOWNLOADED", "REUSED"}:
                transient = result.get(
                    "retryable", result["status"] in {"DOWNLOAD_FAILED", "RATE_LIMITED", "BUDGET_EXCEEDED"}
                )
                state = "PENDING_RETRY" if transient else "PDF_UNAVAILABLE"
                self.repo.set_job("download", wid, "FAILED", error_code=result["status"])
                current = self.repo.get_work(wid)
                if current["status"] not in SUCCESS:
                    self.repo.set_work_status(wid, state, result["status"])
                self.repo.record_error(
                    wid,
                    "download",
                    result["status"],
                    "See download_report.json candidate evidence",
                    transient,
                )
                if transient:
                    self.repo.enqueue_retry(
                        wid, "download", result["status"], 0, result.get("retry_at") or retry_at(0)
                    )
                return {"work_id": wid, "status": state, "download_status": result["status"]}
            pdf = Path(result["path"])
            self.repo.upsert_artifact(wid, "pdf", pdf, result["sha256"], result)
            original = directory / "original.pdf"
            if original.is_file():
                existing_original = self.repo.conn.execute(
                    "SELECT id FROM artifacts WHERE work_id=? AND kind='original_pdf'", (wid,)
                ).fetchone()
                if not existing_original:
                    self.repo.upsert_artifact(
                        wid, "original_pdf", original, sha256_file(original), {"source": "first_download"}
                    )
            self.repo.set_job("download", wid, "COMPLETED")
            self.repo.conn.execute(
                "UPDATE retry_queue SET status='COMPLETED',updated_at=? WHERE work_id=? AND stage='download'",
                (utcnow(), wid),
            )
            if parse_markdown:
                parsed = await self.parse(wid, pdf, force=force, download=result)
                return {"work_id": wid, **parsed}
            if self.repo.get_work(wid)["status"] not in SUCCESS:
                self.repo.set_work_status(wid, "PARTIAL", "markdown_not_requested")
            return {
                "work_id": wid,
                "status": self.repo.get_work(wid)["status"],
                "download_status": result["status"],
            }
        except Exception as error:
            self._error(
                wid,
                "parse" if parse_markdown else "download",
                error,
                retryable=getattr(error, "retryable", True),
            )
            current = self.repo.get_work(wid)
            if current["status"] not in SUCCESS:
                self.repo.set_work_status(wid, "PENDING_RETRY", getattr(error, "code", type(error).__name__))
            return {"work_id": wid, "status": "PENDING_RETRY", "error": redact(str(error))}

    async def harvest(
        self,
        spec,
        max_works=100,
        download_pdf=False,
        parse_markdown=False,
        resume=True,
        client=None,
        downloader=None,
    ) -> dict:
        from contextlib import AsyncExitStack

        from .api.client import OpenAlexClient
        from .api.pagination import next_cursor as guarded_next_cursor
        from .download.pdf_downloader import PDFDownloader

        params = spec.params()
        if not 1 <= max_works <= 10000:
            raise ValueError("max_works must be in 1..10000; use Snapshot for bulk acquisition")
        if not (set(params) - {"per_page"}):
            raise ValueError(
                "Provide a bounded search/filter. Whole-database acquisition should use the OpenAlex Snapshot."
            )
        run = self.repo.begin_crawl(
            {
                "params": params,
                "download_pdf": download_pdf or parse_markdown,
                "parse_markdown": parse_markdown,
            },
            resume=resume,
        )
        run_id = run["id"]
        checkpoint = self.repo.get_checkpoint(run_id)
        resume_at = checkpoint.get("extras", {}).get("retry_at")
        if resume_at and datetime.fromisoformat(resume_at) > datetime.now(timezone.utc):
            self.repo.finish_crawl(run_id, "PAUSED")
            return {
                "run_id": run_id,
                "status": "PAUSED",
                "retry_at": resume_at,
                "reason": "server_or_budget_pause",
            }
        cursor = checkpoint.get("cursor") or "*"
        offset = checkpoint.get("offset", 0)
        processed = checkpoint.get("processed", 0)
        total = checkpoint.get("total_count")
        completed_now = 0
        outcomes = []
        seen_cursors = set(checkpoint.get("extras", {}).get("visited_cursors", []))
        async with AsyncExitStack() as stack:
            if client is None:
                client = await stack.enter_async_context(
                    OpenAlexClient(self.settings.api, self.settings.data_dir / "state")
                )
            if downloader is None and (download_pdf or parse_markdown):
                downloader = await stack.enter_async_context(
                    PDFDownloader(
                        self.settings.download,
                        api_key=os.getenv("OPENALEX_API_KEY"),
                        state_dir=self.settings.data_dir / "state",
                    )
                )
            try:
                while completed_now < max_works:
                    cursor_hash = hashlib.sha256(cursor.encode()).hexdigest()
                    page_path = (
                        self.settings.data_dir / "crawls" / str(run_id) / "pages" / f"{cursor_hash}.json"
                    )
                    if page_path.exists():
                        prior_hash = (
                            checkpoint.get("extras", {}).get("page_sha256")
                            if checkpoint.get("extras", {}).get("page_path") == str(page_path)
                            else None
                        )
                        receipt_path = page_path.with_suffix(".receipt.json")
                        if receipt_path.exists():
                            prior_hash = json.loads(receipt_path.read_text())["sha256"]
                        if prior_hash and sha256_file(page_path) != prior_hash:
                            raise ValueError("CRAWL_PAGE_CHECKSUM_MISMATCH: retained response changed")
                        page = json.loads(page_path.read_text())
                    else:
                        page = await client.get_page(params, cursor=cursor)
                        atomic_json(page_path, page)
                        atomic_json(
                            page_path.with_suffix(".receipt.json"),
                            {
                                "sha256": sha256_file(page_path),
                                "retrieved_at": utcnow(),
                                "params": params,
                                "cursor": cursor,
                                "rate_headers": getattr(client, "last_rate_headers", {}),
                            },
                        )
                    next_cursor = guarded_next_cursor(page, cursor)
                    results = page["results"]
                    total = page.get("meta", {}).get("count", total)
                    if offset > len(results):
                        raise ValueError("CHECKPOINT_OFFSET_INVALID: retained page does not match checkpoint")
                    while offset < len(results) and completed_now < max_works:
                        size = min(
                            self.settings.download.concurrency,
                            max_works - completed_now,
                            len(results) - offset,
                        )
                        batch = results[offset : offset + size]
                        batch_results = await asyncio.gather(
                            *[self.process_work(raw, downloader, parse_markdown) for raw in batch],
                            return_exceptions=True,
                        )
                        for raw, outcome in zip(batch, batch_results):
                            if isinstance(outcome, BaseException):
                                # Raw failed entries remain in the retained page and an error is never hidden.
                                self._error(None, "metadata", outcome, retryable=False)
                                raise outcome
                            self.repo.record_crawl_work(run_id, outcome["work_id"])
                            outcomes.append(outcome)
                        offset += len(batch)
                        processed += len(batch)
                        completed_now += len(batch)
                        self.repo.save_checkpoint(
                            run_id,
                            cursor,
                            offset,
                            processed,
                            total,
                            page_path=str(page_path),
                            page_sha256=sha256_file(page_path),
                        )
                    if offset == len(results):
                        if not results or not next_cursor:
                            unique = self.repo.conn.execute(
                                "SELECT COUNT(*) FROM crawl_work_ids WHERE run_id=?", (run_id,)
                            ).fetchone()[0]
                            audit = {
                                "unique_work_ids": unique,
                                "processed_occurrences": processed,
                                "reported_count": total,
                                "count_matches": unique == total,
                                "note": "Live API results may change; a mismatch requires review and is not proof of loss.",
                            }
                            self.repo.save_checkpoint(
                                run_id,
                                cursor,
                                offset,
                                processed,
                                total,
                                status="COMPLETED",
                                coverage_audit=audit,
                            )
                            self.repo.finish_crawl(run_id, "COMPLETED")
                            return {
                                "run_id": run_id,
                                "status": "COMPLETED",
                                "processed_this_run": completed_now,
                                "processed_total": processed,
                                "reported_total": total,
                                "coverage_audit": audit,
                                "outcomes": outcomes,
                            }
                        if next_cursor == cursor or next_cursor in seen_cursors:
                            raise ValueError(
                                "CURSOR_LOOP: source repeated a cursor; stopped without skipping a page"
                            )
                        seen_cursors.add(cursor)
                        cursor, offset = next_cursor, 0
                        self.repo.save_checkpoint(
                            run_id, cursor, offset, processed, total, visited_cursors=sorted(seen_cursors)
                        )
                self.repo.finish_crawl(run_id, "LIMIT_REACHED")
                return {
                    "run_id": run_id,
                    "status": "LIMIT_REACHED",
                    "processed_this_run": completed_now,
                    "processed_total": processed,
                    "reported_total": total,
                    "outcomes": outcomes,
                }
            except BaseException as error:
                resume_at = getattr(error, "retry_at", None)
                if resume_at:
                    self.repo.save_checkpoint(run_id, cursor, offset, processed, total, retry_at=resume_at)
                self.repo.finish_crawl(run_id, "PAUSED")
                if isinstance(error, (KeyboardInterrupt, asyncio.CancelledError)):
                    raise
                self._error(None, "crawl", error, retryable=getattr(error, "retryable", True))
                return {
                    "run_id": run_id,
                    "status": "PAUSED",
                    "processed_this_run": completed_now,
                    "reported_total": total,
                    "retry_at": resume_at,
                    "error": redact(str(error)),
                    "outcomes": outcomes,
                }

    def reconcile(self) -> dict:
        """Reconcile committed pointers, metadata revisions and abandoned work."""
        recovered, failures = [], []
        for directory in sorted((self.settings.data_dir / "papers").glob("W*")):
            if not directory.is_dir():
                continue
            try:
                raw_path = directory / "metadata_raw.json"
                pending = directory / "metadata_pending.json"
                existing = self.repo.get_work(directory.name)
                if pending.exists():
                    pending_raw = Path(json.loads(pending.read_text())["raw_path"])
                    if not pending_raw.resolve().is_relative_to(directory.resolve()):
                        raise ValueError("Unsafe metadata revision path")
                    self.ingest(json.loads(pending_raw.read_text()))
                elif existing:
                    self.ingest(existing["raw"])
                elif raw_path.exists():
                    self.ingest(json.loads(raw_path.read_text()))
                else:
                    revisions = sorted(
                        (directory / "metadata_history").glob("*.json"), key=lambda p: p.stat().st_mtime
                    )
                    if revisions:
                        self.ingest(json.loads(revisions[-1].read_text()))
                # Finished directories are durable commit intents. Register a
                # crash-orphaned run idempotently before selecting its result.
                candidates = []
                for final in sorted((directory / "extractions").glob("*")):
                    if final.name.startswith(".") or not (final / "manifest.json").is_file():
                        continue
                    manifest = json.loads((final / "manifest.json").read_text())
                    try:
                        self._validate_run(directory, final, manifest)
                    except (ValueError, OSError, KeyError) as error:
                        self._error(directory.name, "reconcile_historic_run", error, retryable=False)
                        continue
                    result = json.loads((final / "result.json").read_text())
                    quality = json.loads((final / "quality_report.json").read_text())
                    result["run_id"] = final.name
                    self.repo.record_extraction(directory.name, result, final, quality)
                    self.repo.upsert_artifact(
                        directory.name,
                        "extraction_manifest",
                        final / "manifest.json",
                        sha256_file(final / "manifest.json"),
                    )
                    candidates.append((RANK.get(manifest["status"], 0), final.name, final, manifest))
                pointer_path = directory / "current.json"
                if pointer_path.exists():
                    try:
                        pointer = json.loads(pointer_path.read_text())
                        run_dir = (directory / pointer["run"]).resolve()
                        manifest = json.loads((run_dir / "manifest.json").read_text())
                        self._publish(directory, run_dir, manifest)
                        recovered.append(directory.name)
                    except (ValueError, OSError, KeyError) as error:
                        if not candidates:
                            raise
                        self._error(directory.name, "reconcile", error, retryable=False)
                if candidates:
                    _, _, best_path, best_manifest = max(candidates)
                    self._publish(directory, best_path, best_manifest)
                for staging in (directory / "extractions").glob(".staging-*"):
                    # Preserve partial evidence; never silently delete an interrupted extraction.
                    self.repo.enqueue_retry(directory.name, "parse", "INTERRUPTED_EXTRACTION", 0, utcnow())
                for stage in ("download", "parse"):
                    job = self.repo.conn.execute(
                        f"SELECT status FROM {stage}_jobs WHERE work_id=?", (directory.name,)
                    ).fetchone()
                    if job and job["status"] == "RUNNING":
                        self.repo.set_job(
                            stage, directory.name, "PENDING_RETRY", error_code="INTERRUPTED_JOB"
                        )
                        self.repo.enqueue_retry(directory.name, stage, "INTERRUPTED_JOB", 0, utcnow())
            except Exception as error:
                failures.append({"work_id": directory.name, "error": redact(str(error))})
                if self.repo.get_work(directory.name):
                    self.repo.set_work_status(directory.name, "PARTIAL", "reconciliation_failed")
                    self._error(directory.name, "reconcile", error)
        return {"recovered": recovered, "failures": failures}

    def verify(self) -> dict:
        from .download.validator import validate_pdf

        results = []
        for row in self.repo.iter_works():
            wid = row["id"]
            directory = self.paper_dir(wid)
            errors = []
            for artifact in self.repo.conn.execute("SELECT * FROM artifacts WHERE work_id=?", (wid,)):
                path = Path(artifact["path"])
                if not path.exists():
                    errors.append({"code": "ARTIFACT_MISSING", "path": str(path)})
                elif artifact["sha256"] and sha256_file(path) != artifact["sha256"]:
                    errors.append({"code": "CHECKSUM_MISMATCH", "path": str(path)})
            pointer_path = directory / "current.json"
            if pointer_path.exists():
                try:
                    pointer = json.loads(pointer_path.read_text())
                    run_dir = (directory / pointer["run"]).resolve()
                    if not run_dir.is_relative_to(directory.resolve()):
                        raise ValueError("Unsafe run pointer")
                    manifest = json.loads((run_dir / "manifest.json").read_text())
                    errors.extend(verify_manifest(manifest, run_dir)["failures"])
                    pdf = Path(manifest["pdf_path"])
                    if not pdf.exists() or sha256_file(pdf) != manifest["pdf_sha256"]:
                        errors.append({"code": "ORIGINAL_PDF_CHANGED"})
                    else:
                        valid = validate_pdf(pdf)
                        extraction = json.loads((run_dir / "result.json").read_text())
                        quality = check_extraction(extraction, valid.get("page_count", 0), run_dir)
                        errors.extend(issue for issue in quality["issues"] if issue["severity"] == "error")
                except Exception as error:
                    errors.append({"code": "VERIFY_FAILED", "message": redact(str(error))})
            elif row["status"] in SUCCESS:
                errors.append({"code": "SUCCESS_WITHOUT_EXTRACTION"})
            if errors:
                self.repo.set_work_status(wid, "PARTIAL", "verification_failed")
                self.repo.enqueue_retry(wid, "parse", "VERIFY_FAILED", 0, utcnow())
            results.append(
                {
                    "work_id": wid,
                    "valid": not errors,
                    "errors": errors,
                    "validation_scope": "retained artifact integrity; completeness remains in quality_report",
                }
            )
        database = self.repo.integrity_check()
        return {
            "database": database,
            "works": results,
            "valid": database["ok"] and all(row["valid"] for row in results),
        }
