import json
from pathlib import Path

import pymupdf
import pytest
from typer.testing import CliRunner

from openalex_corpus.cli import app
from openalex_corpus.config import Settings, load_settings
from openalex_corpus.export import export_corpus
from openalex_corpus.pipeline import Pipeline
from openalex_corpus.quality.checker import check_extraction, verify_manifest
from openalex_corpus.utils import atomic_json, redact, sha256_file


def sample_result():
    return {
        "engine": "docling+pymupdf+pdfplumber",
        "parser_versions": {},
        "status": "COMPLETE",
        "pages": [{"page_number": 1, "char_count": 50, "status": "extracted", "missing_regions": []}],
        "markdown": "# Source title\n\nA faithful source paragraph.",
        "errors": [],
        "warnings": [],
        "tables": [],
        "figures": [],
        "equations": [],
        "references": [],
        "text_blocks": [],
    }


def test_missing_page_is_never_success(tmp_path):
    report = check_extraction(sample_result(), 2, tmp_path)
    assert report["status"] == "PARTIAL"
    assert report["missing_pages"] == [2]


def test_broken_image_reference_fails(tmp_path):
    result = sample_result()
    result["markdown"] += "\n![](figures/absent.png)"
    assert not check_extraction(result, 1, tmp_path)["checks"]["image_references_resolve"]


def test_fallback_never_claims_complete(tmp_path):
    result = sample_result()
    result["engine"] = "pymupdf_fallback"
    assert check_extraction(result, 1, tmp_path)["status"] == "COMPLETE_WITH_WARNINGS"


def test_manifest_checks_size_and_hash(tmp_path):
    path = tmp_path / "original.bin"
    path.write_bytes(b"original")
    manifest = {"files": [{"path": path.name, "sha256": sha256_file(path), "size_bytes": 8}]}
    assert verify_manifest(manifest, tmp_path)["valid"]
    path.write_bytes(b"tampered")
    assert not verify_manifest(manifest, tmp_path)["valid"]


@pytest.fixture
def corpus(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        parsing={"enable_docling": False, "primary_engine": "pymupdf", "max_recovery_attempts": 0},
    )
    pipeline = Pipeline(settings)
    pipeline.ingest(
        {
            "id": "https://openalex.org/W1",
            "title": "Original",
            "doi": None,
            "unknown_future": {"nested": [None, "", 1]},
        }
    )
    yield pipeline
    pipeline.close()


@pytest.mark.parametrize("format", ["json", "jsonl", "csv", "parquet", "sqlite"])
def test_export_retains_nested_data(corpus, tmp_path, format):
    target = tmp_path / f"export.{format}"
    report = export_corpus(corpus.repo, target, format)
    assert Path(report["path"]).is_file()
    if format in {"json", "jsonl"}:
        record = json.loads(target.read_text())
        if isinstance(record, list):
            record = record[0]
        assert record["raw"]["unknown_future"]["nested"] == [None, "", 1]
    if format == "parquet":
        import pyarrow.parquet as pq

        assert json.loads(pq.read_table(target)["raw_json"][0].as_py())["unknown_future"]["nested"] == [
            None,
            "",
            1,
        ]


def make_pdf(path):
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((72, 72), "Source title and a faithful source paragraph.")
        doc.save(path)


async def test_failed_reparse_preserves_previous_success(corpus, monkeypatch):
    pdf = corpus.paper_dir("W1") / "original.pdf"
    make_pdf(pdf)

    async def good(*args):
        return sample_result()

    monkeypatch.setattr(corpus, "_run_parser", good)
    first = await corpus.parse("W1", pdf)
    assert first["status"] == "COMPLETE"
    old_text = (corpus.paper_dir("W1") / "paper.md").read_text()

    async def bad(*args):
        raise RuntimeError("synthetic native parser crash")

    monkeypatch.setattr(corpus, "_run_parser", bad)
    second = await corpus.parse("W1", pdf, force=True)
    assert second["status"] == "FAILED"
    assert corpus.repo.get_work("W1")["status"] == "COMPLETE"
    assert (corpus.paper_dir("W1") / "paper.md").read_text() == old_text


