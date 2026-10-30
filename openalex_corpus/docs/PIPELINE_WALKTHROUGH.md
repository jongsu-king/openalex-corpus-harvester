# Pipeline execution walkthrough

This guide follows one bounded harvest from CLI arguments to a durable cursor checkpoint.
It explains which state is authoritative, when writes occur, and what a rerun actually repeats.
All examples below are instructions or explicitly illustrative traces; they are not collection evidence.

The main implementation is in [cli.py](../src/openalex_corpus/cli.py),
[pipeline.py](../src/openalex_corpus/pipeline.py), and
[repository.py](../src/openalex_corpus/database/repository.py).
For the storage schema, see [DATABASE.md](DATABASE.md).

## 1. Start with a bounded command

Run from the repository's `openalex_corpus/` application directory with the package
installed in the active environment:

```sh
openalex-corpus --config config/config.yaml harvest \
  --query 'organic photovoltaics' \
  --search-scope title_abstract_keywords \
  --exact-search \
  --max-works 30
```

This command requests metadata only. To request PDFs and extracted Markdown, append:

```text
--download-pdf --parse-markdown
```

`--parse-markdown` also enables downloading, even without `--download-pdf`.
`--max-works 30` limits processed result occurrences in this invocation, not the API page size.
It does not promise 30 new unique works, 30 PDFs, or 30 successful extractions.
The API client always requests 100 results per page; an unused page tail stays on disk.
A request with no query or filter is rejected locally as an unbounded whole-database crawl.

## 2. Configuration, startup, and ownership

[config.py](../src/openalex_corpus/config.py) validates YAML with unknown fields forbidden.
The CLI uses explicit `--config`; otherwise it uses `config/config.yaml` if that file exists.
Without either, model defaults apply and the current directory is the path base.
For a YAML directly inside a directory named `config`, relative paths use its parent directory.
For other YAML locations, relative paths use the YAML's own directory.
This rule applies to `data_dir`, `log_dir`, and configured Docling/TEI paths.

`OPENALEX_CORPUS_DATA_DIR` and `OPENALEX_CORPUS_LOG_DIR` override the corresponding YAML values.
A relative environment override still uses the YAML-derived base directory.
The global CLI `--data-dir` override is applied afterward and resolves against the invocation directory.
The ordinary CLI reads `OPENALEX_API_KEY` from the process environment; it does not load an env file.
The OPV script has a separate, explicit `--env-file` option described below.

`Settings.prepare()` creates database, papers, exports, temporary, state, crawls, and log directories.
Logging writes structured events to `log_dir/corpus.jsonl` with rotation.
The CLI assembles search settings from YAML, then overrides only supplied search arguments.
Repeated `--filter field:value` expressions merge into configured filters by field name.
`--refresh-pdf` sets the download refresh flag for this invocation; its CLI default is false.

`run_job()` acquires `data_dir/state/writer.lock` with a zero-second lock timeout.
A second writer fails immediately; it does not wait for the existing harvest to finish.
Keep the lock file: lock ownership, rather than file existence, determines whether a writer is active.
The `Pipeline` opens SQLite with foreign keys, WAL, `synchronous=FULL`, and a 30-second busy timeout.
Missing numbered migrations are applied transactionally before processing begins.
Per-work async locks serialize repeated raw IDs inside one pipeline.
Downloader locks serialize the same paper directory, and parser slots limit worker processes.

The shipped general YAML uses download concurrency 3 and parser workers 1.
It enables OCR and configures local GROBID; bare model defaults disable OCR and omit GROBID.
The shipped OPV YAML uses concurrency 2, so its batch boundaries differ from the trace below.

## 3. Call flow and decisions

