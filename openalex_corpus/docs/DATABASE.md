# Database and provenance

This reference describes the implemented SQLite contract: which rows are current
projections, which retain history, and what requires filesystem evidence. The
schema is in `src/openalex_corpus/database/migrations/001_initial.sql`; write
behavior is in `database/repository.py` and `pipeline.py`. Metadata projection
and export behavior are in `metadata.py` and `export.py`.

## Connection and transaction contract

`Repository` opens a resolved filesystem path using Python's `sqlite3` driver.
Each connection enables foreign keys, WAL mode, `synchronous=FULL`, and a
30-second busy timeout. The CLI serializes corpus writers using
`data/state/writer.lock`; arbitrary external writers do not automatically obey
this application lock. Readers can query during a CLI collection. Use a read-only
SQLite connection for inspection.

Connections autocommit outside explicit transactions. `transaction()` starts
`BEGIN IMMEDIATE`; nested calls use savepoints. Exceptions roll back the current
transaction or savepoint. Operations are atomic together only when enclosed by
an explicit transaction. No transaction spans SQLite and the filesystem.

`schema_migrations(version, applied_at)` tracks numbered SQL files. Each unapplied
migration and its version record commit together. JSON values occupy TEXT
columns; there are no JSON-validity checks, immutability triggers, or cascading
deletes in the migration. Historical immutability is repository behavior, not
a prohibition against every possible direct SQL update.

PostgreSQL is **not implemented or tested**. SQLite-specific migrations, upserts,
row IDs, pragmas, backup, and connection management require a separate backend.
Changing a database URL does not enable PostgreSQL.

## Identity: do not interchange these keys

| Identity | Meaning and scope |
| --- | --- |
| `works.id` | Canonical short OpenAlex Work ID, such as `W123`; corpus identity |
| `works.doi` | Normalized optional DOI; indexed, deliberately not unique |
| `raw_sha256` | SHA-256 of canonical compact source JSON; revision identity within a work |
| Artifact `sha256` | SHA-256 of exact retained file bytes; formatting changes matter |
| `extraction_runs.id` | One result/quality pair for one work and output directory |
| `crawl_runs.id` | UUID identifying a crawl and its membership/checkpoint |
| `crawl_runs.query_hash` | Digest of canonical query JSON used to find a resumable crawl |
| Integer row IDs | Local relational keys, not OpenAlex or cross-database identities |

`work_id()` strips a URL prefix and trailing slash, uppercases the last component,
and requires `W` followed by digits. DOI normalization URL-decodes, trims, removes
`doi:` or a DOI resolver URL prefix, and lowercases. An empty normalized DOI is
null. Distinct Work IDs sharing a DOI remain distinct; ingest reports duplicate
candidates and never merges them automatically.

Repository canonical JSON uses sorted keys, compact separators, Unicode text,
and rejects NaN. The pipeline uses the payload digest to name
`metadata_history/<digest>.json`. Retained JSON files use formatted serialization,
so their byte checksums can differ from `raw_sha256` for the same JSON object.
A payload digest identifies source content; a file digest checks retained bytes.

The pipeline supplies timestamp-plus-random extraction run names. Direct
`record_extraction()` callers can supply `result.run_id`; otherwise the repository
derives a UUID5 from the resolved output directory. This is not a PDF checksum:
multiple parses of the same PDF can have distinct run IDs.

Crawl query identity includes API parameters and effective `download_pdf` and
`parse_markdown` flags. It excludes the per-invocation `max_works` limit. Changing
parameters, including page size, can change the query hash. The hash is indexed,
not unique; several finished or explicitly fresh crawls can share it.

## Relationships and ownership

Arrows mean child foreign key to parent key. Brackets identify child keys;
`?` means the foreign-key column permits SQL NULL.

```text
metadata_revisions [id; UNIQUE work_id,raw_sha256] ----> works [id]
identifiers [work_id,scheme,value,source] ------------> works
work_authors [work_id,author_index] -----------------> works, authors [id]?
work_institutions [work_id,author_index,institution_index]
                                                   -> works, institutions [id]?
work_topics [work_id,topic_index] -------------------> works, topics [id]?
work_locations [work_id,role,location_index] --------> works, sources [id]?
work_references [work_id,reference_index] -----------> works, "references" [id]

artifact_revisions [id] -> artifacts [id] -----------> works
file_checksums [path] ------------------------------> works?

documents [id; UNIQUE extraction_run_id] ------------> extraction_runs [id] -> works
    |                                                validation_results -> works
    |                                                validation_results -> run?
    +-- pages [id; UNIQUE document_id,page_number]
    +-- text_blocks [id]
    +-- tables [id] <-- table_cells [id]
    +-- figures [id]
    +-- equations [id]
    +-- extracted_references [id]
        (each element's document_id references documents.id)

download_jobs [work_id] ----------------------------> works?
parse_jobs [work_id] -------------------------------> works?
retry_queue [id; UNIQUE work_id,stage] --------------> works
errors [id] ---------------------------------------> works?
crawl_checkpoints [run_id] -------------------------> crawl_runs [id]?
crawl_work_ids [run_id,work_id] ---------------------> crawl_runs, works
```

