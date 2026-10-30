# OPV search scope and resumable collection

The discovery run described here took place on **2026-10-29, Asia/Seoul**. OPV means **organic photovoltaics / organic solar cells** in this collection, not oral polio vaccine. The counts and API behavior below are observations from that run. For collection progress after that date, read `data/opv/collection_report.json`.

## What the search includes

The search targets OpenAlex **titles, abstracts, and indexed keywords**, combining English terms with photovoltaic or solar-cell context. It applies no filters for year, language, open-access status, PDF availability, or work type. Results can therefore include reviews, conference material, theses, book chapters, datasets, and other OpenAlex Work types as well as journal articles. Each source record retains its type.

The final query is stored in `config/opv.yaml` as `search.query`, with `scope: title_abstract_keywords` and `exact: true`. It combines:

- `organic` with photovoltaic/photovoltaics or solar cell/solar cells.
- Polymer solar cell(s) and polymer photovoltaic(s).
- Plastic solar cell(s) and plastic photovoltaic(s).
- OPV, OPVs, or organic PV with solar or photovoltaic(s) context.

This defines a set of search candidates to enumerate. It does not establish that every relevant paper is indexed by OpenAlex or uses those terms. Records written only in other languages, records without abstracts or keywords, and papers named only for particular materials may be missed. Organic–inorganic hybrid and perovskite papers may also match. Relevance review is a separate step.

In the first-page discovery sample, three titles containing `perovskite` were flagged for review. Those flags record a string found in the title; they neither exclude the work nor establish what the paper is about. The remaining records were not treated as relevance-validated OPV literature.

## Discovery results recorded on 2026-10-29

| Search definition | Total matches reported by the API | Records retained from the first page |
|---|---:|---:|
| `"organic photovoltaic*"` | 16,482 | 100 |
| `"organic solar cell*"` | 25,780 | 100 |
| `"polymer solar cell*"` | 9,558 | 100 |
| `"plastic solar cell*"` | 333 | 100 |
| OPV/OPVs with photovoltaic/solar-cell context | 6,301 | 100 |
| Core expressions, `core_union` | 62,586 | 100 |
| Broader organic + PV terms, `broad_union` | 82,110 | 100 |
| Final expanded query, `expanded_union` | **82,363** | 100 |

The eight first pages contained 800 record occurrences, representing **608 unique Work IDs** and **192 duplicate occurrences**. The searches overlap, so their reported totals must not be added. The individual wildcard searches also differ syntactically from the explicit singular/plural Boolean query. The final total of 82,363 came directly from the API response to `expanded_union`; it was not estimated from the other counts.

At 100 records per page, the final query implied about **824 pages** at the time. The discovery run had no API key and **did not follow the next cursor**. It retained 100 records for the final query, while the 608 unique records came from the union of all eight first-page samples. Neither figure means that all 82,363 matches had been collected. OpenAlex data can change during a later crawl; the report fields `unique_collected_for_query` and `reported_matches` describe that crawl's progress.

Discovery used **10 requests**, including rejected-query diagnostics, and reserved a conservative **$0.0100** in the local budget ledger. Two HTTP 400 responses remained charged against that reservation; the ledger did not assume they were free and refund the budget. The $0.0001 reservation for an earlier general validation request was separate. No OpenAlex-hosted PDF or TEI content was downloaded during discovery.

### Query syntax observed in that run

