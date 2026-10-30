# OpenAlex Corpus Harvester

Collect OpenAlex metadata, download accessible PDFs, and extract scientific papers into Markdown and structured files. The corpus stores original responses and PDFs, page coordinates, table and figure candidates, reference text, validation results, and a SQLite index.

Docling provides document structure. PyMuPDF and pdfplumber retain page-level evidence and cross-check text and tables. GROBID adds academic structure and references; OCRmyPDF handles selected scanned pages. Missing or conflicting content remains explicit in the output.

![Module and storage layout](docs/diagrams/pipeline-overview.svg)

See [Architecture](docs/ARCHITECTURE.md) for the execution and recovery paths, and the [development log](docs/DEVELOPMENT_LOG.md) for implementation decisions and measured results.

## Installation

From the repository root:

```bash
cd openalex_corpus
uv sync --locked --python 3.11 --extra dev --extra docling --extra ocr
source .venv/bin/activate
python scripts/prepare_models.py --output-dir data/models/docling
openalex-corpus doctor
```

The tested environment is Python 3.11.16 on macOS arm64. Exact Python dependencies are recorded in `uv.lock` and `requirements-tested.txt`. The model preparation command downloads Docling's layout and table models; these assets are not included in the repository.

On macOS, install the system dependencies with:

```bash
brew install openjdk@21 tesseract ghostscript
```

Other systems need Java 21, Tesseract, and Ghostscript available locally. The Python entry points support macOS and Linux, but the recorded installation and live parser runs were performed on macOS. See [GROBID setup](docs/GROBID_SETUP.md) and [OCR setup](docs/OCR_SETUP.md) for paths, settings, and tested versions.

The checked-in default configuration enables local GROBID and OCR when needed. Install and start GROBID before using that parsing configuration:

```bash
python scripts/grobid_service.py install
python scripts/grobid_service.py start
python scripts/grobid_service.py status

# Stop the local service when finished.
python scripts/grobid_service.py stop
```

The helper does not register a login service. After a reboot, start it explicitly. If Docling is unavailable, metadata collection and PyMuPDF/pdfplumber extraction still work, with the missing primary parser recorded as a warning.

## Credentials and first collection

The general CLI reads `OPENALEX_API_KEY` from the environment. The OPV script also accepts `--env-file .env`; it loads only the requested credential and does not execute the file as shell code.

```bash
export OPENALEX_API_KEY='YOUR_KEY'
openalex-corpus harvest \
  --query 'organic solar cells' \
  --from-year 2018 --to-year 2026 \
  --max-works 100 --download-pdf --parse-markdown
```

Anonymous access can retrieve a singleton record or the first search page. Cursor continuation requires an API key. Missing credentials or an exhausted budget pause the crawl, preserve its checkpoint, and return exit code 2. Run the same command after resolving the cause to continue. Keep credentials out of command arguments, YAML, and Git.

For the prepared OPV query:

```bash
cp -n .env.example .env  # Create if absent, then enter your key in this file.
python scripts/harvest_opv.py --env-file .env --all
```

This command collects metadata by default. `--all` removes the invocation's record cap; request and cost limits still apply. See [OPV scope and resume behavior](docs/OPV_SCOPE.md) before enabling PDF downloads.

## CLI reference

Global options such as `--config` and `--data-dir` go before the subcommand. Results are JSON; library progress messages may appear on stderr.

```bash
openalex-corpus --config config/config.yaml stats
openalex-corpus fetch --doi '10.48550/arxiv.2205.01833'
openalex-corpus fetch --openalex-id W4229010617 --download-pdf --parse-markdown

# Check for a changed PDF at an existing URL; retain earlier versions.
openalex-corpus fetch --openalex-id W4229010617 --download-pdf --refresh-pdf

openalex-corpus reparse --status PARTIAL --max-works 100
openalex-corpus reparse --openalex-id W4229010617
openalex-corpus reconcile
openalex-corpus retry --failed-only
openalex-corpus retry --failed-only --now --max-works 10
openalex-corpus verify --all
openalex-corpus stats

openalex-corpus export --format parquet
openalex-corpus export --format jsonl
openalex-corpus export --format json
openalex-corpus export --format csv
openalex-corpus export --format markdown --output data/exports/markdown-run-1
openalex-corpus export --format sqlite --output data/exports/corpus-copy.sqlite
openalex-corpus backup --output data/exports/backup.sqlite
```

