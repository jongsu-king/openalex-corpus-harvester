# Database and provenance

SQLite is the implemented storage backend. Python's `sqlite3` driver enables
foreign keys, WAL mode, a 30-second busy timeout, and `synchronous=FULL` on every
connection. The CLI serializes corpus writers with a filesystem lock. Repository
transactions use `BEGIN IMMEDIATE`; nested transactions use savepoints. Database
files may be read while a CLI collection is running.

PostgreSQL is **not implemented or tested**. The entity model can be ported, but
SQLite-specific migrations, upserts, backup, row IDs, pragmas, and connection
management require a separate backend. Changing a database URL does not enable it.

## Identity and raw history

`works.id` is the short OpenAlex Work ID (`W123…`). DOI normalization removes the
DOI URL/prefix and lowercases it; DOI is indexed but deliberately **not unique**.
Distinct Work IDs with the same DOI are retained and reported as duplicate
candidates. They are never automatically merged.

Each work stores the current complete `raw_json`, normalized `metadata_json`, raw
payload SHA-256, immutable raw-file path, paper directory, status, and timestamps.
`metadata_revisions` stores complete raw and normalized JSON for every distinct
source payload. Unknown source fields survive in raw JSON. A source correction
updates the current projection without deleting prior revisions. The metadata
payload digest is over canonical compact JSON; file checksums hash the exact file
bytes and can therefore differ from that payload digest.

Normalization copies source fields and adds provenance/field status. Empty
arrays, empty strings, and null remain distinguishable. Abstract words are joined
only when their inverted index has valid, unambiguous, contiguous positions.
Ambiguous indexes retain position candidates and `parse_failed`; no missing
tokens are guessed. `title` and `display_name` remain separate source fields.
Nested paths such as `authorships[0].author.orcid` and `locations[0].license` have
their own source and missing-state records. Scalar arrays are kept as a source
field rather than expanded into one diagnostic record per token position.

## Tables

| Group | Tables | Behavior |
| --- | --- | --- |
| Work identity/history | `works`, `metadata_revisions`, `identifiers` | Current projection plus immutable original payload history |
| Authors/affiliations | `authors`, `work_authors`, `institutions`, `work_institutions` | Zero-based source authorship order; every institution association retained |
| Venues/locations | `sources`, `work_locations` | Source IDs, all locations, primary/best-OA roles, license and manuscript version |
| Subject/reference links | `topics`, `work_topics`, `references`, `work_references` | Ordered source topics/references; referenced works need not already be collected |
| Files | `artifacts`, `artifact_revisions`, `file_checksums` | Distinct paths and PDF versions stay separate; hash registration is not proof of current file integrity |
| Parsing | `extraction_runs`, `documents`, `pages`, `text_blocks`, `tables`, `table_cells`, `figures`, `equations`, `extracted_references` | Immutable extraction attempts, complete payload JSON, queryable page/element projections |
| Validation | `validation_results` | Per-run issues with source page, severity, code, message, and full payload |
| Work queues | `download_jobs`, `parse_jobs`, `retry_queue`, `errors` | Latest job state, bounded retry count, structured error history |
| Crawling | `crawl_runs`, `crawl_checkpoints`, `crawl_work_ids` | Query hash, cursor, offset inside retained API page, counts, unique IDs per run |
| Schema | `schema_migrations` | Ordered numbered SQL migrations applied transactionally |

Missing author or institution IDs stay null in association rows; synthetic
OpenAlex IDs are never generated. Entity rows are current projections, while
their historical context survives in the corresponding raw work revision.

All parser elements have a `payload_json` column preserving details not covered
by fixed relational columns. Table null cells are stored as JSON null and never
copied from neighboring cells. Extracted references may contain candidate links
in their raw payload, but `matched_work_id` is populated only for explicit
verified evidence using exact DOI, OpenAlex ID, PMID, or PMCID matching.

## Crash recovery and backups

A crawl checkpoint names both the current cursor and the number of durably
processed entries in that retained page. Replaying an uncommitted batch is safe
because Work IDs, association rows, revisions, and crawl memberships have unique
constraints. A resumed crawl is matched by its canonical query, not the number
of works requested in the latest invocation. Completed crawls start a new run.

