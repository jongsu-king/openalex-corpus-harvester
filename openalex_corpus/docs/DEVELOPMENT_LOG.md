# Development and validation log

This record covers the initial implementation and optional-engine work on **October 29, 2026**, followed by documentation, release checks, and the first GitHub publication on **October 30**. Dates and times below use **Asia/Seoul (KST)**; timestamps inside raw JSON evidence remain in UTC.

The October 30 documentation work used existing source files and run evidence. It did not restart OPV collection or query OpenAlex. The first publication is recorded in section 10 and the public release in section 13.

For operation, see the [README](../README.md). Supporting documents include the
[architecture](ARCHITECTURE.md), [pipeline walkthrough](PIPELINE_WALKTHROUGH.md),
[initial validation](VALIDATION.md), [GROBID/OCR validation](ENHANCED_VALIDATION.md),
and [test matrix](TEST_MATRIX.md). Sections 10 and 11 describe earlier diagram
versions; section 12 records their replacement with text schematics.

## 1. Scope

The project implements an installable Python 3.11+ corpus pipeline:

```text
Search OpenAlex and preserve raw responses
  -> identify works and normalize metadata
  -> find, download, and validate accessible PDFs
  -> extract structure, text, tables, figures, equations, and references
  -> check page coverage, source regions, and file integrity
  -> store Markdown, JSON, and relational records
  -> recover, reparse, collect incrementally, and export
```

Summarization, LLM reconstruction, research evaluation, recommendations, embeddings, RAG, and a web UI were outside the implementation scope. Missing or uncertain content is represented by source records, candidates, original regions, and explicit reasons. Download success, parser completion, automatic quality checks, and content accuracy are tracked separately.

## 2. Timeline and evidence

| Date/time (KST) | Work completed | Evidence |
|---|---|---|
| October 29, initial implementation | Python 3.11 environment, CLI, API client, downloader, parsers, SQLite, validation, recovery, and tests | Source tree, `pyproject.toml`, `uv.lock`, initial validation record |
| October 29, about 11:58 | One live keyless singleton request; all 52 top-level Work keys preserved; no hosted-content downloads | `data/validation/live_api_evidence.json` |
| October 29, initial integration run | Two OA PDFs downloaded and all 14 pages processed; exports, backup, and integrity checks completed | `data/validation/validation_summary.json` |
| October 29, initial regression run | 114 passed, 2 skipped; singleton and Docling opt-in checks run separately | `data/validation/pytest_offline.txt`, initial validation record |
| October 29, about 16:06 | Live OCR on a mixed two-page fixture and a separate rasterized-paper input | `data/validation/ocr/latest_evidence.json` |
| October 29, about 16:09 | GROBID fulltext REST call returned 103,023 bytes of TEI and 16 structured references; title conflict detected | `data/validation/grobid/integration_evidence.json` |
| October 29, about 16:12 | Existing PARTIAL paper reprocessed, 9/9 pages; equation, table, and title issues remained | `data/validation/enhanced_reparse.json`, selected run's quality report |
| October 29, about 16:13 | GROBID restarted with revised configuration; live test passed, engine pool 1/1, loopback binding checked | `post_config_restart_validation` in GROBID integration evidence |
| October 29, about 16:09–16:22 | Eight discovery first pages yielded 608 unique works; final query's first 100 stored in the database; next cursor paused for an API key | `data/opv_discovery/summary.json`, `data/opv/collection_report.json` |
| October 29, follow-up regression run | 130 passed, 5 skipped, 5 warnings | `data/validation/enhanced_pytest_offline.txt` |
| October 30, documentation | Development record, portable installation instructions, document navigation, and diagrams added; publishable files separated from local run data | Repository documentation, `.gitignore`, SVG overview |
| October 30, pre-publication checks | 130 passed, 5 skipped; Ruff, sdist, and wheel builds passed; SVG rendered at 1600×1000 and inspected | `data/validation/docs/pytest_pre_push.txt`, `data/validation/docs/pipeline-overview.png` |
| October 30, detailed diagrams | Five Mermaid diagrams parsed, rendered, and visually inspected with Mermaid 11.17.2; internal file links checked across 13 Markdown documents | `data/validation/docs/mermaid_validation.json`, `architecture-1.png`–`architecture-5.png` |
| October 30, text pipeline revision | Replaced documentation graphics with ASCII execution and storage schematics; expanded source-level contracts and added an invocation walkthrough | Architecture, pipeline walkthrough, API, and database references; see section 12 |