`documents.work_id` also references `works`; no composite constraint checks that
it equals the parent extraction's Work ID. The repository supplies both
consistently. `work_institutions.author_index` is an association coordinate, not
an FK to `work_authors`. Element page numbers are not FKs to `pages`. These
relationships depend on application behavior rather than additional SQL keys.

SQLite permits NULL in a `TEXT PRIMARY KEY` of these ordinary rowid tables
unless the column also declares `NOT NULL`. The migration does not add that
declaration to the text primary keys in `works`, `authors`, `institutions`,
`sources`, `topics`, `references`, `extraction_runs`, `download_jobs`,
`parse_jobs`, `crawl_runs`, `crawl_checkpoints`, or `file_checksums`. Repository
methods supply non-null keys; direct SQL does not have the same protection.
The three nullable job/checkpoint foreign keys above reflect that SQL behavior.

## Current metadata and source history

`works` owns current metadata and the pipeline summary. Required fields are
`status`, `raw_json`, `metadata_json`, `raw_sha256`, `raw_path`, `paper_dir`,
`created_at`, and `updated_at`. DOI, title, publication year/date, and
`missing_reason` are nullable. The status CHECK allows exactly six values:

```text
COMPLETE             COMPLETE_WITH_WARNINGS       PARTIAL
FAILED               PDF_UNAVAILABLE              PENDING_RETRY
```

New rows default to `PARTIAL`. Metadata upsert replaces bibliographic columns,
JSON, digest, paths, and `updated_at`, preserving existing `created_at`, status,
and missing reason. Local timestamps are not publication or OpenAlex update
times; those source values remain in source JSON.

`metadata_revisions` requires work FK, raw digest, both complete JSON values,
raw path, and retrieval timestamp. `UNIQUE(work_id,raw_sha256)` and
`INSERT OR IGNORE` retain the first normalized representation and timestamp for
each distinct payload. Re-running normalization alone can update `works` without
creating a revision or updating an old revision's normalized JSON.

Normalization deep-copies source fields and adds canonical ID/DOI, abstract
reconstruction, and provenance. `doi_raw` retains the original DOI. `title` and
`display_name` remain independent; neither fills the other's missing value.
Unknown source fields survive in the complete raw payload.

`metadata_json.field_status` distinguishes absent keys (`source_field_absent`)
from source null (`source_null`). Empty strings, arrays, and false values remain
present values. Nested paths such as `authorships[0].author.orcid` and
`locations[0].license` have individual records. Scalar arrays remain source
fields without one diagnostic record per scalar.

Abstract text requires valid, unambiguous, contiguous positions starting at zero.
Conflicts, invalid positions, and gaps produce `parse_failed`, null text, issues,
and retained position candidates. No words are guessed. An empty valid index
produces an empty present string.

### Association rows and shared entities

Ingestion deletes and rebuilds the work's author, institution, topic, identifier,
location, and reference associations in the work/revision transaction. Their
order describes the current payload; reconstruct historical associations from
the complete raw revision.

| Table | Key and projection rule |
| --- | --- |
| `work_authors` | `(work_id, author_index)`; zero-based source authorship order |
| `work_institutions` | `(work_id, author_index, institution_index)`; all affiliations |
| `work_topics` | `(work_id, topic_index)`; source order, optional score |
| `work_locations` | `(work_id, role, location_index)`; independent role groups |
| `identifiers` | `(work_id, scheme, value, source)`; all four fields required |
| `work_references` | `(work_id, reference_index)`; required reference FK and source |

Author, institution, topic, and source IDs are nullable in association rows.
Missing IDs do not generate synthetic entity IDs; association payloads remain.
Shared `authors`, `institutions`, `sources`, and `topics` are upserted by ID, so
ingesting another work can update their names and projected fields. Association
payloads and work revisions preserve source-local historical context.

