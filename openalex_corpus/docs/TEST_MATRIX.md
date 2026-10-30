# Test coverage and execution boundaries

This matrix maps the 25 minimum cases from the project request to concrete
pytest tests. Most tests run entirely offline with HTTPX mock transports,
temporary SQLite databases, synthetic PDFs, and deliberate failure injection.
They demonstrate specific invariants; they do not establish exact reconstruction
of arbitrary scientific PDFs. The final validation report records the final
suite count and any actual OpenAlex/OA-paper runs separately.

The 2026-10-29 follow-up installed GROBID and OCR and exercised both with real
engines. The final default suite passed **130 tests**, with 5 opt-in tests
skipped. Separately, the live GROBID test and both live OCR tests passed; the
OCR validation script also recognized a rasterized page from a real paper.
See [the follow-up validation report](ENHANCED_VALIDATION.md) for the preserved
inputs, outputs, and limits of that evidence.

| # | Required case | Concrete tests | Scope of evidence |
| --- | --- | --- | --- |
| 1 | Normal OpenAlex response | `test_api.py::test_normal_response_and_full_unknown_fields_preserved` | Mock successful Work/list response; unknown fields retained |
| 2 | Empty results | `test_api.py::test_empty_search`; `test_pipeline.py::test_terminal_empty_page_is_successfully_checkpointed` | Empty terminal page is stored and checkpointed |
| 3 | Cursor pagination | `test_api.py::test_cursor_pagination_and_loop_guard`; `test_pipeline.py::test_midpage_limit_resume_has_no_skipped_work` | Cursor movement, loop rejection, pause inside a page, and exact resume |
| 4 | HTTP 429 | `test_api.py::test_429_retry_then_success`; `test_download.py::test_download_429_is_bounded` | Bounded retries with response-driven backoff |
| 5 | API timeout | `test_api.py::test_timeout_is_bounded_and_does_not_leak_url` | Retry exhaustion and credential-safe error reporting |
| 6 | API key error | `test_api.py::test_key_error_is_not_retried` | Both 401 and 403 fail without repeated authentication attempts |
| 7 | PDF download failure | `test_download.py::test_download_connection_failure` | Network failure cannot publish a valid PDF |
| 8 | HTML returned as PDF | `test_download.py::test_html_is_never_published_as_pdf` | HTML rejected with either HTML or misleading PDF Content-Type |
| 9 | Corrupt PDF | `test_download.py::test_corrupt_pdf`; `test_parsers.py::test_corrupt_pdf_structured_failure` | Invalid bytes fail validation/parsing with a recorded status |
| 10 | Encrypted PDF | `test_download.py::test_encrypted_pdf`; `test_parsers.py::test_encrypted_pdf_structured_failure` | Password-protected fixture is identified and not claimed parsed |
| 11 | Multiple columns | `test_parsers.py::test_two_column_reading_order` | Synthetic two-column source; observed reading-order behavior |
| 12 | Complex table | `test_parsers.py::test_merged_table_exports_geometry_and_empty_cells` | Merged-cell geometry and empty cells retained in exports; not proof for every table style |
| 13 | Equations | `test_parsers.py::test_equation_keeps_source_crop_no_invented_latex` | Source equation region retained; unsupported LaTeX is never invented |
| 14 | Image-only PDF | `test_parsers.py::test_scanned_pdf_never_claims_complete` | Missing text/OCR requirement explicitly prevents a complete result; does not exercise an actual OCR engine |
| 15 | No abstract | `test_metadata.py::test_no_abstract_and_no_references_have_explicit_missing_state` | Missing abstract remains null with a source-derived missing reason |
| 16 | No references | `test_parsers.py::test_no_references_explicit_reason`; `test_metadata.py::test_no_abstract_and_no_references_have_explicit_missing_state` | No fabricated reference entries; absence/not-detected is recorded |
| 17 | Duplicate DOI | `test_database.py::test_duplicate_doi_flags_do_not_merge_works` | Distinct Work IDs sharing a normalized DOI remain separate |
| 18 | Different manuscript versions | `test_download.py::test_distinct_manuscript_versions_preserve_original`; `test_database.py::test_pdf_versions_and_provenance_are_not_overwritten` | Distinct PDF hashes and provenance retained; first original remains intact |
| 19 | Repeated execution | `test_pipeline.py::test_rerun_idempotence_preserves_single_revision`; `test_database.py::test_idempotent_ingest_unknown_fields_author_order_and_affiliations` | Repeated ingestion does not multiply Work IDs or identical raw revisions |
| 20 | Interrupted execution | `test_pipeline.py::test_crash_after_ingest_before_checkpoint_is_idempotent`; `test_quality_export_cli.py::test_reconcile_post_rename_crash`; `test_quality_export_cli.py::test_metadata_pending_recovers_newer_revision` | Failure windows around metadata, checkpoint, filesystem rename, and DB commit recover from durable evidence |
| 21 | Preserve original JSON fields | `test_metadata.py::test_raw_unknown_null_and_empty_fields_preserved_without_mutation`; `test_database.py::test_metadata_revision_history_is_immutable`; `test_quality_export_cli.py::test_export_retains_nested_data` | Unknown/nested/null/empty values and old revisions survive normalization/export |
| 22 | Database integrity | `test_database.py::test_schema_all_required_tables_wal_foreign_keys`; `test_database.py::test_transaction_rollback_keeps_previous_raw_and_relations`; `test_database.py::test_crash_reopen_and_backup` | Required tables, enforced FKs, rollback, WAL, and backup integrity |
| 23 | Markdown image targets | `test_parsers.py::test_markdown_image_references_exist`; `test_quality_export_cli.py::test_broken_image_reference_fails` | Referenced local image files exist; missing references fail validation |
| 24 | Missing reasons | `test_metadata.py::test_nested_field_status_tracks_author_affiliation_and_location_nulls`; `test_database.py::test_status_totals_and_percent_denominators` | Absent versus null metadata fields, source paths, and work-level missing reasons |
| 25 | Every PDF page validated | `test_parsers.py::test_all_pages_and_blank_page`; `test_parsers.py::test_page_failure_is_retained`; `test_parsers.py::test_docling_missing_page_flagged`; `test_quality_export_cli.py::test_missing_page_is_never_success` | Full expected page range, real blank pages, retained failures, and secondary-parser coverage |

