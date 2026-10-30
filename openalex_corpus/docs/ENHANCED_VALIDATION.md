# GROBID, OCR, and PARTIAL reprocessing

**Date:** October 29, 2026 (Asia/Seoul). This follow-up installed and exercised the optional engines while retaining the source files and previous extraction runs. The [initial validation record](VALIDATION.md) covers the earlier two-paper run.

## Installed components

| Component | Tested version | Installation and use |
|---|---|---|
| OpenJDK | 21.0.13 | Homebrew JDK selected by explicit path |
| GROBID | 0.9.1 | Native macOS ARM build from official source and models; real fulltext REST request |
| OCRmyPDF | 17.13.0 | Installed in the project virtual environment |
| Tesseract | 5.5.3 | English and OSD models |
| Ghostscript | 10.08.0 | Used by the OCR pipeline |

GROBID ran at `127.0.0.1:8070`, with its administration port 8071 also bound to loopback. Configuration allowed one processing engine, a 1,536 MiB Java heap, and 1,024 MiB/120 seconds for pdfalto. RSS after model loading was approximately 1.6–1.8 GiB. Service start, status, and stop are handled by `scripts/grobid_service.py`; no login autostart or global shell configuration was added.

Upstream Gradle packaging failed on duplicate JAR entries. A local Gradle init setting applied `DuplicatesStrategy.EXCLUDE`, matching the distribution ZIP policy without modifying official source files. Reproduction details are in [GROBID_SETUP.md](GROBID_SETUP.md). After a configuration restart, the live integration test passed again; the recorded engine pool was 1/1 and RSS was approximately 1,658.5 MiB.

## Live GROBID result

The source PDF for `W4402950921` was submitted to `/api/processFulltextDocument`.

- TEI XML: **103,023 bytes**, with 108 structural items.
- Structured references: **16**, each with a raw citation string and page/coordinate evidence.
- Source PDF SHA-256: unchanged.
- Live service opt-in test: 1 passed; local TEI tests also passed.
- External consolidation, including Crossref header/citation/funder lookups: disabled.

GROBID selected an example title from the paper's appendix: `DocLayNet: A Large Human-Annotated Dataset for Document-Layout Analysis`. OpenAlex identified the paper as `Docling Technical Report`. Both values were retained with their sources and **`METADATA_TITLE_DISAGREEMENT`** was recorded. The GROBID title did not replace the existing metadata.

Evidence is stored in `data/validation/grobid/integration_evidence.json`, the raw TEI, and service logs. The extraction's `academic_parser` records the successful service call and GROBID 0.9.1 version reported in the TEI. These are historical run results, not a check of the service's present availability.

## Live OCR result

Both OCR opt-in tests passed: selected-page recognition preserved the digital page, and an out-of-range page request was rejected without changing the source. The separate `scripts/validate_ocr.py` run exercised the two inputs below and produced a derived PDF for each.

| Input | Characters before OCR | Characters after OCR | Checks |
|---|---|---|---|
| Mixed fixture: one digital page and one scanned page | `[47, 0]` | `[47, 100]` | Only page 2 underwent OCR; the digital page retained its original provenance |
| Separate raster PDF made from the first page of a real paper | `[0]` | `[2241]` | `Docling` detected in extracted text; page count preserved |

The test inputs and source paper retained their original hashes. Checks covered output PDF opening, page counts, hashes, extracted characters, and visual preservation. The second input was deliberately rasterized for the test; it was not a newly collected scanned paper. This OCR validation made no network requests. Recognition used `eng`; `osd` data was also installed.

The OCR launcher was also updated to find `ocrmypdf` beside the active Python executable when it is absent from shell `PATH`. A zero exit code alone is insufficient: source immutability, output validity, and page counts are checked. Evidence: `data/validation/ocr/latest_evidence.json`. Setup: [OCR_SETUP.md](OCR_SETUP.md).

## Reprocessing the PARTIAL paper

```bash
.venv/bin/openalex-corpus --config config/enhanced.yaml reparse --status PARTIAL --max-works 10
```

The run processed `W4402950921` / `10.48550/arxiv.2408.09869` with Docling, PyMuPDF, pdfplumber, and GROBID.

- **9/9 pages processed**; source files and prior extraction history retained.
- GROBID supplied 16 structured reference candidates alongside 16 existing raw-text candidates. The 32 records describe candidates from two sources, not 32 distinct references.
- OCR was `not_required` because the source already contained digital text.
- The paper remained **`PARTIAL`**. Three equation candidates, table and reading-order disagreements, and the metadata-title conflict still required review.

The selected run retained these findings: `EQUATION_UNVERIFIED`, `equation_parse_failed`, `TABLE_UNVERIFIED`, `docling_table_values_disagree`, `table_cell_values_disagree`, `table_detection_disagreement`, `reading_order_requires_review`, `primary_text_disagreement`, `text_parser_disagreement`, `inline_image_original_bytes_unavailable`, `vector_graphics_preserved_in_page_image`, and `METADATA_TITLE_DISAGREEMENT`.

Run result: `data/validation/enhanced_reparse.json`. Selected-run pointer: `data/papers/W4402950921/current.json`. Integrity result: `data/validation/enhanced_verify.json`. The latter returned `valid=true`; storage integrity did not resolve the extraction issues.

## OPV collection snapshot

The final search combined organic-photovoltaics expressions across OpenAlex titles, abstracts, and keywords. It reported **82,363 matching works** on October 29, 2026. No year, language, work-type, or OA restriction was applied. These are search candidates awaiting relevance review; [OPV_SCOPE.md](OPV_SCOPE.md) records the query and its limits.

Eight first-page searches preserved **608 unique raw works**. Separately, the final query's **first 100 works** were stored in the `data/opv` corpus. The report saved at approximately **16:22:56 KST on October 29** recorded `PAUSED_API_KEY_REQUIRED`, `api_key_present=false`, and `query_exhausted=false`: collection stopped before requesting the next cursor. This is the historical report state, not a current credential check. The 608 discovery records and 100 corpus records are separate collection scopes.

| Local artifact | Contents |
|---|---|
| `data/opv_discovery/opv_discovered_candidates_608.csv` | Browseable discovery list |
| `data/opv_discovery/first_pages_unique_raw.jsonl` | 608 unique raw Work objects |
| `data/opv/exports/corpus.jsonl`, `data/opv/exports/corpus.parquet` | 100 stored works from the final query |
| `data/opv/collection_report.json` | Collection status and progress |

Resume command after configuring a key:

```bash
.venv/bin/python scripts/harvest_opv.py --env-file .env --all
```

The metadata budget remained $0.05 per UTC day with a 1,000-request limit. `--all` removes the work-count limit; budget and server limits still apply. Hosted-content downloads remained disabled at 0 downloads/$0. This OPV run collected metadata only and did not exhaust the final query.

## Regression results and remaining coverage

The expanded default suite returned **130 passed, 5 skipped, 5 warnings**, recorded in `data/validation/enhanced_pytest_offline.txt`. Ruff, source-distribution creation, and wheel creation passed. The five skipped tests were opt-in checks: one external API, one Docling, one GROBID, and two OCR tests. Their separate live runs are recorded independently of the default suite.

Added regression coverage includes TEI title-conflict preservation, OCR output integrity, scoped exact search, explicit `.env` loading, OPV chunks/checkpoints/seed validation, and distinguishing an absent PDF from a download error.

Still untested or incomplete: the full OPV cursor collection, sustained processing of tens of thousands of PDFs, Linux hardware, paid hosted-content downloads, and a PostgreSQL backend. Equation and table interpretation remains unresolved in the PARTIAL paper.