Approximate minute values come from `tested_at`, `recorded_at`, `checked_at`, or extraction-run names, converted to KST. Runs without detailed timestamps are listed by date only. Search totals below are the API's historical responses from October 29.

## 3. Architecture and implementation choices

File names in this table are relative to `src/openalex_corpus/` unless they begin with `config/`, `scripts/`, or `docs/`.

| Area | Main files | Behavior and rationale |
|---|---|---|
| Configuration and CLI | `config.py`, `cli.py`, `config/*.yaml` | Pydantic validates settings; YAML and environment variables remain separate. Commands return JSON and explicit exit codes. |
| API | `api/client.py`, `api/search.py`, `api/pagination.py` | HTTPX sends supported search arguments with `per_page=100`, cursors, retries, server usage headers, and persistent budgets. |
| Metadata | `metadata.py` | Normalizes a copy of the raw record, preserving nulls, empty strings, and empty arrays. Ambiguous abstract positions produce a failure record and candidates. |
| PDF acquisition | `download/pdf_downloader.py`, `download/validator.py` | Tries multiple candidates and checks HTTP responses, signature, size, opening, encryption, page count, and SHA-256. Keeps source versions separate. |
| Primary structure | `parsers/docling_parser.py` | Docling supplies document structure, Markdown, tables, and figure regions. Other stages remain usable without this optional dependency. |
| Page evidence | `parsers/pymupdf_parser.py` | Collects text blocks, spans, coordinates, images, and renderings independently on every page. Page coverage is not inferred solely from structural output. |
| Tables, equations, references | `parsers/table_parser.py`, `parsers/equation_parser.py`, `parsers/reference_parser.py` | Compares pdfplumber output, retains merged/empty cells and raw strings, and preserves uncertain candidates with source regions. |
| Academic structure and OCR | `parsers/grobid_parser.py`, `parsers/ocr.py` | Keeps TEI and service responses as supplementary evidence. OCR selects required pages and writes derived files. |
| Persistence | `database/repository.py`, `database/migrations/001_initial.sql` | Uses SQLite WAL, foreign keys, FULL synchronization, and transactions; stores normalized relations alongside immutable source and extraction history. |
| Quality and recovery | `quality/checker.py`, `quality/recovery.py`, `pipeline.py` | Checks coverage, files, disagreements, and unresolved elements; prevents a worse reparse from replacing a valid result. |
| Export | `export.py` | Supports JSON, JSONL, CSV, Parquet, Markdown, and SQLite; preserves nested metadata and protects corpus source paths. |
| OPV collection | `opv.py`, `scripts/discover_opv.py`, `scripts/harvest_opv.py` | Separates query definition, first-page discovery, and resumable cursor collection; distinguishes metadata completion from PDF processing. |
| Operations | `scripts/prepare_models.py`, `scripts/grobid_service.py`, `scripts/validate_ocr.py` | Provides explicit model preparation, local-service management, and live OCR validation commands. |
| Documentation | `docs/ARCHITECTURE.md`, `docs/PIPELINE_WALKTHROUGH.md`, `docs/API.md`, `docs/DATABASE.md`, `docs/DEVELOPMENT_LOG.md` | Documents execution and storage contracts with editable text schematics and records measured results. |

### Tested environment