`--parse-markdown` implies PDF download. `--max-works` limits response records processed in this invocation, up to 10,000. The same query resumes an unfinished crawl; `--fresh` starts a new one. A stop within an API page retains the response and its offset. Work IDs are upserted, while processed occurrences and distinct IDs are counted separately. Cached PDFs are checked against their recorded hash before reuse.

`retry` respects attempt counts and retry times. `--now` skips the backoff wait but keeps budget and attempt limits. Reprocessing after a parser or configuration change creates a new extraction run. A worse result does not replace a better retained result.

| Exit code | Meaning |
|---:|---|
| 0 | Command finished; individual works may still be PARTIAL or PDF_UNAVAILABLE |
| 1 | Configuration, argument, or execution error |
| 2 | Paused collection, failed work, or integrity failure |
| 130 | Interrupted by the user |

Read the individual JSON statuses as well as the process exit code. The OPV inventory script separately reports metadata completion and unfinished PDF or parsing jobs.

## Search options

| Option | Meaning |
|---|---|
| `--query` | Text query in the selected search scope |
| `--search-scope` | `all`, `title`, `title_and_abstract`, or `title_abstract_keywords` |
| `--exact-search` / `--stemmed-search` | Select exact or stemmed query behavior |
| `--title`, `--abstract` | Dedicated title or abstract search |
| `--from-year`, `--to-year` | Publication year range |
| `--from-date`, `--to-date` | Publication date range, `YYYY-MM-DD` |
| `--source`, `--author`, `--institution`, `--topic` | Official S/A/I/T identifiers |
| `--doi`, `--openalex-id` | Work identifier |
| `--oa/--no-oa`, `--work-type`, `--min-citations` | Access status, type, and minimum citation count |
| `--has-pdf/--no-has-pdf` | OpenAlex archive PDF availability: `has_content.pdf` |
| `--has-content/--no-has-content` | Indexed full text: `has_fulltext` |
| `--updated-since` | Permission-dependent `from_updated_date` filter |
| `--filter field:value` | An allowed OpenAlex filter; repeat for multiple filters |

An OpenAlex archive flag does not describe every external OA location. A record with `has_pdf=false` may still have a publisher or repository PDF. Entity selectors require IDs rather than names. Use a filter such as `primary_location.source.issn:1234-5678` for an ISSN or `ids.pmid:...` for a PMID. Unknown filter names are rejected locally.

The same fields are available under `search` in YAML:

```yaml
search:
  query: organic solar cells
  scope: title_abstract_keywords
  exact: true
  from_year: 2018
  to_year: 2026
  oa: true
  filters:
    primary_location.source.type: journal
```

