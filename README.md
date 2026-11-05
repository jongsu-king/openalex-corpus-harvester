# OpenAlex Corpus Harvester

[![DOI](https://zenodo.org/badge/1439308336.svg)](https://zenodo.org/badge/latestdoi/1439308336)

A Python CLI for collecting OpenAlex records and extracting scientific PDFs into Markdown, structured data, and a local SQLite corpus. It keeps raw responses and PDFs alongside parser output, checksums, and validation results.

The application lives in [`openalex_corpus/`](openalex_corpus/).

## Execution pipeline

```text
CLI arguments + config.yaml + OPENALEX_API_KEY
  |
  +--> validate settings and SearchSpec; acquire the corpus writer lock
  |
  v
Pipeline.harvest(): find an unfinished crawl or create a crawl record
  |
  +--> retained page exists? -- yes --> check known checksum; resume at page_offset
  |                         |
  |                         no
  |                         v
  |    OpenAlexClient.get_page(): local checks -> reserve budget -> throttle -> GET
  |      -> validate response -> save complete parsed page JSON + receipt
  |
  v
Process the next batch of Work records (up to download.concurrency)
  |
  +--> ingest(raw): preserve metadata history -> commit normalized SQL rows
  |                 -> publish metadata_raw.json and metadata.json
  |
  +--> PDF requested? -- no --> retain metadata and return the work status
  |                   |
  |                   yes
  |                   v
  |    PDFDownloader: validate cached version or try ordered source URLs
  |      -> stream .part -> validate PDF -> publish pdf_versions/<sha256>.pdf
  |      -> keep first original.pdf; record retrieval evidence
  |        |
  |        +--> no valid PDF: record failure; queue retry when applicable
  |        |
  |        +--> parse requested? -- no --> retain the PDF and metadata
  |                             |
  |                             yes
  |                             v
  |    parser child process -> extraction .staging-<run>/
  |      PyMuPDF page evidence / optional OCR / Docling / optional GROBID
  |      -> table, figure, equation and reference candidates
  |      -> quality checks -> hashed manifest -> final extractions/<run>/
  |      -> register SQL history -> select current.json when appropriate
  |
  v
All batch outcomes returned -> record membership -> advance page_offset
  |
  +--> invocation limit / pause / interrupt: retain restart position
  +--> page consumed: follow next_cursor, or mark crawl complete at its end

Recovery: reconcile persistence -> retry failed work or reparse -> verify
Exports:  selected metadata and retained artifacts -> JSONL/Parquet/etc.
```

Each arrow names an execution or persistence step; it does not imply one
transaction across the filesystem and SQLite. The [walkthrough](openalex_corpus/docs/PIPELINE_WALKTHROUGH.md)
follows the actual write order and explains what survives a stop at each boundary.

## Getting started

```bash
git clone https://github.com/jongsu-king/openalex-corpus-harvester.git
cd openalex-corpus-harvester/openalex_corpus
uv sync --locked --python 3.11 --extra dev --extra docling --extra ocr
uv run openalex-corpus doctor
```

PDF parsing needs local model files. GROBID requires Java 21; OCR requires Tesseract and Ghostscript. See the [setup instructions](openalex_corpus/README.md) before starting a parsing run. Credentials, downloaded papers, databases, models, and runtime logs stay outside Git.

## Documentation

| Reference | Contents |
|---|---|
| [CLI and setup](openalex_corpus/README.md) | Installation, queries, collection, retries, exports, and backups |
| [Architecture](openalex_corpus/docs/ARCHITECTURE.md) | Module boundaries, cursor state, budget accounting, publication, and recovery |
| [Pipeline walkthrough](openalex_corpus/docs/PIPELINE_WALKTHROUGH.md) | One command traced through requests, batches, parser workers, publication, and resume offsets |
| [Database and storage contracts](openalex_corpus/docs/DATABASE.md) | Keys, relationships, transaction boundaries, artifact hashes, and inspection queries |
| [API and download behavior](openalex_corpus/docs/API.md) | Request accounting, HTTP failures, candidate selection, range transfers, and refresh |
| [Development log](openalex_corpus/docs/DEVELOPMENT_LOG.md) | Implementation decisions, regressions, validation runs, and open issues |
| [Parser integration results](openalex_corpus/docs/ENHANCED_VALIDATION.md) | GROBID, OCR, and the retained PARTIAL result |
| [OPV inventory](openalex_corpus/docs/OPV_SCOPE.md) | Query definition, saved coverage, and resume commands |
| [Test matrix](openalex_corpus/docs/TEST_MATRIX.md) | Test cases and the boundary between offline and live tests |

## Validation snapshot

The October 2026 runs processed two real papers, covering 14 pages, and exercised the installed GROBID and OCR engines. The default test suite passed **130 tests**, with five live integrations skipped by default and exercised separately.

The OPV query reported **82,363 candidate records** on 2026-10-29. Discovery saved 608 distinct records across overlapping first pages; the final query's corpus contained 100 records when collection paused for an API key. These are different counts. The [OPV inventory notes](openalex_corpus/docs/OPV_SCOPE.md) define the search and its limits.


## Citation

If you use this software, please cite it. Machine-readable metadata is in
[`CITATION.cff`](CITATION.cff) (GitHub renders a "Cite this repository" button
from it).

To mint a resolvable **DOI**: sign in to [Zenodo](https://zenodo.org) with
GitHub, enable this repository under Zenodo's GitHub settings, then publish a
GitHub release (e.g. `v0.1.0`). Zenodo archives the release and issues the DOI;
the badge above then resolves and the `doi:` line in `CITATION.cff` can be
filled in. A DOI is issued by a registration agency (Zenodo uses DataCite) and
cannot be self-assigned.
