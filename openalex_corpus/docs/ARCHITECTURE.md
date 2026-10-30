# Collection, extraction, and recovery architecture

OpenAlex Corpus stores Work metadata, accessible source PDFs, parser evidence, and selected Markdown in one local corpus. This reference describes the implemented call order and durable state. `COMPLETE` means the selected extraction passed the implemented checks; it does not establish exact reproduction of every source element.

All schematics below are fixed-width text. Their arrows describe calls, data dependencies, or explicitly labeled commit order. They do not imply that every operation is concurrent or that files and SQLite share a transaction.

Read the [pipeline walkthrough](PIPELINE_WALKTHROUGH.md) for an end-to-end explanation and the [README](../README.md) for commands. Detailed companion references are [DATABASE](DATABASE.md), [API](API.md), [PARSERS](PARSERS.md), [OCR_SETUP](OCR_SETUP.md), and [GROBID_SETUP](GROBID_SETUP.md).

## Contents

1. [Execution map and stage contracts](#1-execution-map-and-stage-contracts)
2. [Writer ownership and process lifecycle](#2-writer-ownership-and-process-lifecycle)
3. [Search, HTTP, and cursor checkpoints](#3-search-http-and-cursor-checkpoints)
4. [Metadata identity, normalization, and commit order](#4-metadata-identity-normalization-and-commit-order)
5. [PDF candidates, caching, streaming, and versions](#5-pdf-candidates-caching-streaming-and-versions)
6. [Parser execution and evidence selection](#6-parser-execution-and-evidence-selection)
7. [Quality decisions and status scopes](#7-quality-decisions-and-status-scopes)
8. [Immutable runs and publication order](#8-immutable-runs-and-publication-order)
9. [Interrupt windows, reconciliation, and retries](#9-interrupt-windows-reconciliation-and-retries)
10. [Database relationships and complete table map](#10-database-relationships-and-complete-table-map)
11. [File layout and artifact interpretation](#11-file-layout-and-artifact-interpretation)
12. [Resource budgets and operating limits](#12-resource-budgets-and-operating-limits)

## 1. Execution map and stage contracts

The main coordinator is [`Pipeline`](../src/openalex_corpus/pipeline.py). `harvest()` drives a bounded crawl; `process_work()` serializes handling of the same raw Work ID; `_process_work()` coordinates metadata, downloading, and optional parsing. `parse()` owns extraction attempts and selection. Individual parser adapters write into their caller's staging directory and cannot publish themselves.

```text
CLI command + Settings + SearchSpec
  |
  v
run_job() -> corpus_lock() -> Pipeline -> harvest(spec, max_works, options)
  |                                        |
  |                            begin_crawl() / get_checkpoint()
  |                                        |
  |                     saved page? --------+-------- no -> OpenAlexClient.get_page()
  |                          | yes                           |
  |                          v                               v
  |                     verify receipt                 reserve -> GET -> save page
  |                          |                               |
  |                          +---------------+---------------+
  |                                          |
  |                                batch results[offset:...]
  |                                          |
  |                      process_work(raw) [one lock per raw ID]
  |                                          |
  |                               ingest(raw) -> SQLite + JSON
  |                                          |
  |                          downloader supplied? -- no --> return metadata-only
  |                                          | yes
  |                           PDFDownloader.download(raw)
  |                                          |
  |                         valid source PDF? -- no --> record failure / retry
  |                                          | yes
  |                           parse_markdown? -- no --> return PDF-only
  |                                          | yes
  |                              Pipeline.parse(wid, pdf)
  |                                          |
  |                        spawn worker -> parse_pdf() -> check_extraction()
  |                                          |
  |                       finalize run -> register -> choose -> _publish()
  |                                          |
  +------------------------------------------+
                                             v
                         record_crawl_work() -> save_checkpoint()
                                             |
                     next batch / next cursor / LIMIT_REACHED / COMPLETED

Key: "->" = ordered call or handoff; branches are labeled yes/no.
     One batch must finish before its checkpoint is advanced.
```

The following contracts use the same stage numbers throughout this document. “Durable writes” identifies recoverable evidence, not one cross-storage transaction.

| Stage | Input and responsible call | Output | Durable writes | Failure and resume contract |
|---|---|---|---|---|
| 0. Prepare | CLI `main()`, `load_settings()`, `run_job()` | Validated settings, locked writer, repository | Directories, schema migrations, logs | Invalid configuration fails locally; no writer lock waiting is configured |
| 1. Acquire page | `harvest()`, `get_page()`, `next_cursor()` | Full response and guarded continuation | Budget reservation, page JSON, receipt | Pause preserves checkpoint; a saved page is replayed before requesting it again |
| 2. Ingest metadata | `ingest()` -> `normalize_work()` -> `ingest_work()` | Canonical Work and current projection | Raw response, immutable revision, pending marker, DB transaction, current JSON | Invalid identity retains raw evidence; pending revision enables repair |
| 3. Acquire PDF | `PDFDownloader.download()` -> `_stream()` -> `validate_pdf()` | Valid PDF path/hash or classified failure | Partial bytes/receipt, PDF version, first original, download manifest/report | Retry only eligible failures; validated cache can avoid a request |
| 4. Extract | `_run_parser()` -> `_parser_worker()` -> `parse_pdf()` | Candidate document result | Intent, parser logs, adapter evidence, worker result/error | Time/RSS/exit failures become failed attempt evidence; staging is preserved on interruption |
| 5. Check | `check_extraction(result, page_count, staging)` | Quality status and issues | `quality_report.json`, enriched `result.json`, front-matter Markdown | Known unresolved output remains `PARTIAL`; parser `FAILED` remains `FAILED` |
| 6. Commit run | `parse()` -> `record_extraction()` -> `upsert_artifact()` | Immutable run and relational rows | Manifest, final directory, run/document/elements, artifact records | Final directory can outlive incomplete DB registration; reconciliation re-registers |
| 7. Publish | Compare rank -> `_publish()` | Selected run | `current.json`, convenience links, `works.status` | Pointer is authoritative; later link/status updates can be repaired |
| 8. Recover/check | `reconcile()`, `verify()`, CLI `retry()` | Repaired projection or explicit unresolved failures | Recreated records/links, errors, queue state | Recovery validates retained bytes; it does not infer absent evidence |

Supporting boundaries are [`api/search.py`](../src/openalex_corpus/api/search.py), [`api/client.py`](../src/openalex_corpus/api/client.py), [`metadata.py`](../src/openalex_corpus/metadata.py), [`database/repository.py`](../src/openalex_corpus/database/repository.py), [`download/pdf_downloader.py`](../src/openalex_corpus/download/pdf_downloader.py), and [`quality/recovery.py`](../src/openalex_corpus/quality/recovery.py).

## 2. Writer ownership and process lifecycle

`cli.main()` resolves YAML settings and optional data-directory overrides. `Settings.prepare()` creates corpus directories. Any YAML directly inside a directory named `config` resolves relative paths from that directory's parent; other YAML files resolve from their own directory. For this checkout's bundled YAML files, that base is the `openalex_corpus/` application directory. `OPENALEX_CORPUS_DATA_DIR` and `OPENALEX_CORPUS_LOG_DIR` can override those paths. Unknown model fields are rejected.

`run_job()` takes `state/writer.lock` with `timeout=0`, constructs `Pipeline`, runs its asynchronous task, and emits JSON. The lock context exits before the `finally` block closes the owned repository. A second CLI writer fails immediately. Library callers must arrange their own use of `corpus_lock()`; constructing `Pipeline` alone does not acquire the writer lock.

```text
CLI parent process                    Per-document worker process
-----------------------------------   -------------------------------------------
writer.lock held
  Repository: one SQLite connection
  asyncio event loop
    harvest batch <= concurrency
      Work-ID lock
        download under directory lock + semaphore; release on return
        create staging directory + intent.json
        parse_slots semaphore acquired
          spawn --------------------> _parser_worker(pdf, staging, options)
          monitor every ~0.2 s           redirect fd 1 -> parser_stdout.log
          |                             redirect fd 2 -> parser_stderr.log
          |                             import parsers; call parse_pdf()
          |                               native adapters, optional OCR child
          |                             write worker_result.json
          |                             or worker_error.json on BaseException
          |                                  |
          +<------------- process exits -----+
          join + read result / classify exit
          finally: if worker alive, terminate descendants/worker; kill worker if needed
        release parse_slots
      release Work-ID lock
    await entire batch; checkpoint
  close request clients; emit JSON and handle result
release writer.lock
finally: close owned repository
```

The worker uses multiprocessing's `spawn` context. It does not inherit a live parser model from the parent. Native-library output goes to per-run files so it does not interleave with CLI JSON. Native crashes may leave no Python error file; a nonzero exit code or absent worker result still produces `PARSER_PROCESS_FAILED`.

The parent samples worker plus descendant RSS. It raises `PARSER_TIMEOUT` or `PARSER_MEMORY_LIMIT` when the configured limit is observed. The cleanup path requests termination of descendants, terminates the worker, waits, and kills the worker if necessary. Sampling is not an instantaneous operating-system memory cap or a guarantee against brief peaks.

There are separate concurrency controls: a crawl batch size, pipeline Work locks, downloader directory locks, a download semaphore, and parser slots. Within a document, parser adapters run in sequence. A slow parse can hold its batch open and delay the next checkpoint even after another Work in that batch has finished.

SQLite uses WAL, foreign keys, `synchronous=FULL`, and a 30-second busy timeout. `Repository.transaction()` starts `BEGIN IMMEDIATE`; nested calls use savepoints. Readers can continue during collection. Filesystem changes and SQLite transactions remain separate durability domains.

## 3. Search, HTTP, and cursor checkpoints

### Stage 1: bounded search identity

[`SearchSpec.params()`](../src/openalex_corpus/api/search.py) constructs deterministic query/filter parameters and requests full Work objects. It rejects unsupported filters, conflicting inputs, invalid identifier forms, and reversed date/year bounds. `harvest()` requires at least one search/filter beyond `per_page` and accepts `max_works` only in `1..10000`.

Crawl identity hashes the serialized parameters plus effective `download_pdf` and `parse_markdown` choices. Parsing implies PDF acquisition. `max_works` is excluded because it limits this invocation, not the search itself. `resume=True` selects the newest unfinished matching crawl; a completed crawl starts a new run. `resume=False` also starts a new run.

A checkpoint contains `cursor`, `offset`, `processed`, `total_count`, `status`, and merged `extras`. Extras can include a page path/hash, visited cursors, a future `retry_at`, and a final coverage audit. If `retry_at` is still in the future, the call returns `PAUSED` before page processing.

### Request and receipt sequence

```text
harvest()                         BudgetJournal / API / corpus files
--------------------------------  ------------------------------------------------
choose cursor C
hash C -> crawls/<id>/pages/<hash>.json
  |
  +-- saved file exists? -- yes -> read receipt SHA (or matching checkpoint SHA)
  |                               compare actual bytes -> decode saved page
  |
  +-- no -> get_page(params, C)
              reject anonymous C != "*"; reject api_key/select parameters
              for each bounded HTTP attempt:
                reserve metadata count + estimated cost under journal lock
                throttle -> GET /works?per_page=100&cursor=C&...
                observe rate headers and additional cost
                classify transport/status/JSON response
              validate result objects with nonempty id and meta{count,next_cursor}
              return page to harvest()
            harvest(): atomic write parsed page JSON
                       atomic write receipt {sha256, retrieved_at, params, cursor, headers}
  |
  v
next_cursor(page, C) -> reject malformed/repeated/empty-continuation cursor
  |
check offset <= len(results)
  |
process bounded batch -> register crawl membership -> save checkpoint
  |
page exhausted? -- no --> keep C + new offset
  | yes
  +-- terminal --> coverage_audit -> COMPLETED
  +-- continuation --> reject visited cursor -> save new C + offset=0
```

The page receipt hashes the actual saved JSON bytes. If a receipt is unavailable, a matching checkpoint page hash may supply the expected hash. A retained page with no recorded hash can still be decoded; a known hash mismatch is a hard pause, not permission to silently replace the response.

The API client's ID check requires a nonempty value. Canonical `W` plus digits
validation occurs during ingestion. A truthy but malformed Work ID can therefore
remain in a saved page and raw-response evidence before ingestion rejects it.

HTTP reservations precede throttling and network I/O. A crash after reservation still consumes budget. Transport failures, HTTP 429, and HTTP 5xx use bounded transient retries. Authentication/access failures, 404, other terminal statuses, invalid JSON, and malformed response shape do not enter that transient retry loop.

A short `Retry-After` participates in the retry wait. A delay beyond `max_retry_after_seconds` returns an error with `retry_at`; the crawl records the future pause. An exhausted reported allowance also produces a pause. `OPENALEX_API_KEY` is read from the environment and sent as a Bearer header; it is not a YAML field. Errors and stored download URLs redact sensitive values.

### Cursor plus offset: a worked example

Assume page `A` contains 100 Work occurrences and continues to `B`, batches are at most three Works, and the first invocation allows 30 Works. Offsets below are zero-based; human entry numbers are one-based.

```text
Invocation 1, max_works=30
  checkpoint: cursor=A, offset=0, processed=0
  fetch/save A and its receipt
  process A[0:3]   -> checkpoint A, offset=3,  processed=3
  ...
  process A[27:30] -> checkpoint A, offset=30, processed=30
  return LIMIT_REACHED; saved A[30:100] is still available

Invocation 2, same query/options, max_works=75
  load A from disk, verify receipt; no GET for A
  begin with A[30] = entry 31
  process remaining 70 occurrences -> A, offset=100, processed=100
  save cursor=B, offset=0, processed=100
  fetch/save B, process its first 5 occurrences
  checkpoint B, offset=5, processed=105
  return LIMIT_REACHED; processed_this_run=75

Interruption variant
  saved checkpoint A, offset=27
  Works A[27:30] finish, but process stops before checkpoint update
  resume starts at A[27] again; some Work operations are replayed
```

`processed` counts processed occurrences; `crawl_work_ids` counts unique Work IDs. Repeated IDs can therefore make those totals differ. Membership insertion, revisions, and metadata upserts are idempotent for their keys. A successful selected parse can be reused, but partial parses may create another run on replay. Network requests have no exactly-once guarantee.

A batch uses `asyncio.gather(..., return_exceptions=True)`. If an entry raises, other entries may already have completed. The code records the error and pauses without advancing that batch's offset. Previously completed members can be repeated when the batch resumes.

Completion requires consuming a terminal page, not merely reaching `max_works`. Repeated/cyclic cursors, an empty page with a continuation, and an offset beyond the saved page length stop processing. Live results can change order or count across requests. `coverage_audit` compares unique IDs with the latest reported count, but a mismatch alone is not proof of loss or a complete explanation of it.

## 4. Metadata identity, normalization, and commit order

### Stage 2: preserve before projecting

`Pipeline.ingest()` keys paper directories by valid `W` plus digits. A normalized DOI is a lookup value, not the primary key. Separate Work IDs with one DOI remain separate records and appear in `duplicate_doi_ids`; their histories are not merged.

There are two hash domains. `raw_sha256` hashes sorted compact JSON representing the payload. Artifact and manifest hashes cover actual file bytes. The pretty-printed raw JSON file can have a different hash from the canonical payload that names it.

```text
Pipeline.ingest(raw)
  1. Canonical JSON -> payload digest H
  2. Save raw_responses/H.json if absent
  3. normalize_work(raw) -> validate Work ID -> papers/W.../
       failure -> H.status.json + errors row; raise; raw response remains
  4. Retain/validate metadata_history/H.json
       existing file: compare decoded payload; verify registered byte hash if known
  5. Write metadata_pending.json -> {raw_path: metadata_history/H.json}
  6. Repository.ingest_work(raw, normalized, raw_path) transaction:
       upsert works current fields
       insert-or-ignore metadata_revisions(Work, H)
       replace current relationship rows; upsert identified entities
       report other Work IDs having the same normalized DOI
       COMMIT
  7. Atomically write metadata_raw.json
  8. Atomically write metadata.json
  9. Remove metadata_pending.json
 10. Register metadata_raw artifact for immutable history file + its byte SHA
 11. Return Work information

Recovery boundaries:
  before 6 -> history + pending can rebuild the DB projection
  after 6  -> DB can be newer than current JSON copies until replay/reconcile
  after 9  -> artifact registration can still be incomplete
```

Steps 6 and 10 are separate database transactions. The immutable raw revision reaches disk before step 6; the two current JSON copies are written after it. This order matters when investigating a database/file disagreement.

Re-ingesting the same payload reuses its history path and does not add a second `(work_id, raw_sha256)` revision. A retained revision with changed content or a known checksum mismatch fails explicitly. A new payload adds a revision and changes the current projection while preserving earlier revision evidence.

### Source values and missing values

Normalization copies source values and adds provenance fields. Unknown fields remain available in raw JSON. `null`, an empty string, and an empty list keep distinct meanings. In particular, an empty `title` is not filled from `display_name`.

Nested fields carry `source`, `source_field`, `retrieval_status`, `validation_status`, and `missing_reason`. Examples include `authorships[0].author.orcid` and `locations[0].license`. A `present` value is source-supplied evidence, not an independent verification of that value.

Abstract reconstruction follows positions from the OpenAlex inverted index. Gaps or conflicting positions retain the per-position candidates and produce `abstract_text=null` with `parse_failed`. The normalizer does not fill missing words from surrounding context.

Current metadata relationship rows are rebuilt for the Work during `ingest_work()`: authorships, institutions, topics, identifiers, locations, and source reference links. Entity rows require supplied source IDs. An unidentified author or institution can still have a relationship payload with a null entity foreign key; the code does not invent an OpenAlex identity.

Metadata history is separate from extraction history. Recollecting a changed title does not rewrite old extraction front matter or old parser results. Those retain the context saved when their run was created.

## 5. PDF candidates, caching, streaming, and versions

### Stage 3: ordered candidates and cache eligibility

[`pdf_candidates()`](../src/openalex_corpus/download/pdf_downloader.py) retains first occurrence order while removing identical URL strings. It considers only the supplied PDF URLs; it does not crawl landing pages to discover additional links.

```text
Candidate list, in order
  1. content_urls.pdf                 source=openalex_content
  2. best_oa_location.pdf_url         source=best_oa_location
  3. primary_location.pdf_url         source=primary_location
  4. locations[i].pdf_url             only locations with is_oa == true
                |
                v
deduplicate URL strings, retaining first candidate metadata
                |
try validated cache associated with one of these URL hashes
                |
cache unavailable / refresh requested
                v
for each candidate:
  require http(s)
  skip ordinary host explicitly marked is_oa=false
  apply allowed_licenses when configured
  for bounded attempts: stream -> validate PDF -> publish version
    first valid result -> return immediately
    exhausted/terminal failure -> retain attempt -> continue next candidate
all candidates fail -> aggregate actionable failure + all attempt evidence
```

The OpenAlex content candidate carries a license from `best_oa_location`, but its archived version/license association is explicitly unverified. Ordinary-host candidates explicitly marked non-OA are skipped. A missing license remains missing; an allowlist can reject it.

Without `refresh_existing`, cached manifest versions are checked newest first. Reuse requires a URL hash matching a current candidate, a structurally valid PDF, and the expected SHA-256. An imported `original.pdf` with no version history can be reused if valid, but returns `source_url=null`, `license=null`, and `retrieval_status=unverified`.

`download_count` increments once for a Work that requires network candidate processing after cache handling, up to the configured `max_downloads` cap. It is not a count of every HTTP attempt or every redirect. `refresh_existing` bypasses cache reuse but does not bypass candidate policies, request budgets, or file-size limits.

### Range and partial-file contract

Partial bytes live at `.downloads/<url-sha256>.part`; the neighboring JSON receipt stores the validator and redacted source/final URLs. The downloader sends `Accept-Encoding: identity` and follows redirects explicitly.

```text
part exists + readable receipt with validator?
  no  -> offset=0; full GET; open partial file with "wb"
  yes -> offset=N; GET with Range: bytes=N- and If-Range: validator
           |
           +-- HTTP 206 -> require Content-Range bytes N-end/total
           |              reject changed supplied response validator
           |              append only when N > 0
           |              verify final size == total
           |
           +-- HTTP 200 -> server ignored Range; restart with "wb", not append
           |
           +-- HTTP 416 -> remove receipt; bounded retry starts from zero
           |
           +-- bad range / changed validator -> remove receipt; retry from zero

Before/during write: reject known non-PDF content types and configured oversize.
After write: flush/fsync; check identity Content-Length and ranged total.
Then validate_pdf(): signature, openability, encryption, pages, structural repair.
```

A reusable validator is a strong ETag or, if unavailable, Last-Modified. Without one, retained bytes are not appended to a new response. Redirects reject unsupported schemes and HTTPS-to-HTTP downgrade. The loop allows at most six request/redirect iterations.

Authorization is attached only to HTTPS requests whose host is `content.openalex.org`. Those attempts reserve the cumulative content budget before sending. A blocked content candidate does not prevent trying another eligible OA candidate.

`validate_pdf()` computes the file hash, checks for `%PDF-` in the first 1,024 bytes, opens the document with PyMuPDF, rejects encryption and zero pages, loads every page, and rejects documents that required structural repair. Passing a Content-Type check alone never publishes a PDF.

### Version publication and result handling

Validated bytes are moved to `pdf_versions/<sha256>.pdf`. Existing matching bytes are reused. Corrupted bytes at that content-addressed path are quarantined before restoration. The first valid source is copied through a temporary file to `original.pdf`; later versions do not replace that first original.

The downloader appends version and attempt records to `download_manifest.json`, removes the partial receipt after success, and returns the path, hash, source, license, version, validation, and retrieval evidence. Failed partial bytes remain available for inspection or a safe retry.

`_process_work()` writes `download_report.json` for the latest operation. On success it registers the `pdf` artifact and, if absent, the `original_pdf` artifact, completes the download job, and completes download retry rows. It then parses only when requested.

On failure it records the download job/error and returns `PENDING_RETRY` for retryable causes or `PDF_UNAVAILABLE` for terminal acquisition outcomes. A previously successful Work retains its status. The failure does not remove collected metadata, historical PDFs, or an existing selected extraction.

## 6. Parser execution and evidence selection

### Stage 4: one ordered pass inside each worker

[`parse_pdf()`](../src/openalex_corpus/parsers/__init__.py) initializes independent arrays for pages, blocks, tables, figures, equations, references, warnings, and errors. It records installed package versions and a policy disabling generative completion. The actual adapter call order is fixed.

```text
original PDF
  |
  1. pymupdf_parser.extract_pdf()
  |    pages + text blocks + spans + image resources + figures + equation regions
  |    pymupdf_raw.json + original-page renders + fallback page text
  |    fatal open/extract failure -> _finish(FAILED) immediately
  |
  2. ocr.run_ocr(original, selected sparse/scanned pages)
  |    disabled/unavailable/failure -> retain unresolved pages + warnings
  |    success -> separate OCR PDF -> second extract_pdf() for OCR evidence
  |               update only selected pages with enough recovered characters
  |
  +---- parsing_pdf = OCR derivative on OCR success; otherwise original PDF
  |
  3. table_parser.extract_tables(parsing_pdf)
  |    pdfplumber + PyMuPDF candidates; independent text comparison
  |
  4. docling_parser.parse_docling(parsing_pdf)
  |    available -> headings/order/tables/figures/formula regions
  |    unavailable -> keep native evidence + reason
  |
  5. retain additional equation crops; literal LaTeX candidates; asset evidence
  |
  6. choose page Markdown from Docling or retained fallback
  |    append table/image/equation assets at their source page
  |
  7. reference_parser.extract_references(native/OCR blocks)
  |    provided TEI? -> parse_tei() + retain source.tei.xml
  |    else GROBID configured? -> request_tei() + retain grobid.tei.xml
  |    append structured reference candidates and TEI metadata
  |
  8. compute unresolved status -> _finish()
       paper_body.md + pages/references/equations/figures JSON + document.json
```

The native pass runs even when Docling is enabled. Docling provides the preferred structural candidate when available, while the other adapters retain evidence for comparison. There is no parallel fan-out across adapters for one PDF.

OCR runs only for selected pages classified `ocr_required`, and only when enabled and its executable is available. It uses OCRmyPDF/Tesseract with `--jobs 1`, `--language eng`, selected page numbers, and a separate derivative. The adapter validates the derivative, unchanged source hash, and equal page count. It does not rewrite `original.pdf` or automatically identify arbitrary OCR languages.

After OCR succeeds, the second native pass supplies recovered text. A selected page is marked recovered only when its character count reaches `text_min_chars`. Digital pages retain digital provenance; OCR use on a neighboring page does not relabel them. A derivative can exist while a page still has insufficient text and remains unresolved.

### Candidate comparisons and page selection

The comparison helper normalizes NFKC/case and certain line-break hyphenation for token counting. This comparison normalization does not modify persisted source text. Disagreement is a review signal, not proof that one candidate is correct.

| Comparison or choice | Implemented condition | Effect |
|---|---|---|
| Native versus pdfplumber page text | At least 10 secondary tokens; unmatched fraction above 0.12 | Add `text_parser_disagreement`; retain evidence |
| Docling versus native/OCR page text | At least 10 fallback tokens; unmatched fraction above 0.18 | Select fallback page text; record `primary_text_disagreement` |
| Docling page absent | Page number missing from primary page set | Mark full-page `missing_regions`, retain fallback |
| Table overlap | Same page, both boxes, overlap above 0.45 | Link alternatives through `candidate_group_id` |
| Overlapping table values agree | `same_cell_matrix()` after whitespace normalization | Mark `cell_matrix_agreement` |
| Overlapping table values differ | Matrices disagree | Keep both candidates and a warning |

Tables preserve rows, cells, spans, empty values, and nulls. CSV, JSON, HTML, Markdown, and region images are output formats for extracted table evidence. Agreement does not verify scientific units, header meaning, or the source's own numerical correctness.

`image_object` and `figure_region` are distinct candidate kinds. One published figure may produce multiple candidates, so candidate count is not the count of conceptual figures. Full-page renders preserve vector content even when individual vector figures are not isolated.

An equation without verified LaTeX keeps `latex=null`, failure/unverified status, and the original region. A fixed mapping of observed Unicode characters can add an unverified `latex_candidate`; it does not reconstruct fractions, matrix layout, superscripts, subscripts, or missing symbols. Any equation candidate currently makes the coordinator's result unresolved and therefore `PARTIAL`.

Docling disables its OCR, picture descriptions, code/formula generation, chart-value extraction, external services, and external plugins. This path does not call a VLM/LLM to create prose, captions, or values. A configured GROBID server is an independent service; consolidation options are zero to disable automatic metadata enrichment.

Native references and TEI references are retained as candidates. Supplied TEI takes precedence over a configured GROBID request. A regex DOI is `doi_candidate`, not a verified link. Repository registration accepts `matched_work_id` only with verified exact DOI/OpenAlex/PMID/PMCID evidence. Title similarity does not establish a reference link.

### Bounded fallback between whole-document attempts

Fallback is controlled by [`recovery_options()`](../src/openalex_corpus/quality/recovery.py), after quality assessment and finalization of the preceding attempt. Every fallback gets its own worker and immutable run. It does not modify the prior run.

```text
attempt quality is COMPLETE or COMPLETE_WITH_WARNINGS? -- yes --> stop
  | no
attempt index >= max_recovery_attempts? ---------------- yes --> stop
  | no
PAGE_PARSE_FAILED / MISSING_REGIONS / MARKDOWN_EMPTY present
  AND enable_ocr already true? ------------------------- yes --> primary=pymupdf
  |                                                            Docling disabled
  |                                                            OCR remains enabled
  no
enable_docling true? ----------------------------------- yes --> primary=pymupdf
  |                                                            Docling disabled
  no
no alternate options -> stop
```

The policy does not turn on OCR when it was disabled by the user. The first branch may repeat native-plus-OCR options for the remaining bounded attempts when the same page error persists. `max_recovery_attempts=2` permits the initial attempt plus at most two recovery attempts; it does not promise three attempts if no policy branch applies.

## 7. Quality decisions and status scopes

### Stage 5: deterministic quality precedence

[`check_extraction()`](../src/openalex_corpus/quality/checker.py) receives the result, the validated source page count, and the run directory. It checks actual page numbers rather than only comparing a page count.

```text
result.status == FAILED? ------------------------- yes --> FAILED
  | no
any error-severity issue OR result.status=PARTIAL? - yes --> PARTIAL
  | no
any issue OR result.status=COMPLETE_WITH_WARNINGS? - yes --> COMPLETE_WITH_WARNINGS
  | no
  +-----------------------------------------------------> COMPLETE

Errors include: missing/duplicate/unexpected page numbers; failed/partial/OCR-needed
pages; missing regions; empty Markdown; unsafe/missing local Markdown image;
reported parser errors.

Warnings include: short Markdown; replacement characters; repeated long lines;
remote image references; unverified equations/tables; parser warnings;
non-Docling structural fallback.
```

`SHORT_MARKDOWN` applies when native page character counts exceed 300 and Markdown length is below 25% of that evidence. Repeated-line detection considers stripped lines longer than 70 characters and warns when a line occurs more than five times. These are conservative signals for inspection, not semantic equivalence tests.

The checker flags a local Markdown image when its resolved target escapes the run or is not a file. Remote/data image references produce a warning. `verify_manifest()` separately validates listed paths, sizes, and SHA-256 values.

Equation uncertainty has two layers: the checker reports `EQUATION_UNVERIFIED` as a warning, but the coordinator already marks any equation-containing result `PARTIAL`. A clean warning list cannot override that parser status. `no_detected_failures` means no implemented detector fired; it does not prove that an element was absent or complete.

### Different records answer different operational questions

| Record or response | Question answered | Example |
|---|---|---|
| `crawl_runs.status` and checkpoint | How far did this query invocation/crawl get? | `LIMIT_REACHED` with offset 30 is resumable |
| Latest `process_work()` return | What happened during this operation? | Download refresh returns `PENDING_RETRY` |
| `download_jobs` / `parse_jobs` | What is the latest stage job state? | A low-quality reparse leaves parse job `FAILED` |
| `extraction_runs.status` | What quality did this immutable attempt achieve? | New run `PARTIAL`, older run `COMPLETE` |
| `current.json` | Which extraction is selected? | Still points to the older `COMPLETE` run |
| `works.status` | What state is projected for the Work? | Remains `COMPLETE` when selection is preserved |
| `retry_queue` | What stage is scheduled for an explicit later retry? | Parse row `PENDING`, with time and consumed attempts |

For a new Work, metadata-only collection produces `PARTIAL` with `pdf_not_requested`; PDF-only collection produces `PARTIAL` with `markdown_not_requested`. Those are intentional stopping points, not necessarily parser failures.

`PDF_UNAVAILABLE` means no usable PDF was obtained through the current candidates and policy. `PENDING_RETRY` signals a retryable acquisition/processing condition. Neither asserts whether a PDF exists elsewhere. A successful previous selection can coexist with a failed latest job and another queued attempt.

## 8. Immutable runs and publication order

### Stages 6 and 7: separate finalization, registration, and selection

`parse()` validates the PDF and computes its hash before parsing. It validates an existing selection before considering reuse. With `force=False`, a valid selected `COMPLETE` or `COMPLETE_WITH_WARNINGS` run for the same PDF hash is returned directly. A `PARTIAL` selection is not reused by this shortcut.

```text
Pipeline.parse(wid, pdf)
  validate PDF -> compute digest -> validate prior selection if present
  reuse eligible? -- yes --> return reused=True
    | no
    set parse job RUNNING
    for each configured/recovery attempt:
      A. mkdir extractions/.staging-<timestamp>-<uuid>/
      B. atomic intent.json {work_id, pdf_path, pdf_sha256, started_at, options}
      C. run worker -> compare OpenAlex/TEI title if both supplied -> quality check
      D. on worker/processing exception: record error; synthesize FAILED result
      E. add run_id/pdf_path/pdf_sha256 to result
      F. write paper.md with metadata front matter + extracted body
      G. write quality_report.json and result.json
      H. write document/pages/references JSON only if worker did not write them
      I. enumerate current files -> collect relative path, size, byte SHA
      J. write manifest.json {work, quality status, PDF hash/path, files, options}
      K. os.replace(staging, final run directory)
      L. record_extraction() transaction: run + document + elements + quality rows
      M. upsert_artifact(extraction_manifest) transaction
      N. transaction registering all listed extraction_file artifacts
      O. update best attempt; decide whether another fallback is applicable
    compare best attempt rank with prior selected rank
      lower -> retain previous selection; restore its Work status
      equal or higher -> _publish():
        P. _validate_run(final, manifest)
        Q. atomic replace current.json                 [selection commit]
        R. update convenience symlinks                 [repairable projection]
        S. set works.status + missing_reason           [repairable projection]
    set parse job COMPLETED for successful best attempt, otherwise FAILED
    complete parse/reconcile retries on success; otherwise enqueue QUALITY_FAILED
    return best attempt summary, possibly with previous_result_preserved
```

The manifest file is written after enumerating run files, so it does not list its own hash. Its hash is registered separately as an `extraction_manifest` artifact. File enumeration includes logs, intent, worker output, adapter evidence, and final output files present at that point.

`record_extraction()` is idempotent only when an existing run ID has the same Work, output directory, serialized result, and serialized quality. It rejects a reused run ID with different contents. Run rows, document rows, elements, and validation rows are inserted within that method's one transaction; artifact registration follows in separate transactions.

`_validate_run()` checks that the run resolves inside the Work directory, any registered manifest hash still matches, every listed artifact has its expected size/hash, and the source PDF matches `pdf_sha256`. If a registered first `original_pdf` exists, its integrity is also checked. A valid newer source does not silently excuse corruption of that registered original.

`current.json` contains the relative run path, selected status, source PDF hash, and selection time. It is the authoritative selection commit. Top-level `paper.md`, JSON files, and asset-directory links are convenience projections. If a name is occupied by a user-owned ordinary directory, publication logs a conflict and preserves that directory.

### Rank and tie behavior

```text
RANK value:     3                 2                   1               0
Status:        COMPLETE  >  COMPLETE_WITH_WARNINGS  >  PARTIAL  >  FAILED/PENDING_RETRY

Within one parse() call: replace best only for a strictly higher rank.
                        The first best-ranked attempt wins later ties.
Against current.json:  publish if best rank >= prior rank; equal can replace.
During reconcile():    highest valid rank wins; ties use greatest run name.
```

Run names begin with a UTC timestamp and a short UUID. Reconciliation's lexical tie-break is an implementation rule, not an assessment of manuscript recency. A higher-ranked extraction of an older PDF can remain selected over a lower-ranked extraction of a newer source. Inspect the pointer's PDF hash and download history to identify the selected manuscript.

The parser writes `document.json` before the parent adds run ID, PDF hash, and metadata comparisons to `result.json`. These files are allowed to differ. OpenAlex/TEI title disagreement retains both titles and adds a warning; it does not substitute one source's title into the other.

## 9. Interrupt windows, reconciliation, and retries

### Stage 8: what each interrupted boundary leaves behind

| Interrupted boundary | Evidence/state left behind | Implemented handling and practical limit |
|---|---|---|
| After API reservation, before request | Consumed journal reservation | No refund; next attempt reserves again |
| After saved page, before receipt | Page may lack receipt/hash | Decode retained page; use matching checkpoint hash if present |
| After Work processing, before batch checkpoint | Work state ahead of cursor/offset | Replay batch; keyed registration avoids duplicate identities, requests may repeat |
| After raw history, before metadata DB commit | History and possibly pending marker | Reconcile uses pending path first; otherwise available current/history fallback |
| After metadata DB commit, before current JSON | DB revision newer than convenience copies | Re-ingest current DB raw/pending revision and rewrite copies |
| During PDF stream | Partial bytes and possible validator receipt | Resume only under range contract; otherwise restart bytes |
| After PDF version rename, before download manifest | Content-addressed bytes may lack retrieval record | Reconcile does not reconstruct missing URL provenance; downloader may need another request |
| During worker/staging writes | `.staging-*`, intent, partial logs/evidence | Preserve directory and enqueue `INTERRUPTED_EXTRACTION`; never promote it |
| After final rename, before run registration | Complete run on disk without DB rows | Validate final run and idempotently register extraction |
| After run rows, before all artifact rows | Run/elements exist; artifact projection incomplete | Reconcile registers run and manifest; it does not rebuild every extraction-file artifact row |
| After pointer, before links/status | New authoritative selection, stale projections | Re-publish validated pointer/selected candidate to repair links/status |
| Job left `RUNNING` | Incomplete stage record | Change job to `PENDING_RETRY`, queue `INTERRUPTED_JOB` |

A final run's manifest continues to list its files even when individual artifact rows were interrupted. `verify()` checks the selected manifest. Reconciliation is therefore useful but is not a universal reconstruction of every possible DB row or missing provenance record.

### Reconciliation scan order

```text
for each papers/W* directory, sorted:
  recover metadata in this priority order:
    metadata_pending.json -> referenced history payload
    else existing DB Work -> stored raw payload
    else metadata_raw.json
    else newest history file by filesystem mtime
  |
  scan non-hidden extraction directories having manifest.json
    validate run + source -> read result/quality -> register run + manifest artifact
    run validation failure -> error record; skip candidate
    manifest/result/quality decoding or registration failure -> outer Work failure
  |
  if current.json exists -> validate and re-publish its selected run
    invalid pointer + no valid candidates -> Work-level reconciliation failure
    invalid pointer + candidates -> record error and continue
  |
  if valid candidates exist -> publish max(rank, run_name)
  |
  preserve every .staging-* -> enqueue parse retry
  inspect download_jobs and parse_jobs -> queue any abandoned RUNNING job

Outer Work failure -> record failure, set PARTIAL/reconciliation_failed, queue retry.
Continue to other Work directories; return recovered[] and failures[].
```

Reconciliation can select a different equal-ranked run because it evaluates all valid final directories. It may also reset a valid pointer to the highest-ranked historic candidate. Review its returned failures and subsequent retry results; a completed scan does not imply all Works are complete.

`verify()` follows a different purpose: verify every registered artifact's existence/hash, validate the selected manifest and input PDF hash, and rerun structural quality checks for the selected result. It also runs SQLite integrity and foreign-key checks. A failure marks the Work `PARTIAL` with `verification_failed` and queues parse recovery.

A passing SQLite integrity check only establishes database structural consistency. A passing manifest establishes agreement with recorded bytes. Neither proves that the original PDF was accurately interpreted or that the paper's claims are correct.

### Three independent retry layers

| Layer | Trigger | Bound and persisted state |
|---|---|---|
| HTTP retry | Eligible transport/429/5xx failures | Metadata default 4 attempts; PDF candidate default 3; every paid/API attempt reserves budget |
| Parser recovery | Non-success quality plus applicable option change | Initial attempt plus up to `max_recovery_attempts`; each attempt is a retained run |
| Explicit CLI retry | Due pending/failed/running queue item | `retry_max_attempts` default 3; one row per Work/stage; consumed attempts persist |

The backoff helper schedules `min(3600, 30 * 2**attempt)` seconds. HTTP retry uses its own jitter and server-delay policy. Re-enqueuing a Work/stage preserves the maximum consumed queue-attempt count; it does not reset the retry budget.

CLI `retry` reconciles first, selects a bounded set of queue rows, and increments
each attempt. For parse/reconcile jobs it selects an existing retained PDF;
`parse()` validates that file afterward. Invalid retained bytes fail that retry
without automatically switching to download. When no retained PDF exists, or
for other job stages, retry re-enters metadata/download/parse processing.
`--now` includes delayed pending/failed rows, but does not remove the
maximum-attempt bound or network limits.

For retry-queue completion, `PDF_UNAVAILABLE` is considered a terminal handled outcome alongside successful extraction statuses. It does not become a successful extraction. Remaining non-success outcomes are requeued or marked exhausted/failed according to the consumed count. The application has no continuous retry scheduler or distributed worker service.

## 10. Database relationships and complete table map

The schema is defined in [`001_initial.sql`](../src/openalex_corpus/database/migrations/001_initial.sql); repository initialization also creates `schema_migrations`. Current metadata projections and immutable extraction evidence have different lifetimes.

```text
works (one row per OpenAlex Work)
  +-- metadata_revisions (many immutable payload revisions)
  +-- work_authors ------> authors (nullable identity)
  +-- work_institutions -> institutions (nullable identity)
  +-- work_topics -------> topics (nullable identity)
  +-- identifiers
  +-- work_locations ----> sources (nullable identity)
  +-- work_references ---> references (source's linked Work IDs)
  +-- artifacts ---------> artifact_revisions
  +-- extraction_runs
  |     +-- documents (at most one per run; normal registration creates one)
  |           +-- pages
  |           +-- text_blocks
  |           +-- tables ------> table_cells
  |           +-- figures
  |           +-- equations
  |           +-- extracted_references
  +-- validation_results (also optionally scoped to an extraction run)
  +-- download_jobs / parse_jobs (one latest row per Work per stage)
  +-- retry_queue (one row per Work/stage)
  +-- errors (work_id may be null)
  +-- file_checksums (keyed by path)
  +-- crawl_work_ids <---- crawl_runs ----> crawl_checkpoints

Key: each child references its parent unless described as an optional association.
     Elements reference documents and carry page_number, not a pages foreign key.
```

| Table | Key or scope | Stored responsibility |
|---|---|---|
| `schema_migrations` | Migration version | Applied schema versions and times |
| `works` | Work ID | Current raw/normalized payload, query columns, status, current raw path |
| `metadata_revisions` | Work + raw payload hash unique | Immutable source/normalized revisions and history paths |
| `authors` | Supplied author ID | Current author identity payload, name, ORCID |
| `work_authors` | Work + author index | Ordered authorships, nullable author ID, corresponding status |
| `institutions` | Supplied institution ID | Current institution payload, ROR, country |
| `work_institutions` | Work + author/institution index | Institution occurrences within authorships |
| `sources` | Supplied source ID | Current source identity, name, ISSN-L |
| `topics` | Supplied topic ID | Current topic identity payload |
| `work_topics` | Work + topic index | Ordered topic occurrences and score |
| `identifiers` | Work + scheme + value + source | Source-backed identifier lookup |
| `work_locations` | Work + role + location index | PDF/landing URLs, OA/license/version, nullable source ID |
| `references` | Referenced source ID | OpenAlex linked-reference identities; SQL name is quoted |
| `work_references` | Work + reference index | Ordered OpenAlex reference relationships |
| `artifacts` | Work + kind + absolute path unique | Current registered byte hash and artifact metadata |
| `artifact_revisions` | Artifact ID + row ID | Recorded changes to artifact hash/metadata |
| `extraction_runs` | Immutable run ID | Attempt status, engine/versions, output directory, result/quality JSON |
| `documents` | Row ID; extraction run unique | PDF path/hash, page count, extraction status |
| `pages` | Document + page number unique | Geometry, counts, OCR/status, complete page payload |
| `text_blocks` | Document + block index | Page number, source text, box, provenance payload |
| `tables` | Document + table index | Page, caption, box, source/status, candidate payload |
| `table_cells` | Table ID + row ID | Row/column, spans, JSON value, detailed cell evidence |
| `figures` | Document + figure index | Page, image path, caption, box, candidate payload |
| `equations` | Document + equation index | Page, source text, nullable LaTeX, box/status, evidence |
| `extracted_references` | Document + reference index | Extracted text, DOI, conditional verified match, match evidence |
| `validation_results` | Work; optional run/page | Quality severity/code/message and original issue payload |
| `download_jobs` | Work ID | Latest download state, starts counted as attempts, error/payload |
| `parse_jobs` | Work ID | Latest parse state, starts counted as attempts, error/payload |
| `crawl_runs` | Crawl ID; indexed query hash | Query/options identity and run lifecycle |
| `crawl_checkpoints` | Crawl ID | Cursor, offset, processed occurrences, total, extras, state |
| `crawl_work_ids` | Crawl + Work ID | Unique membership and discovery time |
| `errors` | Row ID; optional Work | Stage/code/message/retryability, requested ID, attempt/time |
| `retry_queue` | Work + stage unique | Retry code, consumed attempts, next time, queue state |
| `file_checksums` | Absolute path | Registered hash, size, associated Work, registration/verification fields |

The migration contains 33 application tables; `schema_migrations` makes 34 tables in the repository schema. JSON payload columns retain fields beyond the fixed relational columns. Their presence does not make every JSON path independently query-indexed.

Only SQLite is implemented. A different connection string does not enable PostgreSQL. Metadata entity rows may be updated by later collection; old source context remains in revisions. A new parse inserts a new run/document and never updates the previous run's page or table rows.

## 11. File layout and artifact interpretation

```text
data/                                    configured data_dir; default shown here
  database/corpus.sqlite                 SQLite database (WAL files may accompany it)
  state/
    writer.lock                          corpus writer coordination
    network_budget.lock                  shared reservation coordination
    network_budget.json                  daily metadata + lifetime content accounting
  raw_responses/
    <payload-sha>.json                    source payload, including invalid Work payloads
    <payload-sha>.status.json             only when source normalization failed
  crawls/<crawl-id>/pages/
    <cursor-sha>.json                     complete received page
    <cursor-sha>.receipt.json             byte hash + retrieval/query/header evidence
  papers/W.../
    metadata_history/<payload-sha>.json   immutable source revisions
    metadata_pending.json                transient recovery marker when ingest unfinished
    metadata_raw.json / metadata.json     current raw and normalized convenience copies
    .downloads/<url-sha>.part             partial/rejected bytes
    .downloads/<url-sha>.json             resumable transfer receipt
    original.pdf                         first retained original; never refreshed in place
    pdf_versions/<pdf-sha>.pdf            validated version bytes
    download_manifest.json               accumulated download provenance/attempts
    download_report.json                 latest operation result
    current.json                         authoritative selected extraction pointer
    paper.md -> extractions/<run>/paper.md
    tables/ -> extractions/<run>/tables/  analogous optional links for other assets
    extractions/
      .staging-<run>/                     unfinished attempt, retained on interruption
      <run>/
        intent.json                      input/options/start intent
        parser_stdout.log / parser_stderr.log
        worker_result.json               normal worker handoff; error file on exception
        result.json / document.json      parent-enriched result / parser document evidence
        quality_report.json              structural/quality findings
        manifest.json                    selected status, PDF identity, all listed file hashes
        paper.md / paper_body.md          metadata front matter + body / parser body only
        pages.json / references.json / figures.json / equations.json
        pymupdf_raw.json / pdfplumber_raw.json
        docling_status.json / docling_items.json / optional Docling outputs
        source.tei.xml or grobid.tei.xml / tei_document.json
        page_images/ / tables/ / figures/ / equations/
        ocr/ / ocr_evidence/ / ocr_extraction.json
  exports/ / temporary/                  prepared auxiliary directories
```

This tree lists optional files; it is not a promise that every adapter ran. A newly ingested metadata-only Work need not have a PDF; metadata-only processing preserves existing PDFs. A parser may leave a status/error artifact without successful content. The run manifest and result identify the evidence actually retained.

Provenance may include URL, source license/version, retrieval time, byte hash, page number, bounding box, parser/method, original text, and validation messages. An absent caption, license, equation representation, or reference identifier remains unresolved unless a source supplies evidence.

Back up the database with `Repository.backup()` and SQLite's online backup API, followed by integrity checks. Copying only `corpus.sqlite` during WAL writes can omit committed data. Source files and extraction directories require their own backup. Models, model manifests, configuration, and dependency lock files are also needed for reproducibility.

## 12. Resource budgets and operating limits

### Effective defaults and their scopes

Model defaults come from [`config.py`](../src/openalex_corpus/config.py); the bundled [`config/config.yaml`](../config/config.yaml) overrides several. The table distinguishes them so library-only calls are not mistaken for the bundled CLI setup.

| Setting | Model default | Bundled YAML / enforcement scope |
|---|---:|---|
| `max_works` | 100 | Per invocation; accepted range 1..10000 |
| `api.requests_per_second` | 2 | Per client throttle; configured maximum 30 |
| `api.timeout` / `api.max_attempts` | 45 s / 4 | Per request / bounded metadata retry loop |
| `api.daily_request_budget` | 1000 | Per UTC day, shared `data_dir` journal |
| `api.daily_budget_usd` | 0.05 | Per UTC day, conservative local reservations |
| Estimated list/search request cost | 0.0001 / 0.001 USD | Local estimates; see [API](API.md) for dated pricing evidence |
| `download.concurrency` | 3 | Downloader slots and crawl batch upper bound |
| `download.timeout` / `max_attempts` | 60 s / 3 | Per download request / per candidate |
| `download.max_downloads` | 100 | Per downloader instance, after cache/no-candidate handling |
| `download.max_file_bytes` | 104857600 | 100 MiB, headers plus streamed bytes |
| Content request/cost caps | 0 / 0 USD | Cumulative shared journal; no daily reset |
| Estimated content cost | 0.01 USD | Reserved per eligible OpenAlex content attempt |
| API/download max retry wait | 30 s / 30 s | Longer server delay becomes deferred handling |
| `parsing.workers` | 1 | Independent parser-process slots |
| `parsing.worker_memory_mb` | 2048 | YAML: 3072; sampled worker + descendant RSS |
| `parsing.memory_limit_mb` | 4096 | Configuration check on workers times worker budget |
| `parsing.timeout_seconds` | 600 s | Parent's overall worker timeout |
| `parsing.docling_timeout` / threads | 300 s / 2 | Docling adapter limits |
| `parsing.ocr_timeout` | 300 s | OCR process group timeout; one OCR job |
| `parsing.grobid_timeout` | 120 s | YAML: 180 s; configured remote/local service request |
| `parsing.enable_ocr` | false | YAML: true; missing executable still records unavailable |
| `parsing.grobid_url` | null | YAML: localhost:8070; service must be supplied separately |
| `parsing.render_dpi` / text minimum | 110 / 20 chars | Render resolution / sparse-text recovery threshold |
| `parsing.allow_model_downloads` | false | YAML uses local `data/models/docling` artifacts |
| `parsing.max_recovery_attempts` | 2 | Additional bounded attempts, only when policy applies |
| `retry_max_attempts` | 3 | Persistent queue attempts; not reset by re-enqueue |

`workers * worker_memory_mb <= memory_limit_mb` is a configuration invariant. It does not reserve memory or bound parent-process, filesystem-cache, browser, or unrelated process usage. A separately running GROBID service is outside the parser descendant tree and its RSS budget.

### Journal accounting and cost boundaries

`BudgetJournal.transaction()` uses a separate file lock and atomic replacement. Metadata count/cost and reported block state reset when the UTC date changes. Content count/cost are initialized if absent and never reset implicitly. Unreadable journal JSON blocks network use.

Failed requests, retries, and reservations followed by a crash count against limits. Lower observed metadata costs are not refunded. Observed rate-limit costs above the estimate are added after the response. Rate headers can also record a server-imposed blocked-until time.

The default zero content caps prevent paid OpenAlex PDF requests while still allowing eligible ordinary OA URLs. These are local accounting controls for this data directory, not an account-wide spending cap. Another application or data directory has separate history; deleting the journal removes the basis for cumulative enforcement. A changed service price may only become visible after the response.

### Boundaries the implementation does not eliminate

- Saved API pages are received evidence, not a frozen snapshot of all OpenAlex. Whole-database Snapshot acquisition is not implemented by this crawler.
- All configured limits are finite, but they do not guarantee that every query finishes within one invocation or that every PDF can be obtained.
- Native libraries, model versions, OCR engines, platform differences, and source bytes affect output. Identical settings alone do not guarantee byte-identical extraction.
- Multi-column order, complex tables, scanned text, vector isolation, equations, and reference segmentation can have undetected omissions or false positives.
- Candidate agreement checks do not prove mathematical correctness, source accuracy, or scientific validity. Human comparison with source regions remains necessary for uncertain material.
- Final directories, database rows, and convenience links have separate commit boundaries. Retained intent, source, result, and manifests enable recovery; missing/damaged evidence remains an explicit limit.
- A successful selected Work does not imply its latest refresh succeeded. Read latest jobs, individual run status, current pointer, and quality report according to their separate scopes.

See [TEST_MATRIX](TEST_MATRIX.md), [VALIDATION](VALIDATION.md), [ENHANCED_VALIDATION](ENHANCED_VALIDATION.md), and [DEVELOPMENT_LOG](DEVELOPMENT_LOG.md) for coverage and recorded execution evidence.