```text
CLI callback: load_settings -> overrides -> prepare -> configure_logging
  |
  v
harvest command: YAML search + CLI fields -> SearchSpec
  |
  v
run_job -> corpus_lock -> Pipeline -> asyncio.run(task)
  |
  v
Pipeline.harvest -> SearchSpec.params -> begin_crawl -> get_checkpoint
  |
  +-- checkpoint retry_at is in the future -> PAUSED, no page request
  |
  v
Choose cursor page path: crawls/<run>/pages/<sha256(cursor)>.json
  |
  +-- retained page -> verify known checksum -> load JSON
  |
  +-- missing page -> reserve budget -> throttle -> HTTP -> save page -> save receipt
  |
  v
Guard next_cursor; validate offset; take at most concurrency / remaining cap / page tail
  |
  v
asyncio.gather(process_work(...)) for the whole bounded batch
  |
  +-- each work -> per-work lock -> ingest immutable raw revision + metadata DB state
  |     |
  |     +-- metadata only -> return current status, metadata_only=true
  |     |
  |     +-- PDF requested -> download job RUNNING -> PDFDownloader.download
  |           |
  |           +-- no usable PDF -> report + error/job/retry state -> return outcome
  |           |
  |           +-- valid PDF -> version/original/report/artifacts -> download COMPLETED
  |                 |
  |                 +-- no parsing -> PARTIAL/markdown_not_requested, unless already successful
  |                 |
  |                 +-- parsing -> validate PDF -> reuse valid successful run, or:
  |                       parse slot -> spawn worker -> extraction -> quality gate
  |                       staging manifest -> rename -> DB rows -> select -> current.json
  |
  v
All batch outcomes returned -> record crawl work IDs -> advance offset -> checkpoint
  |
  +-- more retained results and capacity -> next batch
  +-- exhausted page + continuation -> next cursor, offset 0 -> checkpoint
  +-- terminal page -> coverage audit -> COMPLETED
  +-- invocation cap -> LIMIT_REACHED
  +-- exception -> PAUSED; preserve last acknowledged position
  |
  v
Emit JSON -> choose exit code -> release writer lock -> finally close repository
```

The batches are bounded `asyncio.gather` calls, not an independent persistent queue of crawl items.
All batch tasks finish before results are inspected in their original page order.
A slow parser therefore delays acknowledgment of the other works in that batch.
Metadata-only ingestion has no internal network await, although it uses the same batching code.

## 4. Query identity and resume selection

[SearchSpec.params()](../src/openalex_corpus/api/search.py) creates a normalized parameter mapping.
Filters are sorted by key, booleans become lowercase, DOI/Work identifiers are normalized,
and year bounds become a range. Query wording itself is retained; it is not semantically canonicalized.
Different whitespace, wording, scope, or exact-search mode can therefore define a different crawl.

For the command above, the parameter mapping is:

```json
{
  "per_page": 100,
  "search.title_abstract_keywords.exact": "organic photovoltaics"
}
```

The repository fingerprints this object together with the effective download and parse booleans:

```text
query identity = {
  params: <normalized mapping>,
  download_pdf: download_pdf OR parse_markdown,
  parse_markdown: parse_markdown
}
query_hash = SHA-256(sorted, compact, ensure_ascii=False JSON of query identity)
```

`max_works`, download concurrency, parser options, and refresh behavior are outside that identity.
Changing `--max-works` can continue the same crawl; adding parsing creates a different crawl identity.
`begin_crawl(resume=True)` selects the latest matching run that is not `COMPLETED` or `COMPLETE`.
Otherwise it creates a UUID run and checkpoint `cursor="*", offset=0, processed=0`.
`--fresh` forces this new crawl state without deleting existing works or extraction history.
An ordinary harvest after a completed crawl starts another crawl; it does not reuse completion.

Checkpoint `processed` counts acknowledged occurrences, while `crawl_work_ids` records distinct IDs.
The offset is the zero-based index of the next result within the retained current page.
The cursor selects that page; it is not necessarily the API's next page cursor yet.
Checkpoint extras retain page path/hash, visited cursors, retry time, and later coverage audit fields.
Extras are merged on update, so an older page path can remain when a new cursor is selected.
The reader only uses that checkpoint hash when the path matches the page being read.

## 5. Request reservation and retained page receipt

[client.py](../src/openalex_corpus/api/client.py) reserves every metadata HTTP attempt before sending it.
`state/network_budget.lock` protects the journal with an OS file lock across processes.
The journal update is flushed, fsynced, and replaced before throttling or HTTP begins.
Failed requests and crashes after reservation still count; reservations are not refunded.
Metadata request and dollar counters reset on the next journal transaction after UTC midnight.
Content download counters are lifetime counters for this data directory.