Installation and execution were tested on **macOS arm64 with Python 3.11.16**, using a virtual environment separate from system Python. The declared Python 3.11+ requirement is broader than the platform/version combination exercised here.

Initial package versions were HTTPX 0.28.1, Pydantic 2.14.1, Docling 2.144.0, PyMuPDF 1.28.2, pdfplumber 0.11.10, Pillow 12.3.0, lxml 6.1.3, pandas 3.1.0, and PyArrow 23.0.1. Follow-up work used OpenJDK 21.0.13, GROBID 0.9.1, OCRmyPDF 17.13.0, Tesseract 5.5.3, and Ghostscript 10.08.0.

Use [uv.lock](../uv.lock) and [requirements-tested.txt](../requirements-tested.txt) to reproduce the Python environment. System dependencies are documented separately in [OCR_SETUP.md](OCR_SETUP.md) and [GROBID_SETUP.md](GROBID_SETUP.md). The dependency ranges in `pyproject.toml` have not been tested in every combination.

### Source preservation and identity

OpenAlex Work ID is the primary corpus identifier. DOIs are normalized and conflicts can be queried, but no DOI uniqueness constraint merges distinct Work IDs. Author order and multiple institution affiliations are retained in relationship tables and raw JSON.

Metadata has content-hashed `metadata_history` records plus a latest convenience copy. PDFs retain the first valid source and SHA-256-addressed versions. Each parsing run writes its own directory; `current.json` selects the active run, and convenience links such as `paper.md` resolve to that run. [DATABASE.md](DATABASE.md) describes the storage rules.

Files and SQLite cannot share one transaction. Temporary downloads, extraction staging, pending metadata, manifests, and database history therefore make interrupted states recoverable. Crawl checkpoints retain the cursor, saved-response hash, and within-page offset. Replaying a batch preserves existing sources and does not create duplicate works.

## 4. Failure cases addressed during implementation

Regression tests used controlled responses and injected failures to exercise these cases.

| Failure risk | Implemented handling |
|---|---|
| Missing abstract positions or different words at the same position | Record `parse_failed`; retain the original index and position candidates without filling gaps. |
| Unknown or nested fields lost during normalization | Keep complete raw JSON and its history, with nested source/missing states. |
| HTML error page accepted as PDF | Check signature, PDF opening, and page count in addition to Content-Type. |
| Interrupted download resumed against another file version | Resume only with matching validators and Content-Range; restart if the server ignores Range. |
| PDF at an existing URL changes | Keep hash-addressed versions; provide explicit `--refresh-pdf`. |
| Parser/model failure or excessive memory use | Run parsers in child processes; monitor RSS including descendants and enforce time limits; retain failure evidence and retry state. |
| Partial page extraction reported as complete | Compare expected, processed, empty, and failed page sets; unprocessed pages prevent success. |
| Corrupt Markdown reused because an earlier run succeeded | Validate the selected manifest, artifact hashes, and source PDF checksum before reuse. |
| Tampered metadata assigned a new trusted checksum | Check the existing trusted hash and content address before accepting data. |
| Process exits after rename but before DB registration | Validate the completed run directory and register it idempotently using a stable run ID. |
| Failed reparse replaces a valid prior result | Compare quality grades and block promotion of worse results. |
| `verify` fails but returns exit code 0 | Distinguish integrity failures, failed work, and paused operation in exit codes and JSON. |
| Export overwrites source DB, PDF, or metadata | Protect corpus paths; finish Markdown exports atomically at a new destination. |
| Restart or chunk boundary resets budgets | Reserve persistent budget before requests; share the downloader across an OPV run; retain retry limits. |
| Terminal cursor treated as full processing completion | Track `metadata_status` separately; report `QUERY_COMPLETE_PROCESSING_INCOMPLETE` when requested processing remains. |

Table, equation, and paragraph-boundary reconstruction remains imperfect. Disagreements retain raw text, table candidates, page coordinates, and region images for review or later reprocessing.

## 5. Validation results

