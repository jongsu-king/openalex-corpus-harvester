# OpenAlex Corpus Harvester

[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](openalex_corpus/pyproject.toml)

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
| [Parsers](openalex_corpus/docs/PARSERS.md) | Extraction order, retained parser evidence, and limitations |
| [GROBID setup](openalex_corpus/docs/GROBID_SETUP.md) | Building and running the local GROBID service |
| [OCR setup](openalex_corpus/docs/OCR_SETUP.md) | OCRmyPDF, Tesseract, and Ghostscript requirements and tested versions |
| [Development log](openalex_corpus/docs/DEVELOPMENT_LOG.md) | Implementation decisions, regressions, validation runs, and open issues |
| [Parser integration results](openalex_corpus/docs/ENHANCED_VALIDATION.md) | GROBID, OCR, and the retained PARTIAL result |
| [OPV inventory](openalex_corpus/docs/OPV_SCOPE.md) | Query definition, saved coverage, and resume commands |
| [Test matrix](openalex_corpus/docs/TEST_MATRIX.md) | Test cases and the boundary between offline and live tests |

## Validation snapshot

The October 2026 runs processed two real papers, covering 14 pages, and exercised the installed GROBID and OCR engines. The default test suite passed **130 tests**, with five live integrations skipped by default and exercised separately.

The OPV query reported **82,363 candidate records** on 2026-10-29. Discovery saved 608 distinct records across overlapping first pages; the final query's corpus contained 100 records when collection paused for an API key. These are different counts. The [OPV inventory notes](openalex_corpus/docs/OPV_SCOPE.md) define the search and its limits.

## Responsible use

This repository contains no papers or collected data. The downloader requests only explicitly supplied PDF URLs; it does not bypass access controls, scrape HTML, handle CAPTCHAs, or guess publisher endpoints. OpenAlex's paid archived content is disabled by default. A freely downloadable PDF can still carry license restrictions, so check them before sharing a corpus, and follow the OpenAlex and publisher terms that apply to your use.

## Citation

If you use this software, please cite it. Machine-readable metadata is in
[`CITATION.cff`](CITATION.cff), and GitHub shows a "Cite this repository" button
based on it.

## License

MIT — see [LICENSE](LICENSE).