The configured search estimate applies to search parameters or a `.search:` filter.
Other list requests use the configured list estimate. Defaults are local estimates, not an invoice.
Rate headers are recorded after a response; a higher observed cost increases the reservation total.
Exhausted server allowance records a block time. Long server pauses become resumable `retry_at` values.
Transient transport failures, 429s, and eligible server errors have bounded retries and jitter.
Each retry requires another reservation. Authentication and malformed response errors are not retried.

`get_page()` requires a key for cursors after `"*"` and rejects caller-supplied `api_key` or `select`.
It validates result/meta shapes and count/next-cursor presence, and requires each work to have a
nonempty ID field. Canonical `W` plus digits validation occurs later during ingestion.
An accepted response is saved as parsed JSON, not as an exact HTTP wire-body capture.
Its receipt records serialized-file SHA-256, retrieval time, parameters, cursor, and rate headers.
The page filename hashes the requested cursor, not the response contents.
Retained pages avoid new API calls and new metadata reservations when resumed.

[pagination.py](../src/openalex_corpus/api/pagination.py) rejects invalid/repeated cursors and
empty pages that nevertheless advertise a continuation. Previously visited cursors are also checked.
An empty terminal page or a fully consumed page without continuation ends the crawl.
`offset > len(results)` pauses instead of silently skipping data.

## 6. Exact write order and commit boundaries

The following numbered sequence describes the successful path; optional branches are explicit.
It is a per-page/per-work ordering, not one transaction across HTTP, filesystem, and SQLite.
Different works in the same batch can interleave their optional download and parse writes.
`atomic_json`/`atomic_text` use a same-directory temporary file, fsync, replace, and directory fsync.
Downloader JSON and budget writes have their own helpers; do not assume one global commit primitive.

1. Create or resume `crawl_runs`; a new run also inserts `crawl_checkpoints`, in one DB transaction.
2. For an uncached page, reserve the metadata attempt in `network_budget.json` before HTTP.
3. Observe returned rate headers in that journal, then validate the returned API page.
4. Write `crawls/<run>/pages/<cursor-hash>.json` atomically.
5. Write its adjacent `<cursor-hash>.receipt.json` atomically, containing the file checksum.
6. For each work, preserve `raw_responses/<canonical-payload-hash>.json` if absent.
7. Normalize metadata and validate the `W` plus digits filesystem key.
8. Write immutable `papers/<WID>/metadata_history/<payload-hash>.json` if absent.
   Existing revisions must parse, equal the payload, and match a trusted artifact hash when present.
9. Write `metadata_pending.json` pointing to the intended immutable revision.
10. In one DB transaction, upsert `works`, insert the revision if new, and rebuild metadata relations.
    Existing work processing status is preserved by the metadata upsert.
11. Atomically replace the convenience files `metadata_raw.json`, then `metadata.json`.
12. Remove `metadata_pending.json`, then register the immutable raw revision as an artifact.
13. For metadata-only work, set reason `pdf_not_requested` only when status is already `PARTIAL`
    and its prior reason is empty. Preserve successful work status.
    Return the current work status; skip the following download/parse steps.
14. For requested PDFs, set the download job to `RUNNING` before calling the downloader.
15. For a network download, write a partial-download receipt before streaming `.downloads/*.part`.
    Reserve content budget before each protected OpenAlex content request, when applicable.
16. Validate the complete partial file and move valid bytes to `pdf_versions/<pdf-sha256>.pdf`.
17. If no original exists, copy via `original.pdf.part` and replace it as `original.pdf`.
18. Append retrieval/version evidence to `download_manifest.json`, then remove the partial receipt.
19. Back in the pipeline, write `download_report.json` for the returned result.
20. On success, register PDF artifacts, register the first original if needed, complete the download job,
    and complete existing download retries. On failure, write job/error/status/retry evidence instead.
21. Without parsing, set `PARTIAL/markdown_not_requested` unless a successful work status already exists.
22. With parsing, validate the source and any previous selected run. Reuse a valid successful run only
    when its PDF hash matches and parsing is not forced; otherwise mark the parse job `RUNNING`.