### Regression and build checks

The initial suite returned **114 passed, 2 skipped, 5 warnings**. The expanded suite returned **130 passed, 5 skipped, 5 warnings** on October 29; the pre-publication run on October 30 returned the same result in about 3.99 seconds. Logs are `data/validation/pytest_offline.txt`, `data/validation/enhanced_pytest_offline.txt`, and `data/validation/docs/pytest_pre_push.txt` respectively.

The five later skips are opt-in checks: one real API test, one Docling test, one GROBID test, and two OCR tests. These were run separately with their enable flags. HTTP 429, timeout, authentication, pricing-header, Range, corruption, and interrupted-commit cases mostly use HTTPX mocks or failure injection. Synthetic PDFs exercise actual PyMuPDF/pdfplumber code; injected parser output tests comparison logic. The [test matrix](TEST_MATRIX.md) identifies each type.

Ruff passed for `src tests scripts`; lock checks and `uv build` passed. The wheel contained the SQLite SQL migration. The five warnings came from PyMuPDF SWIG deprecations; there were no failed assertions.

### OpenAlex and OA PDFs

| Paper | Execution | Recorded status |
|---|---|---|
| `W4229010617`, DOI `10.48550/arxiv.2205.01833` | OpenAlex metadata, arXiv OA PDF, 5/5 pages, Docling + PyMuPDF + pdfplumber | `COMPLETE_WITH_WARNINGS`: unverified reference structure and vector-region preservation warnings |
| `W4402950921`, DOI `10.48550/arxiv.2408.09869` | OpenAlex metadata, arXiv OA PDF, 9/9 pages; later reparsed with GROBID | `PARTIAL`: unresolved equations, tables, reading order, and title conflict |

Both PDFs were downloaded and all 14 pages processed. JSON, JSONL, CSV, Parquet, Markdown, and SQLite exports completed, as did an SQLite online backup. All 41 Markdown image references existed; database integrity, foreign keys, and registered file hashes passed.

This is a two-paper validation, not a corpus-scale accuracy estimate. PDFs came from external OA URLs. No paid OpenAlex hosted PDF/TEI download was executed.

### GROBID

GROBID 0.9.1 was built natively on macOS ARM from official source and models and received a real fulltext REST request at `127.0.0.1:8070`. Administration port 8071 was also limited to loopback. The service script provides start/status/stop; no global autostart was configured.

A duplicate-JAR packaging failure was addressed with a local Gradle init setting, `DuplicatesStrategy.EXCLUDE`, without changing upstream source. Resource settings were a 1,536 MiB Java heap, pdfalto at 1,024 MiB/120 seconds, and one processing engine. A request after restart reached approximately 1,658.5 MiB RSS. This is a measurement for the service in that environment, not a memory limit for the combined pipeline.

For `W4402950921`, the service produced 103,023 bytes of TEI, 108 structural items, and 16 structured references. All references included raw citation strings and coordinates. The source PDF hash was unchanged, and header/citation/funder consolidation through external services was disabled.

The response also demonstrated an extraction error: it selected the example title `DocLayNet: A Large Human-Annotated Dataset for Document-Layout Analysis` instead of OpenAlex's `Docling Technical Report`. Both source values were retained under `METADATA_TITLE_DISAGREEMENT`; the existing title was not overwritten.

### OCR

OCRmyPDF 17.13.0, Tesseract 5.5.3, and Ghostscript 10.08.0 were installed. **Two opt-in tests passed**, covering selected-page recognition and rejection of an invalid page range. Recognition used `eng`; `osd` data was also installed. A separate validation script processed the two inputs below.

| Input | Characters before | Characters after | Result |
|---|---|---|---|
| Synthetic fixture with one digital page and one scanned page | `[47, 0]` | `[47, 100]` | Only page 2 underwent OCR; digital-page provenance retained |
| Separate rasterized copy of a real paper's first page | `[0]` | `[2241]` | `Docling` detected; single-page output preserved |