Additional tests cover API/request-cost budgets across process restarts, budget
journal corruption, source URL credential redaction, conditional HTTP Range
resumption, ignored Range restart, license filtering, cached-file tampering,
cross-parser disagreements, exact-only reference matching, secure TEI XML parsing,
immutable extraction replay, nested export data, and explicitly denominated
statistics. Test names beginning with `test_docling_...` may inject a fake engine
result to test a validation failure; only `test_actual_docling_standard_pipeline`
is an actual installed-engine integration.

## Running tests

From the project directory, run the offline suite:

```bash
.venv/bin/python -m pytest -m "not integration and not optional" -q
```

Live OpenAlex singleton integration is an explicit opt-in. It requests one Work
by DOI, has a two-request local budget, and performs no PDF/TEI content download:

```bash
OPENALEX_RUN_NETWORK=1 .venv/bin/python -m pytest tests/test_integration.py -q
```

Installed Docling integration is also an explicit opt-in. Set the local model
artifact path to use previously downloaded model assets:

```bash
OPENALEX_RUN_DOCLING=1 DOCLING_ARTIFACTS_PATH=/absolute/path/to/models \
  .venv/bin/python -m pytest \
  tests/test_parsers.py::test_actual_docling_standard_pipeline -q
```

Setting `OPENALEX_ALLOW_MODEL_DOWNLOADS=1` permits that opt-in test to download
required model assets. It can use network bandwidth, storage, CPU, and RAM; it
does not enable OpenAlex paid content downloads. The default configuration leaves
both model downloads and protected content downloads disabled.

## Integration evidence and untested boundaries

| Component | Offline evidence | Actual execution boundary |
| --- | --- | --- |
| OpenAlex REST API | Mock responses, pagination, rate limits, costs, errors | Opt-in `test_live_openalex_singleton`; actual run evidence belongs in the final validation report |
| OA publisher/repository PDF | Mock HTTP streaming, validation, cache, Range | Real small-paper run evidence belongs in the final validation report; source access can change |
| PyMuPDF + pdfplumber | Actual libraries execute on synthetic PDFs | The listed offline parser tests execute real native text/image/table extraction |
| Docling | Injected-result disagreement/page-gap checks | Actual engine execution requires the opt-in test or a documented real-paper run; installed package alone is not execution evidence |
| TEI parsing with lxml | Actual parser runs on local TEI fixture; external-entity rejection | XML fixture parsing is tested independently of GROBID server availability |
| GROBID HTTP service | Adapter implementation and local TEI parser tests | GROBID 0.9.1 processed a real paper, producing TEI and 16 structured references; `test_actual_grobid_fulltext_with_raw_references` passed |
| OCRmyPDF/Tesseract | Scanned-page detection, unresolved-OCR status, output validation | Two opt-in tests passed; selected-page recognition preserved native text, and `scripts/validate_ocr.py` recognized a rasterized real-paper page |
| Protected OpenAlex PDF/TEI | Mock budget, priority, redirect credential tests | No paid/protected content download execution claimed; default budget is zero |
| PostgreSQL | None | Backend is not implemented or tested; SQLite is the supported backend |

Tests that deliberately fail a parser or synthesize a PDF are evidence for
failure handling and preservation behavior. They must not be presented as
successful reconstruction of actual papers, successful OCR, or a live GROBID
deployment. A `COMPLETE` status reflects configured automated gates, not proof
of mathematically exact reproduction of source content.