23. Create `extractions/.staging-<run>/` and write `intent.json` with input hash and parsing options.
24. The spawned worker writes parser evidence, logs, and `worker_result.json` or `worker_error.json`.
25. The parent evaluates quality and writes `paper.md`, `quality_report.json`, and `result.json`.
26. Write `document.json`, `pages.json`, and `references.json` only where the worker has not written them.
27. Hash and list staging files in `manifest.json`; the manifest does not include itself in its file list.
28. Rename the whole staging directory to `extractions/<run>/` using `os.replace`.
29. Transactionally register the extraction and relational children, then register its manifest artifact.
30. Register extraction-file artifacts in a DB transaction. A recovery attempt repeats steps 23-30.
31. Validate the chosen run and source hashes, then atomically replace `current.json` to promote it.
32. Update convenience symlinks individually, then update the selected work status.
33. Complete or fail the parse job; complete relevant retries on success or enqueue a quality retry.
34. After every gathered outcome returns, insert each work ID into `crawl_work_ids` with deduplication.
35. Increment offset and occurrence counters by the batch size, then save both checkpoint/run state.
36. At page exhaustion, checkpoint the next cursor at offset zero, or save the terminal coverage audit.
37. Finish the crawl as `LIMIT_REACHED` or `COMPLETED`, then emit the invocation result JSON.

Steps 34 and 35 are separate commits. A crash can leave crawl membership ahead of its checkpoint.
Replay is expected at that boundary; membership deduplication prevents duplicate unique-ID counts.
Malformed work normalization writes a `raw_responses/*.status.json` failure record and preserves raw data.
A metadata exception escapes `process_work`, pauses the batch, and prevents its offset acknowledgment.
Normal returned PDF/parse failure outcomes still count as processed occurrences and advance the checkpoint.

## 7. What the PDF and parser branches do

[pdf_downloader.py](../src/openalex_corpus/download/pdf_downloader.py) tries explicit candidates in order:
OpenAlex content PDF, best OA location, primary location, then OA entries in `locations`.
Duplicate URLs are removed. Non-HTTP URLs, restricted access, and disallowed licenses retain evidence.
No candidate yields `PDF_NOT_AVAILABLE`; it is different from an attempted but unsuccessful download.
A matching cached URL/version is reused only after PDF validation and hash agreement.
An imported original with no version manifest can be reused with unverified download provenance.

`max_downloads` is a downloader-instance cap on works entering candidate attempts, not HTTP requests.
Cached reuse and absence of candidates do not increment it. Candidate retries have a separate bound.
Protected content also needs an API key and positive content count/cost caps; defaults disable it.
The downloader can continue to other candidates after a protected-content failure.
Content lifetime limits remain enforced even when a later metadata day begins.
Range continuation requires a retained validator and validates content ranges and total length.
Rejected bytes remain `.part`; valid versions and the first original remain separately addressable.
`--refresh-pdf` bypasses cache reuse without replacing the first original or prior version history.

[parsers/__init__.py](../src/openalex_corpus/parsers/__init__.py) begins with PyMuPDF evidence,
optionally recovers sparse scanned pages with OCR, extracts independent tables with pdfplumber,
and requests Docling structure when configured and available. GROBID/TEI can supply further structure.
Disagreeing candidates and source regions remain available; the pipeline later decides publication.
See [PARSERS.md](PARSERS.md) for parser-specific artifacts and setup.

The parent spawns a disposable process after acquiring a parser slot.
It monitors elapsed time and the worker plus descendants' RSS approximately every 0.2 seconds.
Timeout or memory excess terminates the worker tree and produces a failed extraction attempt.
Native stdout/stderr go to that attempt's `parser_stdout.log` and `parser_stderr.log`.
This is monitored process isolation, not an OS-enforced memory reservation.

[checker.py](../src/openalex_corpus/quality/checker.py) checks page coverage, unresolved regions,
Markdown content, referenced local assets, parser errors, and uncertainty warnings.
Its quality status is stored separately from individual parser outputs.
[recovery.py](../src/openalex_corpus/quality/recovery.py) can select bounded fallback attempts.
`max_recovery_attempts` limits additional attempts; the policy can stop earlier when no change applies.
The rank is `FAILED/PENDING_RETRY < PARTIAL < COMPLETE_WITH_WARNINGS < COMPLETE`.
A lower-ranked new run cannot replace the previously validated selection; equal rank may replace it.
The returned attempt outcome can be unsuccessful while an earlier successful selection remains published.
Use `current.json` and the work row to distinguish the retained selection from the latest attempt.