The rasterized input was a test derivative, not an originally scanned paper. Source-paper and input hashes remained unchanged, and outputs were stored separately. The checks covered PDF opening, page count, hashes, extracted characters, and visual preservation. This validation made zero network requests; other languages were not tested.

### Why the reparse remained PARTIAL

The follow-up processed all nine pages of `W4402950921`. It retained 16 original raw-reference candidates and 16 GROBID candidates with separate provenance: 32 candidate records, not 32 distinct references. OCR was `not_required` because digital text was already available.

The selected run still contained three equation candidates and findings including `EQUATION_UNVERIFIED`, `equation_parse_failed`, `TABLE_UNVERIFIED`, `docling_table_values_disagree`, `table_cell_values_disagree`, `table_detection_disagreement`, `reading_order_requires_review`, `primary_text_disagreement`, `text_parser_disagreement`, `inline_image_original_bytes_unavailable`, `vector_graphics_preserved_in_page_image`, and `METADATA_TITLE_DISAGREEMENT`.

`data/validation/enhanced_verify.json` returned `valid=true` for storage integrity and its defined checks. The unresolved extraction findings kept the paper at `PARTIAL`.

## 6. OPV search and collection snapshot

OPV was interpreted as organic photovoltaics / organic solar cells. The query combines English phrases in titles, abstracts, and indexed keywords with photovoltaic context for the acronym. No year, language, work-type, or OA restriction was applied. Candidates may therefore include non-article Work types and irrelevant matches. [OPV_SCOPE.md](OPV_SCOPE.md) records the query and known relevance limits.

| Measure | Saved value | Interpretation |
|---|---:|---|
| Discovery first pages | 8 | Up to 100 records for each search definition |
| First-page appearances | 800 | Includes overlap between searches |
| Unique raw Work IDs | 608 | Union of the discovery first pages |
| Repeated appearances | 192 | Duplicate Work IDs among the 800 appearances |
| Final `expanded_union` API count | 82,363 | Total matches reported for the final query on October 29 |
| Unique works stored in final-query corpus | 100 | First final-query page inserted into `data/opv` |
| Stored fraction of reported matches | About 0.1214% | 100 / 82,363; not a PDF acquisition or relevance rate |
| Reported status | `PAUSED_API_KEY_REQUIRED` | Run paused before the next cursor because an API key was required |

The discovery collection of 608 raw records and the final-query database of 100 works are separate scopes. The reported 82,363 matches had not been collected in full.

During query development, standalone wildcard syntax was accepted, but a wildcard phrase inside a Boolean query returned HTTP 400. The rejected response was preserved; the final Boolean query lists singular and plural forms explicitly. Discovery recorded 10 requests, including rejected requests, and a conservative budget reservation of $0.0100. Per-query totals were not added because the queries overlap.

The saved `collection_report.json` snapshot is from approximately **October 29, 16:22:56 KST**, reviewed during documentation on October 30. It records `api_key_present=false`, `query_exhausted=false`, `all_world_literature_complete=false`, and metadata-only collection. These describe that run; a later run report takes precedence for subsequent progress or credential availability.

The default metadata budget is $0.05 per UTC day and 1,000 requests. Hosted content is limited to 0 downloads/$0. `--all` removes the work-count limit while preserving budget, request, server, authentication, and retry limits. The historical cursor pause was not bypassed with date partitions or alternate collection routes.

## 7. Reproduction and resume commands

Run these from the `openalex_corpus` project root. They are operational instructions; the recorded results above identify which runs were actually executed.