Location roles are `locations`, `primary_location`, and `best_oa_location`.
The latter two use index zero when provided. A location can intentionally appear
in several roles. Optional fields include PDF/landing URLs, license, version,
and OA flag. Identifiers use source `openalex`; the `(scheme,value)` lookup index
does not require an identifier value to belong to only one work.

`"references"` stores OpenAlex referenced IDs independently of `works`; targets
need not be collected. Rows are inserted if absent with source
`openalex.referenced_works`. Empty reference IDs are skipped, so reference indexes
can have gaps. Removing an association does not delete shared entities or targets.

## Extraction records and queryable evidence

`record_extraction()` registers a complete saved result in one transaction:

```text
extraction_runs -> one documents row
               -> pages and text/table/figure/equation/reference projections
               -> table_cells attached to table projections
               -> validation_results from quality issues
                  all commit together, or all roll back
```

An existing run ID is accepted only when work ID, resolved output directory,
canonical result JSON, and canonical quality JSON match exactly. Exact replay
returns without adding children; any difference raises an error. SQL does not
forbid different run IDs sharing an output directory; callers must preserve the
pipeline's directory convention.

`extraction_runs` requires status, parser-version JSON, output directory, start
time, result JSON, and quality JSON. Engine and completion time are nullable in
SQL; registration supplies the current completion timestamp. Status comes from
quality, then result, then `PARTIAL`. It has no SQL enum CHECK. Parser versions
can be an empty JSON object.

`documents.extraction_run_id` is required and unique, allowing at most one
document per run; registration creates one even with no pages. `page_count` is
the saved pages-list length. Optional PDF path/hash describe the parse input.
Document payload is `result.document` or `{}`; the complete extraction result
is in `extraction_runs.result_json`.

`pages` requires document, page number, status, and full payload. Its unique
`(document_id,page_number)` rejects duplicate page numbers. Dimensions, counts,
and OCR indicator are nullable. The schema neither sets a numbering base nor
requires consecutive pages. Missing regions and validation messages remain in
page payload JSON.

Text blocks, tables, figures, equations, and extracted references require a
document FK and zero-based index assigned from their result list. Element indexes
have no SQL uniqueness constraint. Page, bbox, source, caption, and applicable
text/path fields may be null. Full payloads retain extra details; a missing bbox
is serialized as JSON `null` in `bbox_json`.

Cells reference `tables.id`; row/column indexes and spans are nullable. Explicit
cell dictionaries are retained. If cells are absent, row arrays generate cells
with spans of one. Required `value_json` may contain JSON `null`, distinct from
SQL NULL. No neighboring values are copied to fill missing cells.

Extracted references are separate from OpenAlex `work_references`. Candidates
remain in their payload. `matched_work_id` is populated only when supplied
evidence has `verified: true` and method `exact_doi`, `exact_openalex_id`,
`exact_pmid`, or `exact_pmcid`. Registration checks that evidence label, not a
remote resolver. This column has no FK to `works`; targets may be uncollected.

`validation_results` requires work ID, code, payload, and creation time. Run ID,
page, severity, and message are nullable. Registration supplies its run ID and
uses `quality.issues`, falling back to `quality.validation_results`. Non-object
issues become `validation_message` rows. These are per-run evidence, not the
current work summary.

## File registration and commit boundaries

`artifacts` is unique on `(work_id,kind,path)` with required resolved absolute
path, metadata JSON, and timestamps, plus optional SHA-256. Different paths or
kinds stay separate even with identical bytes. PDF versions at distinct paths
therefore retain separate records.

`upsert_artifact()` atomically updates the artifact, appends an
`artifact_revisions` row when hash or metadata differs from its last revision,
and upserts `file_checksums` when given a hash. Identical registration adds no
revision. Returning to an older hash can add one because comparison uses only
the most recent registration.

`file_checksums.path` is its PK, independent of artifact kind. Work FK and size
are nullable; hash, timestamp, and status are required. Registration sets status
`REGISTERED` and reads size if the file exists. It does not recompute the supplied
hash. A missing file can receive a registration with null size. There is no
artifact FK; registered hashes alone do not prove current file integrity.

```text
Metadata:
  write raw response, immutable revision, metadata_pending.json     [filesystem]
  upsert work + revision + replace associations                    [SQL commit A]
  write current metadata files; remove pending intent              [filesystem]
  register raw artifact + revision + checksum                      [SQL commit B]

Extraction:
  write staging intent, result, quality, files, manifest            [filesystem]
  rename .staging-<run> to final run directory                      [filesystem]
  register run + document + elements + validation                  [SQL commit A]
  register manifest artifact                                      [SQL commit B]
  register manifest-listed extraction_file artifacts               [SQL commit C]
  publish current.json and links; update work status                [separate steps]
```