## 8. Illustrative two-page, mid-page resume trace

Everything in this trace is schematic. `R-demo`, `C-page-2`, and `A001`/`B001` are invented labels,
not stored run IDs, real OpenAlex Work IDs, or cursors that can be sent to the API.
Assume two stable pages of 100 distinct works each, reported count 200, no failures, and concurrency 3.
Page 1 uses the real initial cursor marker `*`; page 2 uses the invented label `C-page-2`.
Each invocation repeats the same metadata-only command with `--max-works 30` and no `--fresh`.

```text
Page 1: [A001 ... A100], request cursor *,        next cursor C-page-2
Page 2: [B001 ... B100], request cursor C-page-2, next cursor null

Call  Start position    Acknowledged results           End position       Total  Result
----  ----------------  -----------------------------  -----------------  -----  -------------
  1   * / offset 0      A001-A030 (10 batches of 3)     * / offset 30         30  LIMIT_REACHED
  2   * / offset 30     A031-A060 (10 batches of 3)     * / offset 60         60  LIMIT_REACHED
  3   * / offset 60     A061-A090 (10 batches of 3)     * / offset 90         90  LIMIT_REACHED
  4   * / offset 90     A091-A100; B001-B020            C-page-2 / offset 20  120  LIMIT_REACHED
  5   C-page-2 / 20     B021-B050 (10 batches of 3)     C-page-2 / offset 50  150  LIMIT_REACHED
  6   C-page-2 / 50     B051-B080 (10 batches of 3)     C-page-2 / offset 80  180  LIMIT_REACHED
  7   C-page-2 / 80     B081-B100 (6 batches of 3 + 2)  C-page-2 / offset 100 200  COMPLETED
```

Call 1 requests page 1 once and retains all 100 results before processing its first 30.
Calls 2 and 3 read that retained page; they make no metadata HTTP requests.
Call 4 processes page 1 in batch sizes `3, 3, 3, 1`, reaching processed total 100.
It checkpoints `C-page-2 / offset 0`, requests and retains page 2, then has 20 invocation slots left.
It processes page 2 in sizes `3, 3, 3, 3, 3, 3, 2`, ending at offset 20 and total 120.
The remainder is two, because this invocation has already consumed ten works from page 1.
Call 7 ends immediately after its final 20 results; it need not fill its cap of 30.
Only two metadata requests occur in this success-only illustration; transport retries would add attempts.
Actual live pages can contain repeated IDs or changing counts, so the final coverage audit matters.

For a mid-batch crash variant, suppose call 2 durably checkpoints offset 33 after `A031-A033`.
It then writes metadata for `A034-A036` but crashes before the next checkpoint commit.
Rerunning starts from offset 33 and replays `A034-A036`; it does not start at A037.
Existing raw revisions are validated/reused, metadata relations are rebuilt, and crawl IDs deduplicate.
If PDFs were requested, validated cached PDFs and successful same-hash extractions can also be reused.
Partially completed parser attempts remain visible and may require reconciliation/retry.
The rerun gets a fresh invocation allowance of 30; it is not limited to the interrupted call's remainder.

## 9. Crash boundaries and recovery behavior

| Interruption boundary | Retained evidence and next action |
| --- | --- |
| Reservation before HTTP | The reservation remains consumed. Resume without resetting the journal. |
| HTTP returned before page write | No usable page is retained; resume can issue another reserved request. |
| Page written before receipt | The page is reused. A matching checkpoint hash may protect it, but absent both receipt and checkpoint hash there is no trusted checksum check for that page. Preserve it for review. |
| Raw response before normalization | Raw singleton/page evidence remains. No work row is required for this evidence to exist. Correct the malformed-input cause before resuming. |
| Revision/pending marker before DB commit | Reconcile ingests the pending immutable revision and rebuilds convenience files. |
| Metadata DB commit before aliases | Reconcile uses the pending marker or stored DB raw payload to restore aliases. |
| PDF streaming before validation | `.part` and receipt remain; a bounded retry can continue a validated range or restart. |
| Valid PDF before manifest/report | Bytes can survive without full provenance. Reconcile marks interrupted jobs; retry may redownload or reuse available evidence. |
| Parser worker before final rename | `.staging-*` remains unselected. Reconcile preserves it and enqueues parsing again. |
| Final rename before extraction DB commit | Reconcile validates the final run and registers it idempotently. |
| Extraction DB rows before promotion | The completed run remains a candidate. Reconcile can choose the highest-ranked valid candidate. |
| `current.json` before symlinks/status | The selection is committed. Reconcile validates it and repairs convenience links/status. |
| Per-work completion before batch checkpoint | The unacknowledged batch replays on resume. The checkpoint never assumes those writes imply acknowledgment. |
| Page-end checkpoint before next HTTP | Resume begins at the next cursor with offset zero. The previous page remains retained. |