```bash
# Install the locked environment; see separate JDK and OCR system setup guides.
uv sync --locked --python 3.11 --extra dev --extra docling --extra ocr
.venv/bin/openalex-corpus doctor

# Default suite; live integration checks are opt-in.
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests scripts

# Start and inspect the local service.
.venv/bin/python scripts/grobid_service.py start
.venv/bin/python scripts/grobid_service.py status

# Opt-in integration checks.
OPENALEX_RUN_NETWORK=1 .venv/bin/python -m pytest -q tests/test_integration.py
OPENALEX_RUN_DOCLING=1 DOCLING_ARTIFACTS_PATH="$PWD/data/models/docling" \
  OPENALEX_ALLOW_MODEL_DOWNLOADS=0 \
  .venv/bin/python -m pytest -q tests/test_parsers.py -k actual_docling
OPENALEX_RUN_GROBID=1 .venv/bin/python -m pytest -q tests/test_grobid_integration.py
OPENALEX_RUN_OCR=1 .venv/bin/python -m pytest -q tests/test_live_ocr.py

# Reparse existing PARTIAL works while preserving sources and prior runs.
.venv/bin/openalex-corpus --config config/enhanced.yaml \
  reparse --status PARTIAL --max-works 10
.venv/bin/openalex-corpus --config config/enhanced.yaml verify --all

# Resume OPV after preparing the key in the project's .env file.
.venv/bin/python scripts/harvest_opv.py --env-file .env --all
.venv/bin/openalex-corpus --config config/opv.yaml stats
.venv/bin/openalex-corpus --config config/opv.yaml export --format jsonl
.venv/bin/openalex-corpus --config config/opv.yaml export --format parquet

# Recover interrupted persistence and check integrity.
.venv/bin/openalex-corpus --config config/opv.yaml reconcile
.venv/bin/openalex-corpus --config config/opv.yaml verify --all
```

The general CLI reads `OPENALEX_API_KEY` from the environment. The OPV script reads a specified file only when `--env-file` is supplied, with interpolation disabled and without executing the file as shell code. A fresh clone contains no models, built services, or source corpus; prepare those dependencies separately before attempting the corresponding runs.

## 8. Repository contents and local evidence

The repository contains code, settings, dependency locks, tests, operational scripts, and documentation with text schematics. `.gitignore` excludes `data/`, `logs/`, `.venv/`, `.env`, caches, and build output. Raw API responses, PDFs, databases, OCR inputs/derivatives, downloaded models, built services, and credentials remain local.

The following paths identify local evidence and are not repository links.

| Local path | Purpose |
|---|---|
| `data/validation/pytest_offline.txt` | Initial suite: 114 passed / 2 skipped |
| `data/validation/enhanced_pytest_offline.txt` | Expanded suite: 130 passed / 5 skipped |
| `data/validation/live_api_evidence.json` | Live singleton request count, raw-key count, and historical usage headers |
| `data/validation/validation_summary.json` | Two-paper run, exports, backup, and integrity results |
| `data/validation/grobid/integration_evidence.json` | GROBID version, TEI, coordinates, references, title conflict, and post-restart validation |
| `data/validation/ocr/latest_evidence.json` | OCR inputs/outputs, character counts, hashes, and limits |
| `data/validation/enhanced_reparse.json` | Follow-up run ID and status for the nine-page PARTIAL paper |
| `data/validation/enhanced_verify.json` | Follow-up database/file integrity checks |
| `data/papers/W4229010617/current.json`, `data/papers/W4402950921/current.json` | Pointers to selected immutable extraction runs |
| `data/opv_discovery/summary.json` | First-page searches, overlap, unique IDs, and reserved budget |
| `data/opv_discovery/first_pages_unique_raw.jsonl` | 608 unique raw discovery Work objects |
| `data/opv/collection_report.json`, `data/opv/collection_runs/` | Latest and per-run progress for the final-query collection |
| `data/opv/database/corpus.sqlite`, `data/opv/exports/` | Final query's first 100 works and exports |

Publishing the repository does not back up the corpus. SQLite's online backup excludes PDFs and raw JSON; corpus backups also need `data/papers`, `data/crawls`, `data/state`, and the relevant evidence. Moving a corpus to another location may require migration of recorded absolute artifact/source paths in the database.