async def test_reconcile_post_rename_crash(corpus, monkeypatch):
    pdf = corpus.paper_dir("W1") / "original.pdf"
    make_pdf(pdf)

    async def good(*args):
        return sample_result()

    monkeypatch.setattr(corpus, "_run_parser", good)
    original_record = corpus.repo.record_extraction

    def crash(*args):
        raise RuntimeError("simulated crash between rename and DB commit")

    monkeypatch.setattr(corpus.repo, "record_extraction", crash)
    with pytest.raises(RuntimeError):
        await corpus.parse("W1", pdf)
    assert not (corpus.paper_dir("W1") / "current.json").exists()
    monkeypatch.setattr(corpus.repo, "record_extraction", original_record)
    recovered = corpus.reconcile()
    assert not recovered["failures"]
    assert (corpus.paper_dir("W1") / "paper.md").is_file()
    assert corpus.repo.get_work("W1")["status"] == "COMPLETE"
    assert not corpus.reconcile()["failures"]
    assert corpus.repo.conn.execute("SELECT COUNT(*) FROM extraction_runs").fetchone()[0] == 1


def test_metadata_pending_recovers_newer_revision(corpus):
    directory = corpus.paper_dir("W1")
    new_raw = {"id": "https://openalex.org/W1", "title": "Updated metadata"}
    pending_path = directory / "metadata_history" / "pending.json"
    atomic_json(pending_path, new_raw)
    atomic_json(directory / "metadata_pending.json", {"raw_path": str(pending_path)})
    assert not corpus.reconcile()["failures"]
    assert corpus.repo.get_work("W1")["title"] == "Updated metadata"


def test_config_paths_and_memory_budget(tmp_path, monkeypatch):
    config = tmp_path / "config" / "config.yaml"
    config.parent.mkdir()
    config.write_text("data_dir: data\n")
    assert load_settings(config).data_dir == tmp_path / "data"
    monkeypatch.setenv("OPENALEX_CORPUS_DATA_DIR", str(tmp_path / "elsewhere"))
    assert load_settings(config).data_dir == tmp_path / "elsewhere"
    with pytest.raises(ValueError):
        Settings(parsing={"workers": 4, "worker_memory_mb": 2048, "memory_limit_mb": 4096})


def test_secret_redaction(monkeypatch):
    monkeypatch.setenv("OPENALEX_API_KEY", "example-secret")
    assert "example-secret" not in json.dumps(
        redact({"message": "GET x?api_key=example-secret", "Authorization": "secret"})
    )