`reconcile()` scans paper directories; ordinary harvest does not automatically invoke it.
It prefers pending metadata intent, then DB raw metadata, then aliases/history when reconstructing metadata.
It validates finished extraction directories, republishes valid selections, and considers all valid candidates.
Among candidate runs it selects by rank, then run name; this can change an equal-ranked selection.
It preserves abandoned staging directories and converts interrupted download/parse jobs to pending retries.
It does not repair or reset crawl pages, cursors, or budget journals.
Convenience links that conflict with an existing real directory are left in place and logged.
Read the selected run through `current.json` when such a conflict exists.

## 10. Interpret status and exit code together

| Layer | State | Meaning |
| --- | --- | --- |
| Crawl | `LIMIT_REACHED` | Invocation allowance consumed; rerun the same query to continue. |
| Crawl | `COMPLETED` | Terminal cursor reached; downstream PDF/parsing outcomes may still need work. |
| Crawl | `PAUSED` | Error, interruption, or enforced delay stopped progress; inspect error and retry time. |
| Work | `PARTIAL` | Metadata-only, PDF-only, or incomplete extraction; inspect `missing_reason` and quality. |
| Work | `PENDING_RETRY` | A transient operation or caught processing exception needs another attempt. |
| Work | `PDF_UNAVAILABLE` | No usable PDF from this attempt; inspect the download code to distinguish causes. |
| Work | `COMPLETE_WITH_WARNINGS` | Automated extraction gates passed with reviewable uncertainty. |
| Work | `COMPLETE` | Automated gates passed without reported warnings; inspect retained evidence as needed. |
| Attempt | `FAILED` | The attempted extraction failed; a previous good selection may remain. |

The ordinary CLI exits 0 for results not matched by its explicit failure predicates.
Exit 2 means a returned top-level `PAUSED`/`FAILED`/`PENDING_RETRY`, `valid=false`, reconciliation
failures, a nonempty `failures` value, or a child outcome of `FAILED`/`PENDING_RETRY`.
Consequently `PARTIAL`, `PDF_UNAVAILABLE`, and `LIMIT_REACHED` alone do not imply exit 2.
A `COMPLETED` crawl can still exit 2 because one returned work needs retry.
Exit 1 is an exception caught by `run_job`; exit 130 is its keyboard-interrupt handling.
Argument/configuration failures before `run_job` use CLI/framework error handling and may lack JSON.
Treat stdout JSON, work status, quality reports, and exit code as complementary signals.

## 11. Inspect local state and recover safely

The commands below use the example command's `data` directory and execute from `openalex_corpus/`.
Change the path consistently when using `--data-dir`, environment overrides, or the OPV configuration.
These inspection commands do not contact OpenAlex:

```sh
openalex-corpus --config config/config.yaml stats
sqlite3 -readonly data/database/corpus.sqlite <<'SQL'
.headers on
.mode column
SELECT r.id, r.status, c.cursor, c.offset, c.processed, c.total_count
FROM crawl_runs r JOIN crawl_checkpoints c ON c.run_id=r.id
ORDER BY r.started_at DESC LIMIT 5;
SELECT status, missing_reason, COUNT(*) AS n FROM works GROUP BY status, missing_reason;
SELECT id, work_id, stage, code, status, attempts, next_retry_at FROM retry_queue ORDER BY id;
SELECT stage, code, COUNT(*) AS n FROM errors GROUP BY stage, code;
PRAGMA integrity_check;
PRAGMA foreign_key_check;
SQL
rg --files data/crawls data/papers data/raw_responses | head -80
python -m json.tool data/state/network_budget.json
```

