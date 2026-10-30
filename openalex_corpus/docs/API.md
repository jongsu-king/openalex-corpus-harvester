# OpenAlex API, download budgets, and reproducibility

The API client and downloader are independent HTTPX clients. Both have finite
retry limits. Their shared on-disk budget journal is located at
`data/state/network_budget.json` when used through the CLI. A separate lock file
serializes reservations across processes using the same corpus directory.

The paths below are relative to `src/openalex_corpus/`. Read
[the walkthrough](PIPELINE_WALKTHROUGH.md) for the surrounding crawl and
[the storage reference](DATABASE.md) for the resulting database records.

- [Metadata request path](#metadata-request-path)
- [Budget reservation and counter lifetimes](#budget-reservation-and-counter-lifetimes)
- [HTTP response decisions](#http-response-decisions)
- [PDF candidate and publication path](#pdf-candidate-and-publication-path)
- [Interrupted transfer decisions](#interrupted-transfer-decisions)

## Official sources and date of verification

The implementation was checked against the official documentation on
**2026-10-29**:

- [Authentication and rate limits](https://help.openalex.org/api/authentication/)
- [API operation costs](https://help.openalex.org/access/example-costs/)
- [Search parameters](https://help.openalex.org/api/searching/)
- [Filters and paid sync filters](https://help.openalex.org/api/filtering/)
- [Cursor pagination](https://help.openalex.org/api/paging/)
- [Cached PDF and TEI content](https://help.openalex.org/access/fulltext/)
- [Snapshot acquisition](https://help.openalex.org/access/snapshot/)

Older examples elsewhere can describe different credit prices and limits. This
project used the help-center pages above and stores observed rate headers so future
changes can be investigated. It does not assume a historic 100-request-per-second
allowance.

## Authentication and pagination

`OPENALEX_API_KEY` is read from the process environment. It is sent using the
officially supported `Authorization: Bearer ...` header, not placed in metadata
request URLs. There is no API-key field in YAML or source code. Response bodies
from HTTP errors and transport exception text are not incorporated into client
errors, because they may echo credentials. Download provenance redacts secret
query parameters. Authentication is not forwarded to publisher or repository
hosts during content redirects, and a content request carrying credentials must
use HTTPS.

The documented anonymous limit is 10 requests/second; a free key allows 30.
The default client setting is 2 requests/second. Keep it at or below 10 without a
key. The client always uses `per_page=100`. Anonymous access can fetch singleton
records and the first cursor page; a key is required to continue past that page.
Attempting anonymous continuation raises `API_KEY_REQUIRED` before sending a
request. The checkpoint stays available for a later run with the environment
variable set. No API key was required for the live singleton validation below.

API pages are persisted in full before they are consumed. The pipeline saves the
cursor, position within the retained page, processed count, reported total, and
page checksum. A work limit in the middle of a page therefore does not discard
the unprocessed tail. An empty page with a continuation cursor, a repeated cursor,
or an invalid page shape is an error rather than evidence of a finished crawl.
Use the CLI's bounded queries for subsets; use the official Snapshot for a whole
database. API results are live, so saved requests and responses support replay,
but querying the service again later does not guarantee the same result set.

## Search behavior

`SearchSpec` accepts only a maintained allowlist of documented filters. Unknown
filter names fail locally. Author, institution, source, and topic selectors take
official entity IDs, not guessed name filters. Arbitrary title/keyword text uses
`query`, while `title` uses `search.title`. Abstract-only search currently uses the
documented legacy `abstract.search` filter. OpenAlex discourages the legacy
filter syntax when an equivalent scoped search parameter exists.

| Setting | Actual API field | Meaning |
| --- | --- | --- |
| `query` with `scope: all` | `search` | OpenAlex's broad text/keyword search |
| `query` with another `scope` | `search.<scope>` | `title`, `title_and_abstract`, or `title_abstract_keywords` |
| `exact: true` | `.exact` suffix on the selected search parameter | Exact rather than stemmed query behavior |
| `title` | `search.title` | Title text search |
| `abstract` | `abstract.search` filter | Abstract text search |
| `has_pdf` | `has_content.pdf` filter | PDF exists in the OpenAlex content archive |
| `has_content` | `has_fulltext` filter | OpenAlex indicates indexed full text |
| `filters.has_content.grobid_xml` | `has_content.grobid_xml` filter | Cached TEI XML availability |
| `updated_since` | `from_updated_date` filter | Paid-plan update filtering |

Archive PDF availability is distinct from an OA publisher URL. `has_pdf=false`
does not establish that no legal PDF is available elsewhere. `has_content` does
not mean this program has downloaded anything. For precise archive queries, use
the two explicit `has_content.*` filters. Publisher/source, license, version, and
all location data are still retained in the raw work record.

Using publication dates and re-seeing known IDs on a free account can discover
newly encountered work, but does not guarantee detection of every metadata
update. The client does not silently replace an unavailable paid update filter
with a weaker publication-date filter.

## Metadata request path

`api/search.py:SearchSpec.params()` constructs a request from validated options.
`api/client.py:OpenAlexClient` performs HTTP operations; it does not write crawl
pages or ingest works. Those persistence steps belong to `Pipeline.harvest()`.
The sequence for a newly requested page is:

```text
SearchSpec.params()
  |  query -> search[.<scope>][.exact]; allowed filters -> sorted filter string
  v
OpenAlexClient.get_page(params, cursor)
  |-- no key and cursor != "*"? ----------> API_KEY_REQUIRED; no reservation
  |-- params contain api_key or select? --> ValueError; no reservation
  |-- force per_page=100 and the supplied cursor
  |-- choose search estimate or list estimate
  v
_request("works", request, estimated_cost)
  |
  +--> retry loop, at most api.max_attempts
         |-- BudgetJournal.reserve_metadata() -> durable reservation
         |-- _throttle() -> spacing by requests_per_second
         |-- request_count += 1
         |-- HTTPX GET, with redirects disabled
         |-- observe X-RateLimit-* headers before classifying HTTP status
         |-- classify status / transport failure
         |     `-- TransientAPIError -> bounded wait -> next reservation
         `-- HTTP 200 -> decode JSON and require a JSON object
  |
  v
get_page(): require results list, meta object, count and next_cursor keys
  |          require each result to be an object with a nonempty id
  v
Pipeline.harvest(): save complete parsed page JSON and receipt
  `-- process a batch -> checkpoint its offset and the retained page identity
```

The client chooses the search estimate when a request key begins with `search`
or the filter contains `.search:`. A singleton `get_work()` instead normalizes
the identifier and requests `works/<identifier>` with a zero monetary estimate.
It still reserves one metadata request attempt and requires an ID in the result.

The throttle uses a monotonic clock and an `asyncio.Lock` inside one client.
The HTTPX metadata connection pool allows two connections, but that pool is not
a cross-process rate limiter. The budget journal lock coordinates reservations
for clients using the same state directory; it does not coordinate request
spacing across unrelated clients. A retained crawl page is read locally on
resume and needs neither a new metadata reservation nor a new GET.

## Monetary assumptions

The documented October 2026 per-operation estimates used by this project are:

| Operation | Estimate per request |
| --- | ---: |
| Singleton work by ID/DOI | USD 0 |
| List plus filters | USD 0.0001 |
| Text search, including a search filter | USD 0.001 |
| OpenAlex cached PDF content | USD 0.01 |

These are request prices, not per-work metadata prices. A search page can return
up to 100 works. The official free allowance is USD 1/day with a free key and
USD 0.10/day anonymously. Reset occurs at UTC midnight. OpenAlex can draw on an
account's prepaid balance after its free allowance, so an API key alone does not
mean unlimited free use. See the official pricing and authentication pages above.

The metadata client reserves the estimated price **before each attempted
request**, including retries. Crashes and failed attempts retain their
reservations; successful cheaper responses do not refund them. This deliberately
overcounts uncertain spending. On a response, `X-RateLimit-Cost-USD` takes
precedence for accounting. If absent, the documented conversion of one credit
to USD 0.0001 is used with `X-RateLimit-Credits-Used`. A higher observed price
increases the reservation. Remaining-budget and reset headers can block later
metadata requests until the reported reset time.

Prices are external facts. A future price increase can only be observed after a
response unless OpenAlex provides a reliable pre-request quote for that exact
operation. Update the configured estimates when prices change. This is a
conservative local request limiter, not an account-wide billing lock. It cannot
account for requests from another program, browser, or separately configured
data directory. It never buys credits, subscribes to a plan, or changes billing.

## Two independent budget scopes

| Configuration | Default | Scope and behavior |
| --- | ---: | --- |
| `api.daily_request_budget` | 1000 | Maximum reserved metadata HTTP attempts per UTC day |
| `api.daily_budget_usd` | 0.05 | Maximum estimated metadata spend per UTC day |
| `api.estimated_list_cost_usd` | 0.0001 | Reservation for a metadata list request |
| `api.estimated_search_cost_usd` | 0.001 | Reservation for a metadata search request |
| `download.max_downloads` | 100 | Maximum work-level download calls needing network processing per downloader instance; reused PDFs do not consume it |
| `download.max_content_downloads` | 0 | Lifetime reserved OpenAlex content GET attempts in this data directory |
| `download.max_content_cost_usd` | 0 | Lifetime estimated OpenAlex content spend in this data directory |
| `download.content_cost_usd` | 0.01 | Reservation for each content GET attempt |

The metadata day resets automatically; the content counters **do not**. Raising
an explicit content cap permits additional attempts up to the new cumulative
cap. Both the count cap and monetary cap must allow the next attempt. Content
redirects and retries are still accounted for conservatively. Disabling content
downloads does not prevent attempts at free OA publisher/repository URLs.
The downloader uses the first successful available candidate, not every URL.

The metadata and content money caps are separate. Their sum, together with any
usage outside this corpus, must be considered when choosing a personal daily
spending limit. The default content caps are zero, so this project does not make
metered OpenAlex PDF requests unless the configuration explicitly enables them.
The content cap remains lifetime-scoped even if a daily metadata reset occurs.

The journal is written atomically after a reservation and before network use.
Do not delete it to retry: that would remove the history that enforces the caps.
A malformed journal fails closed. Changing `data_dir` creates a different local
budget scope. A downloaded file's successful validation does not remove the
reserved cost of any earlier failed attempt.

## Budget reservation and counter lifetimes

`BudgetJournal.transaction()` uses a separate `network_budget.lock` file. The
lock covers reading, checking, and replacing the journal; it is released before
the HTTP request. Money is calculated using `Decimal` and stored as strings.

```text
reserve_metadata(estimate, request_limit, cost_limit)
  |
  +--> flock(network_budget.lock, LOCK_EX)
  +--> read network_budget.json, or initialize when absent
  |      `-- unreadable JSON -> BUDGET_JOURNAL_INVALID; refuse network use
  +--> UTC day changed? -> reset metadata counters and server block time
  +--> blocked_until is in the future? -> reject reservation
  +--> count >= request_limit? ---------> reject reservation
  +--> reserved + estimate > cap? ------> reject reservation
  +--> increment count; add estimate
  +--> write PID-specific temporary file -> flush -> fsync -> os.replace
  `--> unlock -> caller may throttle and send GET
```

| Journal field | Writer and meaning | Reset |
| --- | --- | --- |
| `day` | UTC date selected when a journal transaction opens | Updated on date change |
| `metadata_requests` | Accepted metadata attempt reservations, including zero-cost singletons | UTC day change |
| `metadata_reserved_usd` | Estimates plus positive observed cost adjustments | UTC day change |
| `rate_headers` | Latest observed metadata `x-ratelimit-*` headers | Cleared on UTC day change |
| `blocked_until` | Server-reported metadata allowance exhaustion | Cleared on UTC day change |
| `content_requests` | Accepted GET reservations for `content.openalex.org` | No implicit reset |
| `content_reserved_usd` | Cumulative configured content cost reservations | No implicit reset |
| `rate_header_warning` | An observed rate value could not be interpreted | Not a budget override |

A rejected reservation raises before the journal replacement and sends no GET.
An accepted reservation survives a crash during the subsequent throttle or
request. Therefore `metadata_requests` can exceed the number of requests that
reached OpenAlex. The client's in-memory `request_count` increments after the
throttle, immediately before GET; it is a different counter.

For an illustrative search with a USD 0.001 estimate, one timeout followed by
one successful retry reserves two requests and USD 0.002. A lower observed cost
does not reduce that amount. If the second response reports USD 0.0015, the
journal adds the USD 0.0005 difference, for USD 0.0025 in total. This example is
accounting arithmetic, not an additional live API measurement.

`observe_headers()` prefers direct USD cost, then falls back to reported credits
times USD 0.0001. A nonpositive remaining allowance stores a reset time. Invalid
numeric header values record `rate_header_warning`; configured limits remain in
force. Header observation occurs even for a response that will subsequently
raise an HTTP error. A successful HTTP 200 response can be returned while its
headers block the *next* request.

## Retry and pause behavior

HTTP 429, 5xx responses, connection errors, and timeouts use bounded retries with
exponential jitter. `Retry-After` accepts seconds or an HTTP date. Delays beyond
`max_retry_after_seconds` return a pause instead of sleeping indefinitely. The
pipeline stores the resulting retry time. Exhausted daily metadata budgets raise
`BudgetExceeded` with a reset timestamp. Content lifetime caps require an explicit
configuration change; waiting for a new day does not replenish them.

Authentication/entitlement errors and malformed responses are not retried as
temporary network failures. Individual PDF candidate failures remain in
`download_report.json` and `download_manifest.json`; metadata survives even when
no candidate succeeds. Each transfer is size-limited and first uses a `.part`
file. Range resumption requires a prior ETag or Last-Modified validator and a
matching Content-Range. A server that ignores the range triggers a safe restart.

## HTTP response decisions

The inner metadata retry loop catches `TransientAPIError` specifically. An error
marked `retryable=True` can instead leave the loop with `retry_at`, allowing the
crawl to persist a pause without holding the process in a long sleep.

| Condition | Result from metadata client | Same-call retry |
| --- | --- | --- |
| Anonymous continuation cursor | `API_KEY_REQUIRED`, before `_request()` | No |
| Local count/cost cap or active server block | `BUDGET_EXCEEDED`, with `retry_at` | No |
| Timeout | `API_TIMEOUT` as `TransientAPIError` | Within attempt limit |
| Network or remote protocol error | `API_CONNECTION_ERROR` as `TransientAPIError` | Within attempt limit |
| HTTP 401 / 403 | `API_AUTH_ERROR` | No |
| HTTP 404 | `API_NOT_FOUND` | No |
| HTTP 429 / 5xx with exhausted allowance | `BUDGET_EXCEEDED` | No |
| HTTP 429 / 5xx with excessive `Retry-After` | `RATE_LIMITED`, with `retry_at` | No |
| Other HTTP 429 | `RATE_LIMITED` as `TransientAPIError` | Within attempt limit |
| Other HTTP 5xx | `API_SERVER_ERROR` as `TransientAPIError` | Within attempt limit |
| Other non-200 status, including a redirect | `API_HTTP_ERROR` | No |
| Invalid JSON, non-object response, invalid page shape | `API_INVALID_RESPONSE` | No |

The wait is the greater of the parsed `Retry-After` and exponential jitter
(multiplier 0.5, jitter ceiling 15 seconds). Every retry re-enters the budget
reservation step. The last transient exception escapes when attempts run out.
Neither authentication errors nor malformed payloads become empty successful
pages. Crawl-level handling, saved offsets, and command exit codes are documented
in [the walkthrough](PIPELINE_WALKTHROUGH.md).

## PDF caching and explicit refresh

By default, an existing manifest version is reused only after its bytes pass
PDF validation and its SHA-256 matches the recorded hash. The newest valid
retrieval record whose source URL is still a candidate is selected. Cached
records from older URLs remain available as historical provenance.

Set `download.refresh_existing: true` to issue a fresh download even for a valid
cached URL. This setting does not bypass any download, content-cost, license, or
size limit. When the same URL returns different bytes, both hashes remain in
`pdf_versions/`, both retrieval records remain in `download_manifest.json`, and
`original.pdf` keeps the first downloaded bytes. A later ordinary run reuses the
newest valid version. Equal bytes share the same content-addressed file, with
separate retrieval evidence. A bad prior version is quarantined before recovery;
it is not silently erased or accepted as valid.

## PDF candidate and publication path

`download/pdf_downloader.py:pdf_candidates()` constructs the following order,
removing repeated URL strings while retaining the first occurrence:

```text
Work raw JSON
  |-- content_urls.pdf                         # OpenAlex archive candidate
  |-- best_oa_location.pdf_url
  |-- primary_location.pdf_url
  `-- locations[i].pdf_url where is_oa is True # original list order
       |
       v
PDFDownloader.download(work, paper_dir)
  |-- per-paper asyncio lock + download concurrency semaphore
  |-- read download_manifest.json
  |     `-- malformed manifest -> fail without overwriting its contents
  |-- refresh disabled and valid cached version available? -> REUSED
  |-- no manifest versions, valid original.pdf available? --> REUSED (no provenance)
  |-- no candidates? -------------------------------------> PDF_NOT_AVAILABLE
  |-- reserve one in-memory download_count slot for this work
  |
  `-- for each candidate, until one succeeds
        |-- validate scheme, OA indication and configured license allowlist
        |-- .downloads/<url_sha256>.part + matching receipt
        |-- bounded transfer attempts -> _stream()
        |-- validate_pdf(): signature, opening, encryption, pages and SHA-256
        |-- publish pdf_versions/<content_sha256>.pdf
        |-- create original.pdf only if absent
        |-- append retrieval and attempt evidence to download_manifest.json
        `-- remove transfer receipt; return DOWNLOADED
```

The legacy `original.pdf` reuse branch also requires refresh to be disabled.
A reused manifest version must have a source URL hash still present in the
current candidate list. Validation checks both readable PDF structure and the
recorded content hash. Cache reuse precedes the per-work network counter and
does not spend a new content reservation. The counter increments once for a work
needing network processing, even if every candidate is subsequently filtered;
it does not count each URL or HTTP retry.

Ordinary publisher/repository candidates explicitly marked `is_oa=False` are
skipped. Unknown OA status is not equivalent to that explicit false value.
The archive candidate has separate authentication and budget checks; its license
is copied from `best_oa_location` and marked as unverified for the archived
version. The downloader requests supplied PDF URLs; it does not discover links
by fetching landing-page HTML.

An already stored file with the expected content-addressed name is validated
before deduplication. Corrupt bytes are preserved under a `.corrupt.*.bin` name
before validated replacement bytes take that path. A corrupt `original.pdf`
is preserved with a warning; a valid version remains available separately.
Different successful retrievals can share one hash-addressed file and still
have separate manifest entries.

Both successful retrieval and exhausted-candidate paths append attempt evidence
to the manifest. Early returns, such as an invalid manifest, cache reuse, or no
candidates, do not run that append step. The surrounding pipeline writes the
returned result to `download_report.json`, records the download job, and either
registers the PDF artifact or records an error and possible retry.

When all candidates fail, the aggregate reason uses this priority:

```text
RATE_LIMITED > DOWNLOAD_FAILED > INVALID_PDF > ENCRYPTED_PDF
            > ACCESS_RESTRICTED > LICENSE_RESTRICTED > PDF_NOT_AVAILABLE
```

The returned result is retryable if any attempt was retryable **or** the aggregate
status is `RATE_LIMITED`. In particular, an archive content-cap rejection stops
that candidate's local retry loop, but the aggregate `RATE_LIMITED` result can
still create a pipeline retry. Repeated retries cannot replenish a lifetime
content cap. Inspect the individual attempt reasons when several URLs failed
for different causes.

## Interrupted transfer decisions

Each URL has a partial file and JSON receipt. `_stream()` resumes only when both
exist and the receipt has a validator. Strong ETag is preferred; Last-Modified
is the fallback. A weak ETag alone is not a sufficient resume validator.

```text
part + receipt + validator? -- no --> ordinary GET; write from byte zero
  |
  yes
  v
offset = current .part size
GET with Range: bytes=<offset>- and If-Range: <validator>
  |
  +--> 206, matching range; no observed validator change -> append
  +--> 200, server ignored range -----------------------> truncate and restart
  +--> 416 or invalid range/changed validator ----------> discard receipt;
  |                                                      bounded retry starts over
  +--> 429 / 5xx / transport failure --------------------> retain partial evidence;
  |                                                      apply retry policy
  `--> valid completed stream -> flush/fsync -> validate PDF -> publish
```

| Transfer check | Implementation behavior |
| --- | --- |
| Redirect handling | At most six request-loop iterations; require Location and HTTP(S); reject HTTPS-to-HTTP downgrade |
| Authorization | Recompute headers for each hop; attach Bearer only to HTTPS `content.openalex.org`; never forward it to an ordinary publisher host |
| Content accounting | Reserve cumulative count and cost before every archive GET, including retried or redirected archive requests |
| HTTP 401 / 403 / 451 | `ACCESS_RESTRICTED`, no retry for that attempt sequence |
| HTTP 429 / 5xx | Retryable; honor Retry-After within the configured wait bound |
| HTML, XHTML, JSON or plain-text Content-Type | Reject as `INVALID_PDF` before publication |
| HTTP 206 | Require a parseable Content-Range whose start equals the local offset; reject an observed changed validator |
| Declared size | Check Content-Length and Content-Range total against `max_file_bytes` before copying |
| Streamed size | Enforce the same cap while writing chunks |
| Length mismatch | Identity-encoded Content-Length or declared total mismatch is a retryable failure |
| Invalid completed PDF | Keep rejected bytes as `.part`; do not promote them to a published PDF |

The transfer receipt retains a validator and sanitized original and final URLs.
Completed attempt details are stored in the download result and manifest.
The receipt supports download resumption; it is separate from
the successful-retrieval manifest and from a crawl-page receipt. A work's latest
download status also differs from its retained corpus status: a previously
successful work keeps that success when a later download attempt fails.

## Actual live validation

One anonymous singleton request was executed on 2026-10-29 at 11:58:39 KST
(02:58:39 UTC), for DOI `10.48550/arXiv.2205.01833`. It returned
`https://openalex.org/W4229010617`. All 52 returned fields were preserved in
`data/validation/live_work.json`. The evidence file is
`data/validation/live_api_evidence.json`.

Observed headers for that request were:

```json
{
  "x-ratelimit-cost-usd": "0",
  "x-ratelimit-credits-used": "0",
  "x-ratelimit-limit": "1000",
  "x-ratelimit-limit-usd": "0.1",
  "x-ratelimit-onetime-remaining": "0",
  "x-ratelimit-prepaid-remaining-usd": "0",
  "x-ratelimit-remaining": "1000",
  "x-ratelimit-remaining-usd": "0.1",
  "x-ratelimit-reset": "75680"
}
```

This confirms a free singleton and the direct USD headers for that response.
It does not validate paid content downloads, authenticated deep pagination, or
paid update filters. The 429, timeout, authentication, pagination, budget, PDF,
resume, cache, and refresh cases are tested with offline HTTPX transports and
real generated PDF bytes, without external charges.