Extraction file registrations share an outer transaction; artifact calls use
savepoints. Crashes between commits can leave files without all registrations.
`current.json` is the authoritative filesystem selection commit; convenience
links and `works.status` can lag it. The manifest lists supporting files; its own
checksum is registered separately because it is written after that list.

## Selected result, latest job, and statistics

There is no `works.selected_run_id`. Selection lives in the paper directory's
`current.json`. Parsing validates a prior selection and preserves it when a new
attempt has lower rank: `COMPLETE` = 3, `COMPLETE_WITH_WARNINGS` = 2, `PARTIAL` = 1,
failed/retry states = 0. A failed latest parse job can coexist with a retained
successful result.

`download_jobs` and `parse_jobs` each have one row per work: latest status,
attempt count, optional error code, update time, and extra payload JSON. They are
mutable summaries, not histories. `set_job()` normally adds one attempt when
entering `RUNNING`, preserving the count for other statuses. Extraction runs and
`errors` provide historical evidence.

`stats()` selects the newest run at the highest achieved quality rank per work,
breaking ties by completion time then SQLite row ID. This mirrors retention but
does not read `current.json` or verify files. Select an explicit run for
reproducible SQL analysis.

The six work-status counts sum to collected Work IDs. `complete_rate` uses all
collected works, including metadata-only works. Download success uses all works
with download jobs, including unavailable PDFs; numerator, denominator,
percentage, and definitions are reported. Missing source PDFs are separate from
network/file-validation failures.

Element totals count candidates, not independently verified semantic objects.
`candidate_groups.tables` groups overlapping parser-assigned candidates;
`extracted.tables` counts candidate rows. `figure_kinds` separates `image_object`
from `figure_region`, which can depict the same content. Unpainted image resources
remain separately in extraction JSON.

## Crawl membership and checkpoints

`crawl_runs` owns query JSON/hash and lifecycle timestamps. Resume chooses the
newest match whose status is neither `COMPLETED` nor `COMPLETE`. `PAUSED` and
`LIMIT_REACHED` can resume; finished crawls start a new run.
`crawl_checkpoints.run_id` is both PK and FK, giving one checkpoint per run.

Checkpoints store cursor, nonnegative page offset/processed count, nullable API
total, status, extras JSON, and update time. Extras merge on update and can retain
page path/hash, visited cursors, retry time, and coverage audit. Cursor identifies
a retained page; offset counts consumed entries inside it, not all query results.

```text
retained page at cursor C: [entry 0][entry 1][entry 2][entry 3]
checkpoint offset = 2:       committed prefix     | resume here
processed = all consumed entries across the crawl, including duplicates
crawl_work_ids = unique (run_id, work_id) memberships
```

Processing and membership inserts precede the batch checkpoint, without one
enclosing SQL transaction. A crash can replay an already committed prefix.
Unique keys prevent duplicate identities/revisions/memberships, and validated
successful parses can be reused. Resumption does not promise exactly-once work.

Membership uses `INSERT OR IGNORE`, retaining the first discovery timestamp per
run/work pair. A work can belong to several crawls. Checkpoint `processed` counts
consumed entries; `stats().discovered_occurrences` counts unique run/work pairs.
API totals overlap and must not be summed as unique corpus size. Coverage audits
compare unique membership with reported totals; live API changes can explain
mismatches without proving data loss.

## Error history and retry eligibility

`errors` retains stage, code, message, retryability, attempt, and time.
`requested_work_id` preserves the supplied ID; nullable `work_id` is set only if
a canonical collected work exists. Unknown IDs can therefore be recorded without
FK failure. Error text redacts the configured API key and recognizable
key/bearer patterns.

`retry_queue` requires an existing work and has one row per `(work_id,stage)`.
Re-enqueueing updates cause/time, sets `PENDING`, and preserves the greater old/new
attempt count. Enqueueing attempt zero cannot reset its budget. Attempts have a
nonnegative SQL CHECK; the caller enforces the maximum, not the schema.

`pending_retries()` returns due rows below `max_attempts`, ordered by ID. Normally
it includes `PENDING`, `FAILED`, and `RUNNING`; `failed_only=True` includes
`PENDING` and `FAILED`. A null next retry time is immediately eligible. Selection
does not claim work. `update_retry()` changes status, optional attempts, next time,
and redacted last error. Job attempts, retry attempts, and parse runs count
different events and must not be treated as interchangeable.

