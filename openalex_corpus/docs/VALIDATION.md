# Initial validation record

**Date:** October 29, 2026 (Asia/Seoul). **Environment:** macOS arm64, Python 3.11.16.

This run covered local installation, metadata collection, PDF download, parsing, storage, recovery, and export. GROBID and OCR were installed and tested later that day; those results are in the [follow-up validation record](ENHANCED_VALIDATION.md). The results below describe the initial run.

## Installation and tests

| Check | Result |
|---|---|
| Default pytest suite | **114 passed, 2 skipped** |
| Tests skipped by default | Live OpenAlex singleton and real Docling opt-in tests |
| Live OpenAlex singleton | Run separately: 1 passed; all 52 top-level keys in the free singleton response preserved |
| Real Docling execution | Mixed-content fixture and both papers processed; offline execution with a local model directory also passed |
| Ruff | Passed for `src`, `tests`, and `scripts` |
| Locked installation | `uv sync --locked --python 3.11 --extra dev --extra docling` succeeded |
| Distribution packages | Source distribution and wheel built; SQL migrations present in the wheel |

The default test log contains five `DeprecationWarning` messages from PyMuPDF SWIG objects. No assertions failed. [TEST_MATRIX.md](TEST_MATRIX.md) distinguishes mocked responses, failure injection, real local parsers, and external integration tests.

Tested package versions: HTTPX 0.28.1, Pydantic 2.14.1, Docling 2.144.0, PyMuPDF 1.28.2, pdfplumber 0.11.10, Pillow 12.3.0, lxml 6.1.3, pandas 3.1.0, and PyArrow 23.0.1. The complete tested environment is recorded in [requirements-tested.txt](../requirements-tested.txt); [uv.lock](../uv.lock) pins the installation. Other dependency combinations were not tested in this run.

## Two OA papers

Singleton lookups and DOI-filtered searches ran without an OpenAlex API key. PDFs came from the arXiv OA URLs returned by the API. **OpenAlex hosted-content downloads: 0; hosted-content budget: $0.**

| Work ID / DOI | Pages processed | Status at this stage | Findings |
|---|---:|---|---|
| `W4229010617` / `10.48550/arxiv.2205.01833` | 5/5 | `COMPLETE_WITH_WARNINGS` | Primary parsing and text/table cross-checks completed. Reference structure remained unverified; vector regions were preserved as page images. |
| `W4402950921` / `10.48550/arxiv.2408.09869` | 9/9 | `PARTIAL` | All pages processed. Equation structure, conflicting table candidates, reading order, and unavailable original bytes for some images required review. |

Both papers used `docling+pymupdf+pdfplumber`. All **14 pages** were processed. Source pages, coordinates, images, table and equation regions, and parser-specific candidates were retained for review.

The initial extraction contained 13 table candidates, 38 image-object/figure-region candidates, three equation candidates, and 17 reference candidates. These are extraction records, not counts of distinct objects in the papers: multiple parsers can describe the same table or figure, and an unnumbered references section can remain a single raw-text candidate. `stats` reports candidate groups separately from image objects and figure regions.

Commands executed:

```bash
.venv/bin/openalex-corpus --config config/validation.yaml fetch \
  --doi 10.48550/arxiv.2205.01833 --download-pdf --parse-markdown
.venv/bin/openalex-corpus --config config/validation.yaml fetch \
  --doi 10.48550/arxiv.2408.09869 --download-pdf --parse-markdown
.venv/bin/openalex-corpus --config config/validation.yaml harvest \
  --doi 10.48550/arxiv.2205.01833 --max-works 1
.venv/bin/openalex-corpus --config config/validation.yaml reparse --max-works 2
.venv/bin/openalex-corpus reconcile
.venv/bin/openalex-corpus verify --all
```

Model downloads were explicitly enabled during setup. The final reparse used the prepared model directory with `allow_model_downloads=false`. The first Docling run on the mixed-content fixture, including model preparation, took about 64 seconds and reached approximately 1.56 GiB RSS. This measurement applies to that fixture and setup run.

## Storage and export

- SQLite reported `integrity_check=ok` and no foreign-key violations.
- All registered file hashes, selected-run manifests, source PDF hashes, and page-coverage checks passed for both papers.
- Status totals accounted for both papers. The `COMPLETE` rate was 0/2; the PDF download success rate was 2/2.
- JSON, JSONL, CSV, Parquet, Markdown, and SQLite exports completed.
- All **41 image references** in the Markdown export resolved to existing files.
- An SQLite online backup was created and returned `integrity_check=ok`.
- Third-party stdout/stderr went to per-run logs, leaving the CLI's result JSON parseable.

`verify.valid=true` reports storage integrity and the checks performed. The second paper remained `PARTIAL` because its extraction issues were unresolved.

## Failure and recovery checks

Tests covered HTTP 429 responses, timeouts, authentication errors, HTML masquerading as PDF, corrupt and encrypted PDFs, Range resume, exhausted budgets, changed PDF content at the same URL, within-page checkpoints, and repeated execution. Interruptions were injected between metadata writes, file renames, and database commits.

Tampering tests checked that modified source JSON or extraction files could not acquire a newly trusted checksum, corrupt previous results were not reused, and a worse reparse could not replace a valid result. Export attempts targeting protected corpus paths were rejected.

## Coverage still missing at this stage

- **GROBID service:** HTTP integration and local TEI/XML security tests existed, but no end-to-end server run had completed. Covered later in [ENHANCED_VALIDATION.md](ENHANCED_VALIDATION.md).
- **OCRmyPDF/Tesseract recognition:** optional execution and scan-failure handling existed, but OCR executables were not yet installed. Covered later in the same follow-up record.
- **Paid OpenAlex hosted PDF/TEI downloads:** only mocked budget, authentication, and candidate-priority tests.
- **Linux hardware, sustained large-scale operation, and PDFs with thousands of pages:** no performance or load test.
- **PostgreSQL:** documented as a possible extension; no backend implemented.

## Local evidence

`data/validation/validation_summary.json` records paper statuses, page counts, check results, and export paths. The same directory contains `pytest_offline.txt`, `live_api_evidence.json`, `live_work.json`, `reparse_result.json`, `reconcile_result.json`, `verify_result.json`, `stats_result.json`, and `doctor_result.json`. Model hashes are in `data/models/docling/model_manifest.json`.

These evidence files and the corpus remain in the local workspace and are excluded from Git.