## 9. Remaining limitations

1. **OPV cursor collection is incomplete.** Resume the saved query, checkpoint, and budget after resolving the recorded authentication pause. Completion requires checking unique IDs, the query's reported count, the terminal cursor, and requested downstream processing.
2. **Search relevance is unreviewed.** English wording and missing indexes can omit relevant work; organic-inorganic/perovskite matches can introduce false positives. The 82,363 count belongs to the saved query response.
3. **Extraction is not a semantic correctness guarantee.** Superscripts/subscripts, merged cells, caption relationships, reading order, and vector figures can remain uncertain. `COMPLETE` means the automatic criteria passed.
4. **OCR coverage is narrow.** Tests used an English mixed fixture and a rasterized page from a digital paper. Real scan collections, low resolution, skew, Korean text, and complex equations need separate evaluation.
5. **GROBID can return incorrect metadata.** The observed title conflict requires retaining source strings alongside structured values. Service RSS must also be considered alongside Docling/OCR processes.
6. **Long-running scale and Linux execution are untested.** Resume, duplicate, budget, and error paths have tests, but sustained PDF processing, storage exhaustion, and publisher diversity need operational evaluation.
7. **Paid hosted content and PostgreSQL remain outside completed validation.** Hosted-content budget/authentication/candidate paths were mocked; no paid download ran. No PostgreSQL backend exists.
8. **Update coverage depends on source and account capabilities.** Without access to an updated-date filter, publication-date searches and ID comparison cannot capture every historical metadata change, merge, or deletion. Full-dataset use requires considering the official Snapshot route.

## 10. First GitHub publication — October 30, 2026