## Read-only SQL examples

Open the configured database, normally `data/database/corpus.sqlite`, with
`sqlite3 -readonly data/database/corpus.sqlite`. Replace illustrative IDs below.
These queries do not merge identities or rewrite records.

```sql
-- Current states and DOI review candidates.
SELECT status, COUNT(*) AS works FROM works GROUP BY status ORDER BY status;
SELECT doi, COUNT(*) AS work_count, GROUP_CONCAT(id) AS work_ids
FROM works WHERE doi IS NOT NULL GROUP BY doi HAVING COUNT(*) > 1;

-- Current author/affiliation order, including missing entity IDs.
SELECT wa.author_index, wa.author_id, a.name,
       wi.institution_index, wi.institution_id, i.name AS institution
FROM work_authors wa
LEFT JOIN authors a ON a.id = wa.author_id
LEFT JOIN work_institutions wi
  ON wi.work_id = wa.work_id AND wi.author_index = wa.author_index
LEFT JOIN institutions i ON i.id = wi.institution_id
WHERE wa.work_id = 'W123'
ORDER BY wa.author_index, wi.institution_index;

-- Complete source history, including fields removed by later revisions.
SELECT retrieved_at, raw_sha256, raw_path, raw_json
FROM metadata_revisions WHERE work_id = 'W123' ORDER BY id;

-- Inspect attempts; explicitly choose one for page-level analysis.
SELECT id, status, completed_at, output_dir
FROM extraction_runs WHERE work_id = 'W123'
ORDER BY completed_at DESC, rowid DESC;
SELECT p.page_number, p.status,
       json_extract(p.payload_json, '$.missing_regions') AS missing_regions
FROM documents d JOIN pages p ON p.document_id = d.id
WHERE d.extraction_run_id = 'REPLACE_WITH_RUN_ID' ORDER BY p.page_number;

-- Retain reference targets that have no collected work row.
SELECT wr.reference_index, wr.reference_id, w.title AS collected_title
FROM work_references wr LEFT JOIN works w ON w.id = wr.reference_id
WHERE wr.work_id = 'W123' ORDER BY wr.reference_index;

-- Consumed occurrences, API totals, and unique memberships differ.
SELECT c.run_id, c.status, c.processed, c.total_count,
       (SELECT COUNT(*) FROM crawl_work_ids m WHERE m.run_id = c.run_id) AS unique_ids
FROM crawl_checkpoints c ORDER BY c.updated_at DESC;

-- Eligibility also depends on the configured attempt limit.
SELECT work_id, stage, code, attempts, next_retry_at, status
FROM retry_queue ORDER BY updated_at DESC;
SELECT requested_work_id, stage, code, message
FROM errors WHERE work_id IS NULL ORDER BY id DESC LIMIT 20;
```

## Backups, exports, and reconciliation limits

`Repository.backup()` uses SQLite's online backup API, checks destination SQLite
integrity, and atomically renames a temporary file. It rejects the live database
path and existing destinations. Do not copy only the live `.sqlite` during WAL
writes. Database consistency does not synchronize a filesystem snapshot or copy
PDFs, raw JSON, or extraction files.

Back up files separately, including `papers`, `crawls`, `raw_responses`, and
recovery state. Stop writers for a coordinated database/file backup. Paths are
frequently absolute; restoring to a new root is not automatic path migration.
`reconcile` and `verify --all` help assess restoration but cannot recreate bytes
that were never backed up.

Reconciliation can replay pending metadata, register valid finalized runs and
manifests, select candidates, rebuild links, and enqueue interrupted staging work
or jobs. It preserves staging evidence and rejects changed trusted hashes. It
does not recreate every historical revision or `extraction_file` registration,
repair corrupt PDFs, or make file/database commits atomic. Candidate selection
uses quality rank and run name, not statistics' SQL tie-breaking operation.

`verify` checks registered files/checksums plus selected manifest/PDF/quality
evidence. It can mark works `PARTIAL` and enqueue retries, so it is not read-only.
`integrity_check()` runs SQLite integrity and foreign-key checks. Neither that
check nor checksum agreement proves exact PDF reconstruction or source claims.

SQLite export delegates to online backup. JSON/JSONL contain current work metadata
and raw payloads; CSV/Parquet encode nested fields as JSON strings. These formats
do not include all revision/job/crawl/extraction tables. Markdown export follows
`current.json`, validates the manifest, and copies its run and supporting files
into a new destination; it is not an all-history backup. Exports reject overlap
with authoritative paths; in-corpus destinations belong under `data/exports`.