An extraction run ID identifies one work and one output directory. Replaying
the same saved result is idempotent. Reusing that ID for a different work,
directory, result, or quality report raises an error; previous output is never
silently replaced. Filesystem promotion and SQLite commit cannot be one atomic
transaction, so the pipeline retains immutable intent/result/manifest evidence
and reconciles interrupted operations.

`retry_queue` has one row per work and stage. Re-enqueueing preserves the highest
attempt count, so automatic recovery does not reset its own budget. Errors may
reference an unknown/uncollected Work ID using `requested_work_id`; the foreign
key stays null until a real work exists. API keys are redacted from error text.

`Repository.backup()` uses SQLite's online backup API, checks the destination's
integrity, and atomically renames a temporary file. It refuses to overwrite a
previous backup or the live database. Do not copy only the live `.sqlite` file
while WAL writes are in progress; use the backup command. File artifacts require
their own filesystem backup in addition to the database backup.

## Statistics and validation boundaries

Every work has exactly one of the six pipeline statuses. `stats` verifies their
sum against the collected Work ID count. Search-result counts are per query and
are not summed into a fictitious unique corpus size. DOI collisions are counted
separately from repeat crawl memberships.

The `COMPLETE` percentage uses all collected works as denominator. Download
success uses all works with a download job, including unavailable PDFs; the
output includes numerator, denominator, percentage, and definitions. A missing
source PDF is counted separately from a failed network/file validation.

Element totals use the newest extraction at the highest achieved quality rank
per work, matching the pipeline rule that a failed reparse must not displace a
better retained result. Multiple parser candidates can overlap; these counts
are extraction records, not an independently verified count of semantic figures
or tables in the source. `verify` checks actual retained files and checksums;
database rows alone cannot prove that an artifact still exists.

`candidate_groups.tables` reports parser-assigned overlapping candidate groups;
the grouping is based on coordinates and is not a verified physical table count.
`extracted.tables` reports all retained parser candidates. `figure_kinds`
separates `image_object` from `figure_region`. An embedded image object and a
Docling figure region may represent the same content, so their sum is not the
number of semantic figures. Unpainted image resources remain in extraction JSON
separately from displayed figure records.

`integrity_check()` runs both SQLite integrity and foreign-key checks. Neither
check proves exact PDF reconstruction or validates source metadata claims.

## Example read-only SQL

Run these using a SQLite client against `data/database/corpus.sqlite`. They do
not alter source records or automatically resolve duplicate identities.

```sql
-- Every collected work belongs to exactly one current state.
SELECT status, COUNT(*) AS works FROM works GROUP BY status ORDER BY status;

-- DOI collisions are review candidates, not automatic merges.
SELECT doi, COUNT(*) AS work_count, GROUP_CONCAT(id) AS work_ids
FROM works WHERE doi IS NOT NULL GROUP BY doi HAVING COUNT(*) > 1;

-- Authors remain in original source order; affiliations retain their own order.
SELECT wa.author_index, a.id, a.name, wi.institution_index, i.name AS institution
FROM work_authors wa
LEFT JOIN authors a ON a.id = wa.author_id
LEFT JOIN work_institutions wi
  ON wi.work_id = wa.work_id AND wi.author_index = wa.author_index
LEFT JOIN institutions i ON i.id = wi.institution_id
WHERE wa.work_id = 'W123'
ORDER BY wa.author_index, wi.institution_index;

-- Raw history includes fields that were removed by a later source revision.
SELECT retrieved_at, raw_sha256, raw_path, raw_json
FROM metadata_revisions WHERE work_id = 'W123' ORDER BY id;

-- Explicitly select an extraction attempt before querying page-level evidence.
SELECT e.id AS run_id, e.status, e.output_dir, p.page_number, p.status AS page_status,
       json_extract(p.payload_json, '$.missing_regions') AS missing_regions
FROM extraction_runs e
JOIN documents d ON d.extraction_run_id = e.id
JOIN pages p ON p.document_id = d.id
WHERE e.work_id = 'W123'
ORDER BY e.completed_at DESC, p.page_number;

-- Structured retry causes include attempts already consumed.
SELECT work_id, stage, code, attempts, next_retry_at, status
FROM retry_queue ORDER BY updated_at DESC;
```