| Item | Recorded result |
|---|---|
| Repository | [jongsu-king/openalex-corpus-harvester](https://github.com/jongsu-king/openalex-corpus-harvester) |
| Visibility at publication | `PRIVATE` |
| Default branch | `main`, tracking `origin/main` locally |
| First published commit | [`ca870a24afb45d3765a00bef709f75209118dec8`](https://github.com/jongsu-king/openalex-corpus-harvester/commit/ca870a24afb45d3765a00bef709f75209118dec8) |
| Initial scope | 75 files: source, settings, locks, tests, documentation, and SVG |
| Remote checks | `git push -u origin main` succeeded; `git ls-remote` returned the same `refs/heads/main` commit as local HEAD; GitHub API confirmed the architecture document existed |
| Diagram checks | One editable SVG overview and five detailed Mermaid diagrams rendered and inspected |
| Pre-publication checks | pytest 130 passed / 5 skipped, Ruff, sdist/wheel builds, internal document links, and Git whitespace checks passed |
| Exclusions checked | `.env`, source PDFs, collected JSON, SQLite, local models/services/virtual environment, and logs excluded; candidate files contained no actual keys, personal absolute paths, or large binaries |

The first commit is a snapshot of the completed workspace. Earlier implementation steps were not reconstructed as historical Git commits; their evidence is preserved in the local run records and this log. The documentation entry recording the successful first push was written afterward.

Repository publication left the recorded collection and extraction state unchanged: 100 stored final-query OPV works, an unexhausted query, and the unresolved PARTIAL paper.

## 11. English documentation and diagrams — October 30, 2026

The README, architecture reference, parser runbooks, OPV notes, and validation records were rewritten in English. At that revision, the overview showed Python modules, function calls, the worker boundary, and persisted files. The SVG was generated by `scripts/render_architecture.py`, and the detailed diagrams were Mermaid source in `ARCHITECTURE.md`. Section 12 supersedes that presentation; the earlier assets remain in Git history.

The five revised Mermaid diagrams passed parsing and rendering with Mermaid 11.17.2. The SVG rendered at 1680 × 1120 with no text outside the canvas. All local links and fenced code blocks across 13 Markdown files were checked, and Ruff and Git whitespace checks passed. The application code and stored corpus were not changed by this documentation revision.

## 12. Detailed text pipelines — October 30, 2026

The documentation now uses fixed-width ASCII schematics in Markdown `text`
blocks. The SVG overview, its renderer script, and embedded Mermaid diagrams
were removed from the current tree. Paper extraction still retains source
figures, equation crops, and page images; this revision changes documentation
presentation only.

The source review expanded four references:

| Reference | Detail added |
| --- | --- |
| [Architecture](ARCHITECTURE.md) | Numbered stage contracts, actual module calls, parser ordering, resource limits, write boundaries, failure branches, and recovery behavior |
| [Pipeline walkthrough](PIPELINE_WALKTHROUGH.md) | A command traced from configuration to final status; an illustrative two-page cursor sequence; mid-batch replay and interruption cases |
| [API](API.md) | Reservation before each request, counter lifetimes, HTTP classification, candidate priority, cache validation, redirect authentication, Range transfer, and PDF publication |
| [Database](DATABASE.md) | Primary/foreign keys, nullable fields, current projections versus retained history, canonical versus file hashes, transaction boundaries, retry state, and inspection SQL |

The walkthrough separates processed response entries from distinct Work IDs.
Its invented 200-record example uses 100-result pages, batches of three, and
30-record invocation caps. The fourth invocation consumes the last ten records
of page one and the first twenty of page two. This trace explains offset reuse;
it is not a new OPV collection measurement.

The persistence notes distinguish a completed filesystem rename from database
registration and from publication through `current.json`. They also document
limits found during source review: SQL does not enforce historical immutability
with triggers; reconciliation cannot rebuild every artifact registration;
and a page retained before its receipt can lack a trusted checksum if its
checkpoint does not yet contain one. These are descriptions of current behavior,
not implementation fixes.

Validation passed across 14 Markdown files: 122 relative links/anchors and 36
ASCII text schematics, each no wider than 100 columns. No image embeds or Mermaid
blocks remain. Ten database inspection queries and the walkthrough's inspection
SQL ran against an in-memory database created from the actual migration. Ruff
and Git whitespace checks passed. Local evidence is saved in
`data/validation/docs/text_documentation_validation.json`.

The application and its tests were not changed; the 130-pass regression result
above remains the prior measured run.

## 13. Public release — November 5, 2026

| Item | Recorded result |
|---|---|
| Visibility | Changed from `PRIVATE` to `PUBLIC` at commit [`80f9218`](https://github.com/jongsu-king/openalex-corpus-harvester/commit/80f9218) |
| Pre-release review | Pattern searches of the tree and all commits found no credentials, personal absolute paths, or private hosts; GitHub's activity log showed no force pushes. A dedicated secret scanner was not run |
| Regression checks | 130 passed, 5 skipped, on Python 3.11.18 and macOS arm64 with only the `dev` extra installed |
| Other checks | Ruff, `uv lock --check`, sdist/wheel builds, `CITATION.cff` schema validation, and internal Markdown links passed |
| Access checks | The repository page, REST API, raw README, and `git clone` worked without authentication |
| Not re-run | Live OpenAlex, Docling, GROBID, and OCR integrations; their recorded results above remain the prior measured runs |

The release commit changed documentation, package metadata, and a configuration
comment; it did not change application code or tests. The Zenodo badge was removed
at that commit because no DOI had been issued.

The Zenodo integration was then enabled, and release
[`v0.1.1`](https://github.com/jongsu-king/openalex-corpus-harvester/releases/tag/v0.1.1)
was archived as [10.5281/zenodo.23970940](https://doi.org/10.5281/zenodo.23970940).
The concept DOI [10.5281/zenodo.23970939](https://doi.org/10.5281/zenodo.23970939)
covers all versions. Release `v0.1.0` predates the integration and has no Zenodo
record. That release's `CITATION.cff` contains no DOI because the DOI was issued
after the tag was published; the file on `main` contains it.