def test_cli_stats_and_empty_search(tmp_path):
    runner = CliRunner()
    base = ["--data-dir", str(tmp_path / "corpus")]
    result = runner.invoke(app, base + ["stats"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["status_total_consistent"]
    result = runner.invoke(app, base + ["harvest"])
    assert result.exit_code == 1
    assert "bounded search" in result.output


def test_reingest_rejects_tampered_raw_revision(corpus):
    row = corpus.repo.get_work("W1")
    raw_path = Path(row["raw_path"])
    original_hash = corpus.repo.conn.execute(
        "SELECT sha256 FROM artifacts WHERE path=?", (str(raw_path),)
    ).fetchone()[0]
    atomic_json(raw_path, {"id": "https://openalex.org/W1", "title": "tampered"})
    with pytest.raises(ValueError, match="RAW_METADATA_CORRUPT"):
        corpus.ingest(row["raw"])
    assert (
        corpus.repo.conn.execute("SELECT sha256 FROM artifacts WHERE path=?", (str(raw_path),)).fetchone()[0]
        == original_hash
    )


async def test_reuse_validates_output_and_rebuilds_missing_markdown(corpus, monkeypatch):
    pdf = corpus.paper_dir("W1") / "original.pdf"
    make_pdf(pdf)
    calls = []

    async def good(*args):
        calls.append(1)
        return sample_result()

    monkeypatch.setattr(corpus, "_run_parser", good)
    first = await corpus.parse("W1", pdf)
    (corpus.paper_dir("W1") / first["run"] / "paper.md").unlink()
    second = await corpus.parse("W1", pdf)
    assert len(calls) == 2
    assert second["run"] != first["run"]
    assert (corpus.paper_dir("W1") / "paper.md").is_file()


async def test_reconcile_rejects_mutated_source(corpus, monkeypatch):
    pdf = corpus.paper_dir("W1") / "original.pdf"
    make_pdf(pdf)

    async def good(*args):
        return sample_result()

    monkeypatch.setattr(corpus, "_run_parser", good)
    await corpus.parse("W1", pdf)
    pdf.write_bytes(b"not a pdf")
    assert corpus.reconcile()["failures"]
    assert corpus.repo.get_work("W1")["status"] == "PARTIAL"


@pytest.mark.parametrize("target", ["db", "raw", "paperdir"])
def test_export_refuses_authoritative_destinations(corpus, target):
    destination = {
        "db": corpus.settings.db_path,
        "raw": Path(corpus.repo.get_work("W1")["raw_path"]),
        "paperdir": corpus.paper_dir("W1"),
    }[target]
    with pytest.raises(ValueError, match="authoritative|Exports inside"):
        export_corpus(corpus.repo, destination, "json")


def test_verify_integrity_failure_nonzero_exit(corpus):
    row = corpus.repo.get_work("W1")
    Path(row["raw_path"]).unlink()
    result = CliRunner().invoke(app, ["--data-dir", str(corpus.settings.data_dir), "verify", "--all"])
    assert result.exit_code == 2, result.output
    assert json.loads(result.output)["valid"] is False


async def test_published_markdown_keeps_page_image_relative_link(corpus, monkeypatch):
    pdf = corpus.paper_dir("W1") / "original.pdf"
    make_pdf(pdf)

    async def page_image_result(pdf_path, staging, options):
        (staging / "page_images").mkdir()
        (staging / "page_images" / "page_1.png").write_bytes(b"fixture image reference")
        result = sample_result()
        result["markdown"] += "\n![](page_images/page_1.png)"
        return result

    monkeypatch.setattr(corpus, "_run_parser", page_image_result)
    await corpus.parse("W1", pdf)
    assert (corpus.paper_dir("W1") / "page_images" / "page_1.png").is_file()


async def test_real_parser_process_keeps_diagnostics_and_pages(corpus):
    pdf = corpus.paper_dir("W1") / "original.pdf"
    make_pdf(pdf)
    staging = corpus.paper_dir("W1") / "worker-test"
    staging.mkdir()
    result = await corpus._run_parser(pdf, staging, corpus.settings.parsing.model_dump())
    assert [p["page_number"] for p in result["pages"]] == [1]
    assert (staging / "parser_stdout.log").is_file()
    assert (staging / "parser_stderr.log").is_file()


def test_invalid_metadata_has_retained_raw_and_explicit_failure(corpus):
    with pytest.raises(ValueError):
        corpus.ingest({"title": "source record without an ID", "unknown": [None, ""]})
    statuses = list((corpus.settings.data_dir / "raw_responses").glob("*.status.json"))
    assert len(statuses) == 1
    status = json.loads(statuses[0].read_text())
    assert status["validation_status"] == "parse_failed"
    assert json.loads(Path(status["raw_path"]).read_text())["unknown"] == [None, ""]


async def test_tei_conflicting_title_is_retained_and_flagged(corpus, monkeypatch):
    pdf = corpus.paper_dir("W1") / "original.pdf"
    make_pdf(pdf)

    async def conflicting(*args):
        result = sample_result()
        result["tei_metadata"] = {"title": "A different embedded article title"}
        return result

    monkeypatch.setattr(corpus, "_run_parser", conflicting)
    output = await corpus.parse("W1", pdf, force=True)
    assert output["status"] == "COMPLETE_WITH_WARNINGS"
    assert corpus.repo.get_work("W1")["metadata"]["title"] == "Original"
    result = json.loads((corpus.paper_dir("W1") / output["run"] / "result.json").read_text())
    assert result["tei_metadata"]["title"] == "A different embedded article title"
    assert result["metadata_comparisons"][0]["action"] == "retain_both_no_substitution"
