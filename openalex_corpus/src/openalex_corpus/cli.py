"""Command line entrypoints. Commands emit machine-readable JSON."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Annotated

import typer

from .config import load_settings
from .database.repository import Repository
from .pipeline import Pipeline, corpus_lock
from .utils import configure_logging, redact, utcnow

app = typer.Typer(
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    help="Collect OpenAlex metadata, preserve PDFs and extract an auditable local corpus.",
)


def emit(value):
    typer.echo(json.dumps(redact(value), ensure_ascii=False, indent=2, default=str))


@app.callback()
def main(
    ctx: typer.Context,
    config: Annotated[Path | None, typer.Option("--config", "-c", exists=True, dir_okay=False)] = None,
    data_dir: Annotated[Path | None, typer.Option("--data-dir")] = None,
):
    """Configuration paths are relative to their YAML project directory."""
    default = Path("config/config.yaml")
    settings = load_settings(config or (default if default.exists() else None))
    if data_dir:
        settings.data_dir = data_dir.resolve()
    settings.prepare()
    configure_logging(settings.log_dir)
    ctx.obj = settings


def run_job(ctx, task):
    pipeline = None
    try:
        with corpus_lock(ctx.obj):
            pipeline = Pipeline(ctx.obj)
            result = asyncio.run(task(pipeline))
            emit(result)
            if isinstance(result, dict):
                children = result.get("results", result.get("outcomes", []))
                if (
                    result.get("status") in {"PAUSED", "FAILED", "PENDING_RETRY"}
                    or result.get("valid") is False
                    or result.get("failures")
                    or result.get("reconciliation", {}).get("failures")
                    or any(child.get("status") in {"FAILED", "PENDING_RETRY"} for child in children)
                ):
                    raise typer.Exit(2)
    except typer.Exit:
        raise
    except KeyboardInterrupt:
        emit({"status": "PAUSED", "reason": "interrupted; resume with the same command"})
        raise typer.Exit(130)
    except Exception as error:
        if pipeline:
            requested = ctx.params.get("openalex_id") or ctx.params.get("doi")
            pipeline.repo.record_error(
                requested,
                ctx.info_name or "cli",
                getattr(error, "code", type(error).__name__),
                str(error),
                getattr(error, "retryable", False),
            )
        emit({"status": "FAILED", "code": getattr(error, "code", type(error).__name__), "error": str(error)})
        raise typer.Exit(1)
    finally:
        if pipeline:
            pipeline.close()


@app.command()
def harvest(
    ctx: typer.Context,
    query: str | None = None,
    title: str | None = None,
    abstract: str | None = None,
    search_scope: Annotated[
        str | None, typer.Option(help="all, title, title_and_abstract, title_abstract_keywords")
    ] = None,
    exact_search: Annotated[bool | None, typer.Option("--exact-search/--stemmed-search")] = None,
    from_year: int | None = None,
    to_year: int | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    source: str | None = None,
    author: str | None = None,
    institution: str | None = None,
    topic: str | None = None,
    doi: str | None = None,
    openalex_id: str | None = None,
    oa: Annotated[bool | None, typer.Option("--oa/--no-oa")] = None,
    work_type: str | None = None,
    min_citations: int | None = None,
    has_pdf: Annotated[bool | None, typer.Option("--has-pdf/--no-has-pdf")] = None,
    has_content: Annotated[bool | None, typer.Option("--has-content/--no-has-content")] = None,
    updated_since: str | None = None,
    filters: Annotated[
        list[str] | None, typer.Option("--filter", help="Documented field:value filter; repeatable")
    ] = None,
    max_works: Annotated[int | None, typer.Option(min=1, max=10000)] = None,
    download_pdf: bool = False,
    parse_markdown: bool = False,
    fresh: bool = False,
    refresh_pdf: bool = False,
):
    """Harvest at most max-works records per invocation; resume retained pages by default."""
    from .api.search import SearchSpec

    values = dict(ctx.obj.search)
    if search_scope is not None:
        values["scope"] = search_scope
    if exact_search is not None:
        values["exact"] = exact_search
    provided = locals().copy()
    for name in (
        "query",
        "title",
        "abstract",
        "from_year",
        "to_year",
        "from_date",
        "to_date",
        "source",
        "author",
        "institution",
        "topic",
        "doi",
        "openalex_id",
        "oa",
        "work_type",
        "min_citations",
        "has_pdf",
        "has_content",
        "updated_since",
    ):
        if provided[name] is not None:
            values[name] = provided[name]
    if filters:
        raw_filters = dict(values.get("filters", {}))
        for expression in filters:
            if ":" not in expression:
                raise typer.BadParameter("--filter must be field:value")
            key, value = expression.split(":", 1)
            raw_filters[key] = value
        values["filters"] = raw_filters
    spec = SearchSpec(**values)
    ctx.obj.download.refresh_existing = refresh_pdf

    async def task(pipeline):
        return await pipeline.harvest(
            spec, max_works or ctx.obj.max_works, download_pdf, parse_markdown, resume=not fresh
        )

    run_job(ctx, task)


@app.command()
def fetch(
    ctx: typer.Context,
    doi: str | None = None,
    openalex_id: str | None = None,
    download_pdf: bool = False,
    parse_markdown: bool = False,
    refresh_pdf: bool = False,
):
    """Fetch a single DOI or Work ID, retaining the complete OpenAlex response."""
    if bool(doi) == bool(openalex_id):
        raise typer.BadParameter("Provide exactly one --doi or --openalex-id")
    from .api.client import OpenAlexClient
    from .download.pdf_downloader import PDFDownloader

    ctx.obj.download.refresh_existing = refresh_pdf

    async def task(pipeline):
        async with OpenAlexClient(ctx.obj.api, ctx.obj.data_dir / "state") as client:
            raw = await client.get_work(doi or openalex_id)
        if download_pdf or parse_markdown:
            async with PDFDownloader(
                ctx.obj.download, api_key=os.getenv("OPENALEX_API_KEY"), state_dir=ctx.obj.data_dir / "state"
            ) as downloader:
                return await pipeline.process_work(raw, downloader, parse_markdown)
        return await pipeline.process_work(raw)

    run_job(ctx, task)


def _known_pdf(pipeline, wid):
    directory = pipeline.paper_dir(wid)
    report = directory / "download_report.json"
    if report.exists():
        value = json.loads(report.read_text())
        if value.get("path") and Path(value["path"]).is_file():
            return Path(value["path"]), value
    return directory / "original.pdf", None


@app.command()
def reparse(
    ctx: typer.Context,
    status: str | None = None,
    openalex_id: str | None = None,
    max_works: Annotated[int, typer.Option(min=1, max=10000)] = 100,
):
    """Reparse retained PDFs into new immutable runs; keep earlier better results."""

    async def task(pipeline):
        rows = (
            [pipeline.repo.get_work(openalex_id)]
            if openalex_id
            else list(pipeline.repo.iter_works(status))[:max_works]
        )
        results = []
        for row in rows:
            if row is None:
                raise ValueError("Requested Work ID is not in this corpus")
            pdf, report = _known_pdf(pipeline, row["id"])
            if not pdf.exists():
                results.append({"work_id": row["id"], "status": "PDF_UNAVAILABLE"})
                continue
            try:
                results.append(
                    {
                        "work_id": row["id"],
                        **await pipeline.parse(row["id"], pdf, force=True, download=report),
                    }
                )
            except Exception as error:
                pipeline._error(row["id"], "parse", error)
                results.append({"work_id": row["id"], "status": "FAILED", "error": str(error)})
        return {"results": results}

    run_job(ctx, task)


@app.command()
def retry(
    ctx: typer.Context,
    failed_only: bool = False,
    now: bool = False,
    max_works: Annotated[int, typer.Option(min=1, max=10000)] = 100,
):
    """Retry bounded pending/failed jobs. --now also includes backoff-delayed jobs."""
    from .download.pdf_downloader import PDFDownloader

    async def task(pipeline):
        reconciliation = pipeline.reconcile()
        if now:
            rows = [
                dict(row)
                for row in pipeline.repo.conn.execute(
                    "SELECT * FROM retry_queue WHERE status IN ('PENDING','FAILED') AND attempts<? ORDER BY id LIMIT ?",
                    (ctx.obj.retry_max_attempts, max_works),
                )
            ]
        else:
            rows = list(
                pipeline.repo.pending_retries(
                    failed_only=failed_only, max_attempts=ctx.obj.retry_max_attempts
                )
            )[:max_works]
        outcomes = []
        async with PDFDownloader(
            ctx.obj.download, api_key=os.getenv("OPENALEX_API_KEY"), state_dir=ctx.obj.data_dir / "state"
        ) as downloader:
            for item in rows:
                attempt = item["attempts"] + 1
                pipeline.repo.update_retry(item["id"], "RUNNING", attempts=attempt)
                work = pipeline.repo.get_work(item["work_id"])
                try:
                    pdf, report = _known_pdf(pipeline, work["id"])
                    if item["stage"] in {"parse", "reconcile"} and pdf.is_file():
                        outcome = await pipeline.parse(work["id"], pdf, force=True, download=report)
                    else:
                        outcome = await pipeline.process_work(
                            work["raw"], downloader, parse_markdown=True, force=True
                        )
                    success = outcome.get("status") in {
                        "COMPLETE",
                        "COMPLETE_WITH_WARNINGS",
                        "PDF_UNAVAILABLE",
                    }
                    state = (
                        "COMPLETED"
                        if success
                        else "FAILED"
                        if attempt >= ctx.obj.retry_max_attempts
                        else "PENDING"
                    )
                    from .quality.recovery import retry_at

                    queued = pipeline.repo.conn.execute(
                        "SELECT next_retry_at FROM retry_queue WHERE id=?", (item["id"],)
                    ).fetchone()
                    delays = [retry_at(attempt)] + ([queued[0]] if queued and queued[0] else [])
                    pipeline.repo.update_retry(
                        item["id"],
                        state,
                        attempts=attempt,
                        next_retry_at=max(delays) if not success else None,
                    )
                    outcomes.append({"work_id": work["id"], "attempt": attempt, **outcome})
                except Exception as error:
                    pipeline._error(work["id"], item["stage"], error, attempt)
                    pipeline.repo.update_retry(
                        item["id"], "FAILED", attempts=attempt, last_error=redact(str(error))
                    )
                    outcomes.append({"work_id": work["id"], "status": "FAILED", "error": str(error)})
        return {"reconciliation": reconciliation, "results": outcomes}

    run_job(ctx, task)


@app.command()
def verify(ctx: typer.Context, all_: Annotated[bool, typer.Option("--all")] = False):
    """Verify DB foreign keys, retained hashes, PDFs and extraction page coverage."""

    async def task(pipeline):
        return pipeline.verify()

    run_job(ctx, task)


@app.command()
def reconcile(ctx: typer.Context):
    """Recover interrupted jobs and repair convenience links from committed pointers."""

    async def task(pipeline):
        return pipeline.reconcile()

    run_job(ctx, task)


@app.command()
def stats(ctx: typer.Context):
    """Print counts and explicitly denominated rates."""
    with Repository(ctx.obj.db_path) as repo:
        emit(repo.stats())


@app.command("export")
def export_command(
    ctx: typer.Context,
    format: Annotated[str, typer.Option(help="json, jsonl, csv, parquet, markdown, sqlite")] = "jsonl",
    output: Path | None = None,
):
    from .export import export_corpus

    if format not in {"json", "jsonl", "csv", "parquet", "markdown", "sqlite"}:
        raise typer.BadParameter("Unsupported format")
    destination = output or ctx.obj.data_dir / "exports" / (
        "markdown" if format == "markdown" else f"corpus.{format}"
    )
    with Repository(ctx.obj.db_path) as repo:
        emit(export_corpus(repo, destination, format))


@app.command()
def backup(ctx: typer.Context, output: Path | None = None):
    """Create a consistent SQLite backup; retain/copy data/papers separately."""
    destination = output or ctx.obj.data_dir / "exports" / (
        "backup-" + utcnow().replace(":", "-") + ".sqlite"
    )
    with Repository(ctx.obj.db_path) as repo:
        repo.backup(destination)
    emit(
        {
            "backup": str(destination),
            "note": "Raw files must be backed up separately; sqlite includes metadata but not PDF bytes.",
        }
    )


@app.command()
def doctor(ctx: typer.Context):
    """Inspect dependencies without contacting external APIs or downloading models."""
    import shutil
    from importlib.metadata import PackageNotFoundError, version

    versions = {}
    for package in (
        "openalex-corpus",
        "httpx",
        "pydantic",
        "pymupdf",
        "pdfplumber",
        "docling",
        "ocrmypdf",
        "lxml",
        "pandas",
        "pyarrow",
    ):
        try:
            versions[package] = version(package)
        except PackageNotFoundError:
            versions[package] = None
    from .parsers.ocr import ocr_binary

    emit(
        {
            "versions": versions,
            "api_key_present": bool(os.getenv("OPENALEX_API_KEY")),
            "tesseract": shutil.which("tesseract"),
            "ocrmypdf": ocr_binary(),
            "grobid_url_configured": bool(ctx.obj.parsing.grobid_url),
            "data_dir": str(ctx.obj.data_dir),
            "protected_content_budget_usd": ctx.obj.download.max_content_cost_usd,
        }
    )


if __name__ == "__main__":
    app()