Publication-date searches can discover newly encountered records when update filtering is unavailable, but cannot detect every correction, merge, or deletion of older records. Duplicate DOIs are retained as review candidates. For whole-database acquisition, use the [OpenAlex Snapshot](https://help.openalex.org/access/snapshot/).

## Budgets and resources

The default configuration allows 1,000 metadata requests and USD 0.05 per UTC day. Downloads use three concurrent tasks, a limit of 100 work downloads per downloader instance, and a 100 MiB file limit. OpenAlex archived content is disabled by default with a zero-request, zero-dollar cap. Other OA locations can still be attempted.

A request reserves its estimated cost on disk before transmission, including retries. Rate-limit headers and long `Retry-After` values can pause later requests. Metadata budgets reset by UTC date; archived-content caps accumulate within the corpus. Local accounting cannot include another application's use of the same account. See [API and budget handling](docs/API.md).

The checked-in YAML uses one parser process, a 3 GiB worker budget, a 4 GiB total reservation limit, and a 600-second timeout. The bare configuration model defaults to 2 GiB per worker. Child-process RSS is sampled and excessive workers are terminated. The configuration requires `workers * worker_memory_mb <= memory_limit_mb`; this is not an operating-system memory quota. A separately running GROBID service has its own memory use.

## Extraction status

Each parser retains its own evidence. Table candidates with conflicting values are preserved rather than merged. Embedded image objects and detected figure regions are counted separately. Equation regions keep their source text, coordinates, and crops; a deterministic `latex_candidate` is never treated as verified LaTeX.

| Status | Meaning |
|---|---|
| `COMPLETE` | Required files, page coverage, image references, and configured checks passed without warnings |
| `COMPLETE_WITH_WARNINGS` | Usable output with remaining parser or validation warnings |
| `PARTIAL` | Unresolved extraction issues, or metadata/PDF collection without full parsing |
| `FAILED` | Extraction failed |
| `PDF_UNAVAILABLE` | No usable PDF obtained under the current candidate/access policy |
| `PENDING_RETRY` | A retryable failure remains |

These statuses describe automated processing, not the scientific correctness of the paper or exact recovery of every layout element. `field_status` records absent, null, empty, or ambiguous metadata without substituting inferred values.

Set `parsing.grobid_url` for a service or `parsing.tei_path` for a TEI file belonging to the same paper. With `enable_ocr: true`, only pages that need recognition are selected; the original PDF is retained. The [parser reference](docs/PARSERS.md) describes the extraction order and limitations. [Integration results](docs/ENHANCED_VALIDATION.md) include the real-paper reparse that remained PARTIAL after GROBID was added.

## Storage, recovery, and exports

```text
data/
  database/corpus.sqlite
  crawls/<run>/pages/             # full responses and receipts
  papers/W.../
    metadata_history/<hash>.json  # immutable payload revisions
    metadata_raw.json            # current convenience copy
    metadata.json
    original.pdf                 # first validated PDF
    pdf_versions/<sha256>.pdf
    download_manifest.json
    current.json                 # selected extraction pointer
    extractions/<run>/
      paper.md
      document.json
      pages.json
      references.json
      quality_report.json
      manifest.json
      tables/ figures/ equations/ page_images/
    paper.md                     # symlink to the selected run
  state/                         # budget journal and writer lock
  exports/
```

SQLite uses WAL, foreign keys, FULL synchronization, transactions, and migrations. A corpus has one writer; downloads run asynchronously and parsing runs in child processes. Unknown source fields remain in raw JSON. [DATABASE.md](docs/DATABASE.md) documents relations and statistics. PostgreSQL is not implemented.

Downloads are validated as `.part` files before atomic promotion. Extraction output moves from `.staging-*` into a final run directory before DB registration. `reconcile` repairs interruptions between file publication and DB commit and rebuilds convenience links. Changed hashes are errors; they are not silently accepted as a new baseline. Partial downloads and interrupted runs remain available for recovery and inspection.

A SQLite backup does not contain PDF or JSON artifacts. Back up `data/papers`, `data/crawls`, and `data/state` as well, then run `reconcile` and `verify --all` after restoration. Moving the corpus to a new absolute path may require updating artifact and source paths in the database.

CSV and Parquet exports encode nested fields as JSON strings. Markdown export includes linked images and supporting files and requires a new destination. Exporting over an authoritative corpus path is rejected.

## Tests

```bash
pytest -q
ruff check src tests scripts

# Live integrations are opt-in.
OPENALEX_RUN_NETWORK=1 pytest -q tests/test_integration.py
OPENALEX_RUN_DOCLING=1 DOCLING_ARTIFACTS_PATH="$PWD/data/models/docling" \
  OPENALEX_ALLOW_MODEL_DOWNLOADS=0 pytest -q tests/test_parsers.py -k actual_docling
OPENALEX_RUN_GROBID=1 pytest -q tests/test_grobid_integration.py
OPENALEX_RUN_OCR=1 pytest -q tests/test_live_ocr.py
```

The default suite uses generated PDFs and mocked HTTP responses. Live tests require their documented services, models, credentials, or source inputs. The [test matrix](docs/TEST_MATRIX.md) maps the cases; [validation records](docs/ENHANCED_VALIDATION.md) distinguish offline checks from actual API and parser runs.

The overview SVG is generated by `python scripts/render_architecture.py`. The detailed diagrams in [ARCHITECTURE.md](docs/ARCHITECTURE.md) are editable Mermaid source.