Inspect a retained page path or paper directory reported by those commands, substituting actual values.
`python -m json.tool PATH` reads JSON; `shasum -a 256 PATH` computes a file checksum on macOS.
Compare the page checksum with its receipt, not with the cursor hash in its filename.
For an extraction, inspect `current.json`, then the selected run's manifest and quality report.
Parser logs live inside individual run directories, including interrupted staging directories.

Before substantial recovery, retain the existing files and a consistent database backup:

```sh
openalex-corpus --config config/config.yaml backup
openalex-corpus --config config/config.yaml reconcile
openalex-corpus --config config/config.yaml verify --all
```

`backup` copies SQLite, not PDF/raw/extraction files; preserve those separately for a full corpus backup.
`reconcile` and `verify` write recovery/status state. `verify` may enqueue parsing repairs.
After inspecting their JSON, rerun the original harvest command to continue its metadata cursor.
For queued work, `retry --max-works 10` observes due times and retry limits; `--now` bypasses backoff.
`retry` first reconciles and may download and parse, including local GROBID requests if configured.
`reparse --openalex-id <actual-work-id>` forces a new extraction from a retained known PDF.
Use these processing commands only when that PDF/parser work is intended.
An API-budget pause with a future checkpoint `retry_at` remains paused until that time.
Do not delete the budget journal or edit checkpoint offsets to make a pause disappear.
For checksum failures, preserve the damaged bytes and reports, restore trusted backups when available,
and verify again before resuming; `--fresh` does not repair corrupted shared paper artifacts.

## 12. OPV inventory wrapper and shared limits

[scripts/harvest_opv.py](../scripts/harvest_opv.py) calls
[opv.run_inventory()](../src/openalex_corpus/opv.py) around repeated `Pipeline.harvest()` batches.
It requires an explicit query with `scope=title_abstract_keywords` and defaults to `config/opv.yaml`.
It loads only `OPENALEX_API_KEY` when `--env-file` is explicitly supplied, without shell execution.

```sh
python scripts/harvest_opv.py --max-works 300 --chunk-size 100
```

The wrapper holds one corpus writer lock, one pipeline, one API client, and one optional downloader.
Thus the downloader's in-memory `max_downloads` count spans all chunks in that invocation.
Metadata and content journal caps span chunks and separate invocations sharing the same data directory.
`--all` removes only the wrapper's invocation record cap; chunk size stays between 1 and 10,000.
It does not remove authentication requirements, budgets, downloader limits, or retry bounds.
An available seed page is accepted only with exact matching parameters, initial cursor, and checksum.
Seeding never replaces an advanced checkpoint; `--fresh` bypasses seeding.

`collection_report.json` is updated during processing; normal completion also writes a report under
`collection_runs/<execution-id>.json`. These report IDs are distinct from the reusable crawl run ID.
The wrapper fingerprint uses sorted compact JSON with the standard ASCII-escaping default;
the repository uses `ensure_ascii=False`, so their digest strings need not match for non-ASCII queries.
Both incorporate normalized parameters and the effective download/parse flags.

Unlike ordinary harvest, the wrapper can reuse an exhausted matching collection report after checking
that its referenced checkpoint is completed and its saved query object matches exactly.
That shortcut refreshes corpus/processing counts and integrity checks; it does not rerun failed PDFs.
`COMPLETE_QUERY_MATCHES` describes query exhaustion, while count disagreement yields
`COMPLETED_WITH_COUNT_MISMATCH_REVIEW`. Counts come from a live API, not a frozen snapshot.
Requested downstream failures can yield `QUERY_COMPLETE_PROCESSING_INCOMPLETE` after cursor exhaustion.
Only `PDF_UNAVAILABLE` with download code `PDF_NOT_AVAILABLE` is a benign PDF absence in that audit.
Other failed/unfinished download jobs, and non-successful work status when parsing is requested,
remain actionable. A database integrity failure takes precedence as `FAILED_DATABASE_INTEGRITY`.
The OPV script exits 2 when the report requires attention or database integrity fails, 1 on a caught
exception, and 130 on keyboard interruption; its report is the appropriate completion signal.
