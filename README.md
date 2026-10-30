# OpenAlex Corpus Harvester

A Python CLI for collecting OpenAlex records and extracting scientific PDFs into Markdown, structured data, and a local SQLite corpus. It keeps raw responses and PDFs alongside parser output, checksums, and validation results.

The application lives in [`openalex_corpus/`](openalex_corpus/).

![Module and storage layout](openalex_corpus/docs/diagrams/pipeline-overview.svg)

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
| [Development log](openalex_corpus/docs/DEVELOPMENT_LOG.md) | Implementation decisions, regressions, validation runs, and open issues |
| [Parser integration results](openalex_corpus/docs/ENHANCED_VALIDATION.md) | GROBID, OCR, and the retained PARTIAL result |
| [OPV inventory](openalex_corpus/docs/OPV_SCOPE.md) | Query definition, saved coverage, and resume commands |
| [Test matrix](openalex_corpus/docs/TEST_MATRIX.md) | Test cases and the boundary between offline and live tests |

## Validation snapshot

The October 2026 runs processed two real papers, covering 14 pages, and exercised the installed GROBID and OCR engines. The default test suite passed **130 tests**, with five live integrations skipped by default and exercised separately.

The OPV query reported **82,363 candidate records** on 2026-10-29. Discovery saved 608 distinct records across overlapping first pages; the final query's corpus contained 100 records when collection paused for an API key. These are different counts. The [OPV inventory notes](openalex_corpus/docs/OPV_SCOPE.md) define the search and its limits.
