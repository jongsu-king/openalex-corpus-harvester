CREATE TABLE IF NOT EXISTS works (
 id TEXT PRIMARY KEY, doi TEXT, title TEXT, publication_year INTEGER, publication_date TEXT,
 status TEXT NOT NULL DEFAULT 'PARTIAL' CHECK(status IN ('COMPLETE','COMPLETE_WITH_WARNINGS','PARTIAL','FAILED','PDF_UNAVAILABLE','PENDING_RETRY')),
 missing_reason TEXT, raw_json TEXT NOT NULL, metadata_json TEXT NOT NULL, raw_sha256 TEXT NOT NULL,
 raw_path TEXT NOT NULL, paper_dir TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS works_doi_idx ON works(doi);
CREATE INDEX IF NOT EXISTS works_status_idx ON works(status);
CREATE TABLE IF NOT EXISTS metadata_revisions (
 id INTEGER PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works(id), raw_sha256 TEXT NOT NULL,
 raw_json TEXT NOT NULL, metadata_json TEXT NOT NULL, raw_path TEXT NOT NULL, retrieved_at TEXT NOT NULL,
 UNIQUE(work_id,raw_sha256)
);
CREATE TABLE IF NOT EXISTS authors (id TEXT PRIMARY KEY, name TEXT, orcid TEXT, payload_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS work_authors (
 work_id TEXT NOT NULL REFERENCES works(id), author_index INTEGER NOT NULL, author_id TEXT REFERENCES authors(id),
 author_position TEXT, is_corresponding INTEGER, payload_json TEXT NOT NULL, PRIMARY KEY(work_id,author_index)
);
CREATE TABLE IF NOT EXISTS institutions (id TEXT PRIMARY KEY, name TEXT, ror TEXT, country_code TEXT, payload_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS work_institutions (
 work_id TEXT NOT NULL REFERENCES works(id), author_index INTEGER NOT NULL, institution_index INTEGER NOT NULL,
 institution_id TEXT REFERENCES institutions(id), payload_json TEXT NOT NULL, PRIMARY KEY(work_id,author_index,institution_index)
);
CREATE TABLE IF NOT EXISTS sources (id TEXT PRIMARY KEY, name TEXT, issn_l TEXT, payload_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS topics (id TEXT PRIMARY KEY, name TEXT, payload_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS work_topics (
 work_id TEXT NOT NULL REFERENCES works(id), topic_index INTEGER NOT NULL, topic_id TEXT REFERENCES topics(id),
 score REAL, payload_json TEXT NOT NULL, PRIMARY KEY(work_id,topic_index)
);
CREATE TABLE IF NOT EXISTS identifiers (
 work_id TEXT NOT NULL REFERENCES works(id), scheme TEXT NOT NULL, value TEXT NOT NULL, source TEXT NOT NULL,
 PRIMARY KEY(work_id,scheme,value,source)
);
CREATE INDEX IF NOT EXISTS identifiers_lookup_idx ON identifiers(scheme,value);
CREATE TABLE IF NOT EXISTS work_locations (
 work_id TEXT NOT NULL REFERENCES works(id), location_index INTEGER NOT NULL, role TEXT NOT NULL,
 source_id TEXT REFERENCES sources(id), pdf_url TEXT, landing_page_url TEXT, license TEXT, version TEXT,
 is_oa INTEGER, payload_json TEXT NOT NULL, PRIMARY KEY(work_id,role,location_index)
);
CREATE TABLE IF NOT EXISTS "references" (id TEXT PRIMARY KEY, source TEXT NOT NULL, payload_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS work_references (
 work_id TEXT NOT NULL REFERENCES works(id), reference_index INTEGER NOT NULL, reference_id TEXT NOT NULL REFERENCES "references"(id),
 source TEXT NOT NULL, PRIMARY KEY(work_id,reference_index)
);
CREATE TABLE IF NOT EXISTS artifacts (
 id INTEGER PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works(id), kind TEXT NOT NULL, path TEXT NOT NULL,
 sha256 TEXT, metadata_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(work_id,kind,path)
);
CREATE TABLE IF NOT EXISTS artifact_revisions (
 id INTEGER PRIMARY KEY, artifact_id INTEGER NOT NULL REFERENCES artifacts(id), sha256 TEXT, metadata_json TEXT NOT NULL,
 recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS extraction_runs (
 id TEXT PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works(id), status TEXT NOT NULL, engine TEXT,
 parser_versions_json TEXT NOT NULL, output_dir TEXT NOT NULL, started_at TEXT NOT NULL, completed_at TEXT,
 result_json TEXT NOT NULL, quality_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS extraction_work_idx ON extraction_runs(work_id,completed_at);
CREATE TABLE IF NOT EXISTS documents (
 id INTEGER PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works(id), extraction_run_id TEXT NOT NULL UNIQUE REFERENCES extraction_runs(id),
 pdf_path TEXT, pdf_sha256 TEXT, page_count INTEGER NOT NULL, status TEXT NOT NULL, payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pages (
 id INTEGER PRIMARY KEY, document_id INTEGER NOT NULL REFERENCES documents(id), page_number INTEGER NOT NULL,
 width REAL, height REAL, text_blocks_count INTEGER, char_count INTEGER, table_count INTEGER, image_count INTEGER,
 equation_count INTEGER, ocr_used INTEGER, status TEXT NOT NULL, payload_json TEXT NOT NULL, UNIQUE(document_id,page_number)
);
CREATE TABLE IF NOT EXISTS text_blocks (
 id INTEGER PRIMARY KEY, document_id INTEGER NOT NULL REFERENCES documents(id), page_number INTEGER,
 block_index INTEGER NOT NULL, text TEXT, bbox_json TEXT, source TEXT, payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tables (
 id INTEGER PRIMARY KEY, document_id INTEGER NOT NULL REFERENCES documents(id), table_index INTEGER NOT NULL,
 page_number INTEGER, caption TEXT, bbox_json TEXT, source TEXT, status TEXT, payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS table_cells (
 id INTEGER PRIMARY KEY, table_id INTEGER NOT NULL REFERENCES tables(id), row_index INTEGER, column_index INTEGER,
 row_span INTEGER, col_span INTEGER, value_json TEXT NOT NULL, payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS figures (
 id INTEGER PRIMARY KEY, document_id INTEGER NOT NULL REFERENCES documents(id), figure_index INTEGER NOT NULL,
 page_number INTEGER, caption TEXT, image_file TEXT, bbox_json TEXT, source TEXT, payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS equations (
 id INTEGER PRIMARY KEY, document_id INTEGER NOT NULL REFERENCES documents(id), equation_index INTEGER NOT NULL,
 page_number INTEGER, latex TEXT, text TEXT, bbox_json TEXT, source TEXT, status TEXT, payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS extracted_references (
 id INTEGER PRIMARY KEY, document_id INTEGER NOT NULL REFERENCES documents(id), reference_index INTEGER NOT NULL,
 page_number INTEGER, original_text TEXT, doi TEXT, matched_work_id TEXT, match_evidence_json TEXT, payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS validation_results (
 id INTEGER PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works(id), extraction_run_id TEXT REFERENCES extraction_runs(id),
 page_number INTEGER, severity TEXT, code TEXT NOT NULL, message TEXT, payload_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS download_jobs (
 work_id TEXT PRIMARY KEY REFERENCES works(id), status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
 error_code TEXT, updated_at TEXT NOT NULL, payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS parse_jobs (
 work_id TEXT PRIMARY KEY REFERENCES works(id), status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
 error_code TEXT, updated_at TEXT NOT NULL, payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS crawl_runs (
 id TEXT PRIMARY KEY, query_hash TEXT NOT NULL, query_json TEXT NOT NULL, status TEXT NOT NULL,
 started_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT
);
CREATE INDEX IF NOT EXISTS crawl_query_idx ON crawl_runs(query_hash,started_at);
CREATE TABLE IF NOT EXISTS crawl_checkpoints (
 run_id TEXT PRIMARY KEY REFERENCES crawl_runs(id), cursor TEXT, offset INTEGER NOT NULL DEFAULT 0 CHECK(offset>=0),
 processed INTEGER NOT NULL DEFAULT 0 CHECK(processed>=0), total_count INTEGER, status TEXT NOT NULL,
 extras_json TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS crawl_work_ids (
 run_id TEXT NOT NULL REFERENCES crawl_runs(id), work_id TEXT NOT NULL REFERENCES works(id), discovered_at TEXT NOT NULL,
 PRIMARY KEY(run_id,work_id)
);
CREATE TABLE IF NOT EXISTS errors (
 id INTEGER PRIMARY KEY, work_id TEXT REFERENCES works(id), requested_work_id TEXT, stage TEXT NOT NULL, code TEXT NOT NULL,
 message TEXT NOT NULL, retryable INTEGER NOT NULL, attempt INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS retry_queue (
 id INTEGER PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works(id), stage TEXT NOT NULL, code TEXT NOT NULL,
 attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts>=0), next_retry_at TEXT, status TEXT NOT NULL DEFAULT 'PENDING',
 last_error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(work_id,stage)
);
CREATE INDEX IF NOT EXISTS retry_pending_idx ON retry_queue(status,next_retry_at);
CREATE TABLE IF NOT EXISTS file_checksums (
 path TEXT PRIMARY KEY, work_id TEXT REFERENCES works(id), sha256 TEXT NOT NULL, size_bytes INTEGER,
 verified_at TEXT NOT NULL, status TEXT NOT NULL
);
