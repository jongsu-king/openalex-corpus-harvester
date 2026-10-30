"""Resumable OPV query inventory with explicit lexical coverage and hard budgets."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from contextlib import AsyncExitStack
from pathlib import Path

from .api.search import SearchSpec
from .config import Settings
from .pipeline import Pipeline, corpus_lock
from .utils import atomic_json, sha256_file, utcnow


def load_api_key_file(path: Path) -> None:
    """Load only the requested credential, without shell execution or interpolation."""
    if not path.is_file():
        raise ValueError("The explicitly requested environment file does not exist")
    try:
        from dotenv import dotenv_values
    except ImportError as error:
        raise RuntimeError("Install python-dotenv to use --env-file") from error
    key = dotenv_values(path, encoding="utf-8-sig", interpolate=False).get("OPENALEX_API_KEY")
    if not key or not key.strip():
        raise ValueError("OPENALEX_API_KEY is missing from the requested environment file")
    key = key.strip()
    if key.lower() in {"your_key", "your_api_key", "replace_me", "example", "..."} or "\n" in key:
        raise ValueError("OPENALEX_API_KEY is a placeholder or an invalid multiline value")
    os.environ["OPENALEX_API_KEY"] = key


def _fingerprint(params: dict, download_pdf: bool, parse_markdown: bool) -> str:
    value = {
        "params": params,
        "download_pdf": download_pdf or parse_markdown,
        "parse_markdown": parse_markdown,
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _refresh_completion_report(pipeline, report, download_pdf, parse_markdown):
    """Separate exhausted metadata cursors from current downstream work quality."""
    report["corpus_stats"] = pipeline.repo.stats()
    report["database_integrity"] = pipeline.repo.integrity_check()
    report["checked_at"] = utcnow()
    report["metadata_status"] = report.get("metadata_status", report["status"])
    report["status"] = report["metadata_status"]
    processing = {
        "download_requested": download_pdf or parse_markdown,
        "parse_requested": parse_markdown,
        "work_status_counts": {},
        "download_job_status_counts": {},
        "download_error_counts": {},
        "parse_job_status_counts": {},
        "failed_or_pending_works": 0,
        "pdf_unavailable_works": 0,
    }
    if download_pdf or parse_markdown:
        rows = pipeline.repo.conn.execute(
            """SELECT w.status,d.status AS download_status,d.error_code AS download_error,
            p.status AS parse_status,COUNT(*) AS n
            FROM crawl_work_ids c JOIN works w ON w.id=c.work_id
            LEFT JOIN download_jobs d ON d.work_id=w.id
            LEFT JOIN parse_jobs p ON p.work_id=w.id
            WHERE c.run_id=? GROUP BY w.status,d.status,d.error_code,p.status""",
            (report["crawl_run_id"],),
        )
        successful_download = {"DOWNLOADED", "REUSED", "COMPLETE", "COMPLETED", "SUCCESS"}
        for row in rows:
            n = row["n"]
            for field, source in (
                ("work_status_counts", "status"),
                ("download_job_status_counts", "download_status"),
                ("parse_job_status_counts", "parse_status"),
            ):
                state = row[source] or "NOT_ATTEMPTED"
                processing[field][state] = processing[field].get(state, 0) + n
            if row["download_error"]:
                code = row["download_error"]
                processing["download_error_counts"][code] = (
                    processing["download_error_counts"].get(code, 0) + n
                )
            # Work status also covers malformed files and nonretryable failures;
            # only a genuine absence of an available PDF is a benign terminal state.
            if row["status"] == "PDF_UNAVAILABLE" and row["download_error"] == "PDF_NOT_AVAILABLE":
                processing["pdf_unavailable_works"] += n
                continue
            if row["download_status"] not in successful_download or (
                parse_markdown and row["status"] not in {"COMPLETE", "COMPLETE_WITH_WARNINGS"}
            ):
                processing["failed_or_pending_works"] += n
    report["requested_processing"] = processing
    if report["query_exhausted"] and processing["failed_or_pending_works"]:
        report["status"] = "QUERY_COMPLETE_PROCESSING_INCOMPLETE"
    if not report["database_integrity"]["ok"]:
        report["status"] = "FAILED_DATABASE_INTEGRITY"
    report["requires_attention"] = (
        report["status"].startswith(("PAUSED", "FAILED"))
        or report["status"]
        in {"QUERY_COMPLETE_PROCESSING_INCOMPLETE", "COMPLETED_WITH_COUNT_MISMATCH_REVIEW"}
        or bool(processing["failed_or_pending_works"])
    )


def seed_first_page(
    pipeline: Pipeline,
    spec: SearchSpec,
    page_path: Path,
    download_pdf=False,
    parse_markdown=False,
    fresh=False,
) -> str:
    """Reuse a verified, exact-query discovery response without another API call."""
    receipt_path = page_path.with_suffix(".receipt.json")
    receipt = json.loads(receipt_path.read_text())
    if receipt.get("params") != spec.params() or receipt.get("cursor") != "*":
        raise ValueError("Discovery seed does not match the requested query exactly")
    if receipt.get("sha256") != sha256_file(page_path):
        raise ValueError("Discovery seed checksum mismatch")
    page = json.loads(page_path.read_text())
    if not isinstance(page.get("results"), list) or not isinstance(page.get("meta"), dict):
        raise ValueError("Malformed discovery seed")
    run = pipeline.repo.begin_crawl(
        {
            "params": spec.params(),
            "download_pdf": download_pdf or parse_markdown,
            "parse_markdown": parse_markdown,
        },
        resume=not fresh,
    )
    # An existing advanced checkpoint wins; never reset it to the first page.
    if run["cursor"] == "*" and run["offset"] == 0 and run["processed"] == 0:
        destination = (
            pipeline.settings.data_dir
            / "crawls"
            / run["id"]
            / "pages"
            / (hashlib.sha256(b"*").hexdigest() + ".json")
        )
        if destination.exists():
            existing_receipt = json.loads(destination.with_suffix(".receipt.json").read_text())
            if existing_receipt["sha256"] != sha256_file(destination):
                raise ValueError("Existing retained crawl page checksum mismatch")
        else:
            atomic_json(destination, page)
            atomic_json(
                destination.with_suffix(".receipt.json"),
                {**receipt, "sha256": sha256_file(destination), "seeded_from": str(page_path.resolve())},
            )
    return run["id"]


async def run_inventory(
    settings: Settings,
    *,
    max_works=10000,
    all_matches=False,
    chunk_size=1000,
    fresh=False,
    download_pdf=False,
    parse_markdown=False,
    seed_page: Path | None = None,
    client=None,
    downloader=None,
) -> dict:
    """Follow one targeted query to its terminal cursor or an explicit limit.

    --all removes only the invocation record cap. It never removes API budgets,
    authentication requirements, retry limits, or the per-batch memory bound.
    """
    if max_works < 1 or not 1 <= chunk_size <= 10000:
        raise ValueError("max_works must be positive; chunk_size must be in 1..10000")
    spec = SearchSpec(**settings.search)
    if not spec.query or spec.scope != "title_abstract_keywords":
        raise ValueError("OPV inventory requires an explicit title/abstract/keyword query")
    settings.prepare()
    report_path = settings.data_dir / "collection_report.json"
    fingerprint = _fingerprint(spec.params(), download_pdf, parse_markdown)
    execution_id = utcnow().replace(":", "-") + "-" + uuid.uuid4().hex[:8]
    with corpus_lock(settings):
        pipeline = Pipeline(settings)
        resources = AsyncExitStack()
        try:
            if report_path.exists() and not fresh:
                previous = json.loads(report_path.read_text())
                if previous.get("query_fingerprint") == fingerprint and previous.get("query_exhausted"):
                    retained = pipeline.repo.conn.execute(
                        "SELECT c.status,r.query_json FROM crawl_checkpoints c JOIN crawl_runs r ON r.id=c.run_id WHERE c.run_id=?",
                        (previous.get("crawl_run_id"),),
                    ).fetchone()
                    expected = {
                        "params": spec.params(),
                        "download_pdf": download_pdf or parse_markdown,
                        "parse_markdown": parse_markdown,
                    }
                    if (
                        retained
                        and retained["status"] == "COMPLETED"
                        and json.loads(retained["query_json"]) == expected
                    ):
                        previous.update(reused_completed_inventory=True, processed_this_invocation=0)
                        _refresh_completion_report(pipeline, previous, download_pdf, parse_markdown)
                        atomic_json(report_path, previous)
                        return previous
            if client is None:
                from .api.client import OpenAlexClient

                client = await resources.enter_async_context(
                    OpenAlexClient(settings.api, settings.data_dir / "state")
                )
            if downloader is None and (download_pdf or parse_markdown):
                from .download.pdf_downloader import PDFDownloader

                downloader = await resources.enter_async_context(
                    PDFDownloader(
                        settings.download,
                        api_key=os.getenv("OPENALEX_API_KEY"),
                        state_dir=settings.data_dir / "state",
                    )
                )
            seeded = False
            if seed_page and seed_page.is_file() and not fresh:
                seed_first_page(pipeline, spec, seed_page, download_pdf, parse_markdown)
                seeded = True
            processed = 0
            report = {
                "execution_id": execution_id,
                "started_at": utcnow(),
                "query": spec.params(),
                "query_fingerprint": fingerprint,
                "query_scope": "OpenAlex title, abstract, indexed keywords",
                "scope_interpretation": "Organic photovoltaics candidate corpus; lexical retrieval is not a verified relevance label",
                "all_world_literature_complete": False,
                "query_exhausted": False,
                "api_key_present": bool(os.getenv("OPENALEX_API_KEY")),
                "metadata_only": not (download_pdf or parse_markdown),
                "daily_request_budget": settings.api.daily_request_budget,
                "daily_budget_usd": settings.api.daily_budget_usd,
                "content_cost_cap_usd": settings.download.max_content_cost_usd,
                "initial_page_seeded": seeded,
                "status": "RUNNING",
            }
            atomic_json(report_path, report)
            first = True
            while all_matches or processed < max_works:
                take = chunk_size if all_matches else min(chunk_size, max_works - processed)
                outcome = await pipeline.harvest(
                    spec,
                    max_works=take,
                    download_pdf=download_pdf,
                    parse_markdown=parse_markdown,
                    resume=not (fresh and first),
                    client=client,
                    downloader=downloader,
                )
                first = False
                processed_now = outcome.get("processed_this_run", 0)
                processed += processed_now
                run_id = outcome["run_id"]
                checkpoint = pipeline.repo.get_checkpoint(run_id)
                unique = pipeline.repo.conn.execute(
                    "SELECT COUNT(*) FROM crawl_work_ids WHERE run_id=?", (run_id,)
                ).fetchone()[0]
                total = checkpoint["total_count"]
                exhausted = outcome["status"] == "COMPLETED"
                status = "COMPLETE_QUERY_MATCHES" if exhausted else outcome["status"]
                if exhausted and total is not None and unique != total:
                    status = "COMPLETED_WITH_COUNT_MISMATCH_REVIEW"
                if (
                    outcome["status"] == "PAUSED"
                    and not report["api_key_present"]
                    and "OPENALEX_API_KEY" in outcome.get("error", "")
                ):
                    status = "PAUSED_API_KEY_REQUIRED"
                report.update(
                    status=status,
                    metadata_status=status,
                    crawl_run_id=run_id,
                    query_exhausted=exhausted,
                    processed_this_invocation=processed,
                    unique_collected_for_query=unique,
                    reported_matches=total,
                    estimated_uncollected_matches=max(0, total - unique) if total is not None else None,
                    metadata_coverage_percent=round(100 * unique / total, 4) if total else None,
                    retained_cursor=checkpoint["cursor"],
                    retained_page_offset=checkpoint["offset"],
                    error=outcome.get("error"),
                    retry_at=outcome.get("retry_at"),
                    checked_at=utcnow(),
                    count_is_live_not_snapshot=True,
                )
                atomic_json(report_path, report)
                if exhausted or outcome["status"] != "LIMIT_REACHED":
                    break
                if processed_now == 0:
                    report.update(
                        status="FAILED_NO_PROGRESS",
                        metadata_status="FAILED_NO_PROGRESS",
                        error="A batch made no progress; refusing an infinite loop",
                    )
                    break
            _refresh_completion_report(pipeline, report, download_pdf, parse_markdown)
            atomic_json(report_path, report)
            atomic_json(settings.data_dir / "collection_runs" / f"{execution_id}.json", report)
            return report
        finally:
            await resources.aclose()
            pipeline.close()