The [OpenAlex search documentation](https://help.openalex.org/api/searching/) describes scoped exact search, Boolean expressions, and trailing wildcards. In the recorded requests, the API accepted standalone `"organic photovoltaic*"` but rejected a Boolean query containing `"solar cell*"` with HTTP 400. The response is preserved in `data/opv_discovery/errors/opv_context_rejection.json`.

The working union query therefore lists singular and plural forms explicitly instead of using wildcards. The rejected query is retained as a failed attempt, not counted as a successful search. This is a record of the API behavior observed on 2026-10-29, not a fresh check of the service.

## Collect and resume

The harvester writes to **`data/opv`**, separate from the two existing test papers. By default it collects metadata only. It verifies the saved final-query first page against its hash and query before reusing it as a seed. PDF downloading and parsing require explicit options.

Run these commands from the application directory (`openalex_corpus/`):

```bash
# Save/reuse the first page; pause with PAUSED_API_KEY_REQUIRED if no key is available.
.venv/bin/python scripts/harvest_opv.py --all

# Explicitly load OPENALEX_API_KEY after adding it to the project's .env file.
.venv/bin/python scripts/harvest_opv.py --env-file .env --all

# Process at most 10,000 works in this run; resume on the next invocation.
.venv/bin/python scripts/harvest_opv.py --env-file .env --max-works 10000

# Choose explicit local budget caps for this run.
.venv/bin/python scripts/harvest_opv.py --env-file .env --all \
  --daily-budget-usd 0.05 --daily-request-budget 1000

# Inspect and export the collected records.
.venv/bin/openalex-corpus --config config/opv.yaml stats
.venv/bin/openalex-corpus --config config/opv.yaml export --format jsonl
.venv/bin/openalex-corpus --config config/opv.yaml export --format parquet
```

The `.env` file is parsed as data, never executed as shell code. The harvester uses `python-dotenv`'s `dotenv_values` to read only `OPENALEX_API_KEY` from the explicitly requested file, with environment-variable interpolation disabled. It accepts a UTF-8 BOM and rejects empty values and common placeholders. The key is not printed in results or logs. Without `--env-file`, that file is not loaded automatically.

`--all` removes only the per-run work-count limit. Daily dollar and request caps, server limits, authentication requirements, and retry limits still apply. The default metadata budget is $0.05 and 1,000 requests per day; the OpenAlex-hosted content budget is 0 downloads and $0. These caps do not imply that the full query can finish in one day. When a budget is exhausted, the harvester writes its checkpoint and resume time, then stops. `--daily-budget-usd` and `--daily-request-budget` change local caps; they do not buy credits or change an account plan.

The discovery run encountered OpenAlex's requirement for a free API key beyond the first cursor. The harvester pauses when it needs a key rather than splitting dates or changing pagination to work around that requirement. See the upstream [authentication](https://help.openalex.org/api/authentication/) and [paging](https://help.openalex.org/api/paging/) documentation when preparing a new run.

Ordinary reruns continue from the saved cursor and within-page offset. `--fresh` starts the same query again from the beginning while retaining previously stored raw records.

## Completion states and optional PDF processing

`COMPLETE_QUERY_MATCHES` means that the query reached its final cursor and the number of unique collected IDs matched the reported count. If the counts differ, for example because the source data changed during collection, the result is `COMPLETED_WITH_COUNT_MISMATCH_REVIEW`. Every report retains `all_world_literature_complete=false`: exhausting a query is a narrower claim than collecting all relevant literature.

Add `--download-pdf` to request PDF retrieval and `--parse-markdown` to request structured extraction. Completing the metadata list and obtaining every PDF are separate tasks. The downloader does not bypass payment or access restrictions. The script reuses one downloader throughout the run so that the `download.max_downloads` cap is not reset at each chunk.

If requested PDF or parsing work still has failures, retries, or unfinished items, reaching the final metadata cursor produces `QUERY_COMPLETE_PROCESSING_INCOMPLETE` and exit code 2. The report keeps the metadata outcome in `metadata_status` and `query_exhausted`; `requested_processing` records processing-state counts and the number of unavailable PDFs for that query.

Even when reusing a completed report, the harvester rechecks current database statistics and integrity. An integrity failure produces `FAILED_DATABASE_INTEGRITY` and exit code 2.

`pdf_unavailable_works` counts only works actually marked `PDF_NOT_AVAILABLE`. A corrupt PDF, failed download, access restriction, or content-budget stop is recorded by cause in `download_error_counts` and leaves processing incomplete.

## Retained evidence and tests

| File or directory | Contents |
|---|---|
| `config/opv_searches.yaml` | All discovery search definitions and the selected query |
| `data/opv_discovery/pages/*.json` | Complete raw first-page responses, with request details, hashes, and rate headers in adjacent `.receipt.json` files |
| `data/opv_discovery/summary.json` | Per-query totals, first-page overlaps, unique-ID counts, budget use, and the scope actually completed |
| `data/opv_discovery/first_pages_unique_raw.jsonl` | The 608 unique Work objects from the discovery sample |
| `data/opv_discovery/first_pages_index.json` | DOI, title, year, query membership, and title-string review flags |
| `data/opv/collection_report.json` | The latest corpus collection report; `collection_runs/` retains reports for individual runs |

`tests/test_opv.py` and `tests/test_opv_download_budget.py` cover transmission of `scope` and `exact`, BOM handling and non-executing credential reads, cursor and download-cap continuity across chunks, resume within a page, seeding followed by a missing-key pause, rejection of altered seed data, changed API totals, and reporting of downstream or database-integrity failures. The saved live first-page responses establish what the API returned during discovery. Simulated cursor tests establish resume and completion behavior; they are not evidence of a completed live crawl.
