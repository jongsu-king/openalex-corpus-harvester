"""SQLite corpus repository with immutable history and resumable checkpoints."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from ..metadata import normalize_doi, work_id
from .models import WorkStatus


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dump(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False
    )


def _safe(message: Any) -> str:
    message = str(message)
    secret = os.getenv("OPENALEX_API_KEY")
    if secret:
        message = message.replace(secret, "[REDACTED]")
    return re.sub(r"(?i)(api_key[=\s:]+|bearer\s+)[^&\s\"']+", r"\1[REDACTED]", message)


def _canonical_id(value: Any) -> str | None:
    return str(value).rstrip("/").rsplit("/", 1)[-1] if value else None


class Repository:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.execute("PRAGMA busy_timeout=30000")
        self._migrate()

    def _migrate(self) -> None:
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        migration_dir = Path(__file__).with_name("migrations")
        for path in sorted(migration_dir.glob("[0-9]*.sql")):
            version = int(path.name.split("_", 1)[0])
            if self.conn.execute("SELECT 1 FROM schema_migrations WHERE version=?", (version,)).fetchone():
                continue
            try:
                self.conn.executescript("BEGIN IMMEDIATE;\n" + path.read_text(encoding="utf-8"))
                self.conn.execute("INSERT INTO schema_migrations VALUES (?,?)", (version, _now()))
                self.conn.commit()
            except BaseException:
                self.conn.rollback()
                raise
        if not self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='works'"
        ).fetchone():
            raise RuntimeError("Database migrations are missing from the installed package")

    def __enter__(self) -> "Repository":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        nested = self.conn.in_transaction
        savepoint = "sp_" + uuid.uuid4().hex
        self.conn.execute(f"SAVEPOINT {savepoint}" if nested else "BEGIN IMMEDIATE")
        try:
            yield self.conn
        except BaseException:
            if nested:
                self.conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                self.conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            else:
                self.conn.rollback()
            raise
        else:
            if nested:
                self.conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            else:
                self.conn.commit()

    def ingest_work(self, raw: dict, normalized: dict, raw_path: str | Path) -> dict:
        identifier = work_id(raw.get("id") or normalized["openalex_id"])
        raw_serialized = _dump(raw)
        digest = hashlib.sha256(raw_serialized.encode()).hexdigest()
        raw_path = Path(raw_path).resolve()
        paper_dir = next(
            (parent for parent in raw_path.parents if parent.name == identifier), raw_path.parent
        )
        doi = normalize_doi(raw.get("doi"))
        timestamp = _now()
        with self.transaction():
            old = self.conn.execute("SELECT raw_sha256 FROM works WHERE id=?", (identifier,)).fetchone()
            self.conn.execute(
                """INSERT INTO works(id,doi,title,publication_year,publication_date,raw_json,metadata_json,raw_sha256,raw_path,paper_dir,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                doi=excluded.doi,title=excluded.title,publication_year=excluded.publication_year,publication_date=excluded.publication_date,
                raw_json=excluded.raw_json,metadata_json=excluded.metadata_json,raw_sha256=excluded.raw_sha256,
                raw_path=excluded.raw_path,paper_dir=excluded.paper_dir,updated_at=excluded.updated_at""",
                (
                    identifier,
                    doi,
                    raw.get("title"),
                    raw.get("publication_year"),
                    raw.get("publication_date"),
                    raw_serialized,
                    _dump(normalized),
                    digest,
                    str(raw_path),
                    str(paper_dir),
                    timestamp,
                    timestamp,
                ),
            )
            self.conn.execute(
                "INSERT OR IGNORE INTO metadata_revisions(work_id,raw_sha256,raw_json,metadata_json,raw_path,retrieved_at) VALUES (?,?,?,?,?,?)",
                (identifier, digest, raw_serialized, _dump(normalized), str(raw_path), timestamp),
            )
            self._normalize_relations(identifier, raw)
            duplicates = (
                [
                    row[0]
                    for row in self.conn.execute(
                        "SELECT id FROM works WHERE doi=? AND id<>? ORDER BY id", (doi, identifier)
                    )
                ]
                if doi
                else []
            )
        return {
            "work_id": identifier,
            "is_new": old is None,
            "changed": old is None or old[0] != digest,
            "duplicate_doi_ids": duplicates,
        }

    def _normalize_relations(self, identifier: str, raw: dict) -> None:
        for table in (
            "work_authors",
            "work_institutions",
            "work_topics",
            "identifiers",
            "work_locations",
            "work_references",
        ):
            self.conn.execute(f'DELETE FROM "{table}" WHERE work_id=?', (identifier,))
        identifiers = dict(raw.get("ids") or {})
        if raw.get("doi") is not None:
            identifiers["doi"] = normalize_doi(raw["doi"])
        for scheme, value in identifiers.items():
            if value is not None:
                self.conn.execute(
                    "INSERT OR IGNORE INTO identifiers VALUES (?,?,?,?)",
                    (identifier, scheme, str(value), "openalex"),
                )
        for author_index, authorship in enumerate(raw.get("authorships") or []):
            author = authorship.get("author") or {}
            author_id = _canonical_id(author.get("id"))
            if author_id:
                self.conn.execute(
                    "INSERT INTO authors VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,orcid=excluded.orcid,payload_json=excluded.payload_json",
                    (author_id, author.get("display_name"), author.get("orcid"), _dump(author)),
                )
            self.conn.execute(
                "INSERT INTO work_authors VALUES (?,?,?,?,?,?)",
                (
                    identifier,
                    author_index,
                    author_id,
                    authorship.get("author_position"),
                    authorship.get("is_corresponding"),
                    _dump(authorship),
                ),
            )
            for institution_index, institution in enumerate(authorship.get("institutions") or []):
                institution_id = _canonical_id(institution.get("id"))
                if institution_id:
                    self.conn.execute(
                        "INSERT INTO institutions VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,ror=excluded.ror,country_code=excluded.country_code,payload_json=excluded.payload_json",
                        (
                            institution_id,
                            institution.get("display_name"),
                            institution.get("ror"),
                            institution.get("country_code"),
                            _dump(institution),
                        ),
                    )
                self.conn.execute(
                    "INSERT INTO work_institutions VALUES (?,?,?,?,?)",
                    (identifier, author_index, institution_index, institution_id, _dump(institution)),
                )
        for topic_index, topic in enumerate(raw.get("topics") or []):
            topic_id = _canonical_id(topic.get("id"))
            if topic_id:
                self.conn.execute(
                    "INSERT INTO topics VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,payload_json=excluded.payload_json",
                    (topic_id, topic.get("display_name"), _dump(topic)),
                )
            self.conn.execute(
                "INSERT INTO work_topics VALUES (?,?,?,?,?)",
                (identifier, topic_index, topic_id, topic.get("score"), _dump(topic)),
            )
        location_groups = {"locations": raw.get("locations") or []}
        for key in ("primary_location", "best_oa_location"):
            location_groups[key] = [raw[key]] if raw.get(key) is not None else []
        for role, locations in location_groups.items():
            for location_index, location in enumerate(locations):
                source = location.get("source") or {}
                source_id = _canonical_id(source.get("id"))
                if source_id:
                    self.conn.execute(
                        "INSERT INTO sources VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,issn_l=excluded.issn_l,payload_json=excluded.payload_json",
                        (source_id, source.get("display_name"), source.get("issn_l"), _dump(source)),
                    )
                self.conn.execute(
                    "INSERT INTO work_locations VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        identifier,
                        location_index,
                        role,
                        source_id,
                        location.get("pdf_url"),
                        location.get("landing_page_url"),
                        location.get("license"),
                        location.get("version"),
                        location.get("is_oa"),
                        _dump(location),
                    ),
                )
        for reference_index, ref in enumerate(raw.get("referenced_works") or []):
            reference_id = _canonical_id(ref)
            if not reference_id:
                continue
            self.conn.execute(
                'INSERT OR IGNORE INTO "references" VALUES (?,?,?)',
                (reference_id, "openalex.referenced_works", _dump({"id": ref})),
            )
            self.conn.execute(
                "INSERT INTO work_references VALUES (?,?,?,?)",
                (identifier, reference_index, reference_id, "openalex.referenced_works"),
            )

    @staticmethod
    def _decode_work(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        result = dict(row)
        result["raw_json"] = result["raw"] = json.loads(result["raw_json"])
        result["metadata_json"] = result["metadata"] = json.loads(result["metadata_json"])
        return result

    def get_work(self, identifier: str) -> dict | None:
        return self._decode_work(
            self.conn.execute("SELECT * FROM works WHERE id=?", (work_id(identifier),)).fetchone()
        )

    def iter_works(self, status: str | None = None) -> Iterator[dict]:
        query, args = (
            ("SELECT * FROM works WHERE status=? ORDER BY id", (status,))
            if status
            else ("SELECT * FROM works ORDER BY id", ())
        )
        for row in self.conn.execute(query, args):
            yield self._decode_work(row)

    def set_work_status(self, identifier: str, status: str, missing_reason: str | None = None) -> None:
        WorkStatus(status)
        cursor = self.conn.execute(
            "UPDATE works SET status=?,missing_reason=?,updated_at=? WHERE id=?",
            (status, missing_reason, _now(), work_id(identifier)),
        )
        if cursor.rowcount != 1:
            raise KeyError(identifier)

    def upsert_artifact(
        self,
        identifier: str,
        kind: str,
        path: str | Path,
        sha256: str | None = None,
        metadata: dict | None = None,
    ) -> int:
        path = Path(path).resolve()
        timestamp = _now()
        with self.transaction():
            self.conn.execute(
                """INSERT INTO artifacts(work_id,kind,path,sha256,metadata_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(work_id,kind,path) DO UPDATE SET sha256=excluded.sha256,metadata_json=excluded.metadata_json,updated_at=excluded.updated_at""",
                (work_id(identifier), kind, str(path), sha256, _dump(metadata or {}), timestamp, timestamp),
            )
            artifact_id = self.conn.execute(
                "SELECT id FROM artifacts WHERE work_id=? AND kind=? AND path=?",
                (work_id(identifier), kind, str(path)),
            ).fetchone()[0]
            last = self.conn.execute(
                "SELECT sha256,metadata_json FROM artifact_revisions WHERE artifact_id=? ORDER BY id DESC LIMIT 1",
                (artifact_id,),
            ).fetchone()
            if last is None or (last[0], last[1]) != (sha256, _dump(metadata or {})):
                self.conn.execute(
                    "INSERT INTO artifact_revisions(artifact_id,sha256,metadata_json,recorded_at) VALUES (?,?,?,?)",
                    (artifact_id, sha256, _dump(metadata or {}), timestamp),
                )
            if sha256:
                self.conn.execute(
                    """INSERT INTO file_checksums VALUES (?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET
                    work_id=excluded.work_id,sha256=excluded.sha256,size_bytes=excluded.size_bytes,verified_at=excluded.verified_at,status=excluded.status""",
                    (
                        str(path),
                        work_id(identifier),
                        sha256,
                        path.stat().st_size if path.is_file() else None,
                        timestamp,
                        "REGISTERED",
                    ),
                )
        return artifact_id

    def record_extraction(self, identifier: str, result: dict, output_dir: str | Path, quality: dict) -> str:
        identifier = work_id(identifier)
        output_dir = str(Path(output_dir).resolve())
        run_id = result.get("run_id") or uuid.uuid5(uuid.NAMESPACE_URL, output_dir).hex
        timestamp = _now()
        pages = result.get("pages") or []
        status = quality.get("status") or result.get("status") or "PARTIAL"
        with self.transaction():
            existing = self.conn.execute(
                "SELECT work_id,output_dir,result_json,quality_json FROM extraction_runs WHERE id=?",
                (run_id,),
            ).fetchone()
            if existing is not None:
                if (existing["work_id"], existing["output_dir"]) != (identifier, output_dir):
                    raise ValueError(
                        "Extraction run ID already belongs to a different work or output directory"
                    )
                if (existing["result_json"], existing["quality_json"]) != (_dump(result), _dump(quality)):
                    raise ValueError("Refusing to mutate an existing immutable extraction run")
                return run_id
            self.conn.execute(
                "INSERT INTO extraction_runs VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    identifier,
                    status,
                    result.get("engine"),
                    _dump(result.get("parser_versions") or {}),
                    output_dir,
                    result.get("started_at") or timestamp,
                    timestamp,
                    _dump(result),
                    _dump(quality),
                ),
            )
            document_id = self.conn.execute(
                "INSERT INTO documents(work_id,extraction_run_id,pdf_path,pdf_sha256,page_count,status,payload_json) VALUES (?,?,?,?,?,?,?)",
                (
                    identifier,
                    run_id,
                    result.get("pdf_path"),
                    result.get("pdf_sha256"),
                    len(pages),
                    status,
                    _dump(result.get("document") or {}),
                ),
            ).lastrowid
            for page in pages:
                self.conn.execute(
                    """INSERT INTO pages(document_id,page_number,width,height,text_blocks_count,char_count,table_count,image_count,equation_count,ocr_used,status,payload_json)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        document_id,
                        page["page_number"],
                        page.get("width"),
                        page.get("height"),
                        page.get("text_blocks_count"),
                        page.get("char_count"),
                        page.get("table_count"),
                        page.get("image_count"),
                        page.get("equation_count"),
                        page.get("ocr_used"),
                        page.get("status", "unverified"),
                        _dump(page),
                    ),
                )
            for index, block in enumerate(result.get("text_blocks") or []):
                self.conn.execute(
                    "INSERT INTO text_blocks(document_id,page_number,block_index,text,bbox_json,source,payload_json) VALUES (?,?,?,?,?,?,?)",
                    (
                        document_id,
                        block.get("page_number"),
                        index,
                        block.get("text"),
                        _dump(block.get("bbox")),
                        block.get("source"),
                        _dump(block),
                    ),
                )
            for index, table in enumerate(result.get("tables") or []):
                table_id = self.conn.execute(
                    "INSERT INTO tables(document_id,table_index,page_number,caption,bbox_json,source,status,payload_json) VALUES (?,?,?,?,?,?,?,?)",
                    (
                        document_id,
                        index,
                        table.get("page_number"),
                        table.get("caption"),
                        _dump(table.get("bbox")),
                        table.get("source"),
                        table.get("status"),
                        _dump(table),
                    ),
                ).lastrowid
                for cell in table.get("cells") or []:
                    if isinstance(cell, dict):
                        self.conn.execute(
                            "INSERT INTO table_cells(table_id,row_index,column_index,row_span,col_span,value_json,payload_json) VALUES (?,?,?,?,?,?,?)",
                            (
                                table_id,
                                cell.get("row_index", cell.get("row")),
                                cell.get("column_index", cell.get("col", cell.get("column"))),
                                cell.get("row_span", cell.get("rowspan")),
                                cell.get("col_span", cell.get("colspan")),
                                _dump(cell.get("value", cell.get("text"))),
                                _dump(cell),
                            ),
                        )
                if not table.get("cells"):
                    for row_index, row in enumerate(table.get("rows") or []):
                        if isinstance(row, list):
                            for col_index, value in enumerate(row):
                                self.conn.execute(
                                    "INSERT INTO table_cells(table_id,row_index,column_index,row_span,col_span,value_json,payload_json) VALUES (?,?,?,?,?,?,?)",
                                    (
                                        table_id,
                                        row_index,
                                        col_index,
                                        1,
                                        1,
                                        _dump(value),
                                        _dump({"value": value, "source": table.get("source")}),
                                    ),
                                )
            for index, figure in enumerate(result.get("figures") or []):
                self.conn.execute(
                    "INSERT INTO figures(document_id,figure_index,page_number,caption,image_file,bbox_json,source,payload_json) VALUES (?,?,?,?,?,?,?,?)",
                    (
                        document_id,
                        index,
                        figure.get("page_number"),
                        figure.get("caption"),
                        figure.get("image_file", figure.get("path")),
                        _dump(figure.get("bbox")),
                        figure.get("source"),
                        _dump(figure),
                    ),
                )
            for index, equation in enumerate(result.get("equations") or []):
                self.conn.execute(
                    "INSERT INTO equations(document_id,equation_index,page_number,latex,text,bbox_json,source,status,payload_json) VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        document_id,
                        index,
                        equation.get("page_number"),
                        equation.get("latex"),
                        equation.get("text"),
                        _dump(equation.get("bbox")),
                        equation.get("source"),
                        equation.get("status"),
                        _dump(equation),
                    ),
                )
            for index, reference in enumerate(result.get("references") or []):
                if not isinstance(reference, dict):
                    reference = {"original_text": reference}
                # Only explicitly verified links are accepted. Similarity guesses remain raw candidates.
                evidence = reference.get("match_evidence")
                match_id = (
                    reference.get("matched_work_id")
                    if (
                        isinstance(evidence, dict)
                        and evidence.get("verified") is True
                        and evidence.get("method")
                        in {"exact_doi", "exact_openalex_id", "exact_pmid", "exact_pmcid"}
                    )
                    else None
                )
                self.conn.execute(
                    "INSERT INTO extracted_references(document_id,reference_index,page_number,original_text,doi,matched_work_id,match_evidence_json,payload_json) VALUES (?,?,?,?,?,?,?,?)",
                    (
                        document_id,
                        index,
                        reference.get("page_number"),
                        reference.get(
                            "original_text",
                            reference.get("raw_text", reference.get("original_reference_string")),
                        ),
                        normalize_doi(reference.get("doi")),
                        match_id,
                        _dump(evidence),
                        _dump(reference),
                    ),
                )
            messages = quality.get("issues") or quality.get("validation_results") or []
            for issue in messages:
                if not isinstance(issue, dict):
                    issue = {"code": "validation_message", "message": str(issue)}
                self.conn.execute(
                    "INSERT INTO validation_results(work_id,extraction_run_id,page_number,severity,code,message,payload_json,created_at) VALUES (?,?,?,?,?,?,?,?)",
                    (
                        identifier,
                        run_id,
                        issue.get("page_number"),
                        issue.get("severity"),
                        issue.get("code", "validation_message"),
                        issue.get("message"),
                        _dump(issue),
                        timestamp,
                    ),
                )
        return run_id

    def record_error(
        self, identifier: str | None, stage: str, code: str, message: str, retryable: bool, attempt: int = 1
    ) -> int:
        known_id = None
        if identifier:
            try:
                candidate = work_id(identifier)
                if self.conn.execute("SELECT 1 FROM works WHERE id=?", (candidate,)).fetchone():
                    known_id = candidate
            except ValueError:
                pass
        return self.conn.execute(
            "INSERT INTO errors(work_id,requested_work_id,stage,code,message,retryable,attempt,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (known_id, identifier, stage, code, _safe(message), bool(retryable), attempt, _now()),
        ).lastrowid

    def set_job(self, stage: str, identifier: str, status: str, **fields: Any) -> None:
        table = {"download": "download_jobs", "parse": "parse_jobs"}.get(stage)
        if table is None:
            raise ValueError("Job stage must be download or parse")
        old = self.conn.execute(
            f"SELECT attempts FROM {table} WHERE work_id=?", (work_id(identifier),)
        ).fetchone()
        attempts = fields.pop("attempts", (old[0] if old else 0) + (1 if status == "RUNNING" else 0))
        error_code = fields.pop("error_code", None)
        self.conn.execute(
            f"""INSERT INTO {table}(work_id,status,attempts,error_code,updated_at,payload_json) VALUES (?,?,?,?,?,?) ON CONFLICT(work_id) DO UPDATE SET
            status=excluded.status,attempts=excluded.attempts,error_code=excluded.error_code,updated_at=excluded.updated_at,payload_json=excluded.payload_json""",
            (work_id(identifier), status, attempts, error_code, _now(), _dump(fields)),
        )

    def enqueue_retry(
        self, identifier: str, stage: str, code: str, attempt: int = 0, next_retry_at: str | None = None
    ) -> int:
        timestamp = _now()
        self.conn.execute(
            """INSERT INTO retry_queue(work_id,stage,code,attempts,next_retry_at,status,created_at,updated_at) VALUES (?,?,?,?,?,'PENDING',?,?)
            ON CONFLICT(work_id,stage) DO UPDATE SET code=excluded.code,attempts=MAX(retry_queue.attempts,excluded.attempts),
            next_retry_at=excluded.next_retry_at,status='PENDING',updated_at=excluded.updated_at""",
            (work_id(identifier), stage, code, attempt, next_retry_at, timestamp, timestamp),
        )
        return self.conn.execute(
            "SELECT id FROM retry_queue WHERE work_id=? AND stage=?", (work_id(identifier), stage)
        ).fetchone()[0]

    def pending_retries(self, failed_only: bool = False, max_attempts: int = 3) -> list[dict]:
        statuses = ("PENDING", "FAILED") if failed_only else ("PENDING", "FAILED", "RUNNING")
        marks = ",".join("?" for _ in statuses)
        return [
            dict(row)
            for row in self.conn.execute(
                f"SELECT * FROM retry_queue WHERE status IN ({marks}) AND attempts<? AND (next_retry_at IS NULL OR next_retry_at<=?) ORDER BY id",
                (*statuses, max_attempts, _now()),
            )
        ]

    def update_retry(
        self,
        retry_id: int,
        status: str,
        attempts: int | None = None,
        next_retry_at: str | None = None,
        last_error: str | None = None,
    ) -> None:
        self.conn.execute(
            "UPDATE retry_queue SET status=?,attempts=COALESCE(?,attempts),next_retry_at=?,last_error=?,updated_at=? WHERE id=?",
            (status, attempts, next_retry_at, _safe(last_error) if last_error else None, _now(), retry_id),
        )

    def begin_crawl(self, query: dict, resume: bool = True) -> dict:
        serialized = _dump(query)
        digest = hashlib.sha256(serialized.encode()).hexdigest()
        with self.transaction():
            row = (
                self.conn.execute(
                    "SELECT id FROM crawl_runs WHERE query_hash=? AND status NOT IN ('COMPLETED','COMPLETE') ORDER BY started_at DESC LIMIT 1",
                    (digest,),
                ).fetchone()
                if resume
                else None
            )
            if row:
                run_id = row[0]
                self.conn.execute(
                    "UPDATE crawl_runs SET status='RUNNING',updated_at=? WHERE id=?", (_now(), run_id)
                )
            else:
                run_id = uuid.uuid4().hex
                timestamp = _now()
                self.conn.execute(
                    "INSERT INTO crawl_runs VALUES (?,?,?,'RUNNING',?,?,NULL)",
                    (run_id, digest, serialized, timestamp, timestamp),
                )
                self.conn.execute(
                    "INSERT INTO crawl_checkpoints VALUES (?,'*',0,0,NULL,'RUNNING','{}',?)",
                    (run_id, timestamp),
                )
        return self.get_checkpoint(run_id)

    def get_checkpoint(self, run_id: str) -> dict:
        row = self.conn.execute("SELECT * FROM crawl_checkpoints WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        result = dict(row)
        result["id"] = run_id
        result["extras"] = json.loads(result.pop("extras_json"))
        return result

    def save_checkpoint(
        self,
        run_id: str,
        cursor: str | None,
        offset: int,
        processed: int,
        total_count: int | None,
        status: str = "RUNNING",
        **extras: Any,
    ) -> None:
        with self.transaction():
            previous = self.get_checkpoint(run_id)
            combined = {**previous["extras"], **extras}
            timestamp = _now()
            self.conn.execute(
                "UPDATE crawl_checkpoints SET cursor=?,offset=?,processed=?,total_count=?,status=?,extras_json=?,updated_at=? WHERE run_id=?",
                (cursor, offset, processed, total_count, status, _dump(combined), timestamp, run_id),
            )
            self.conn.execute(
                "UPDATE crawl_runs SET status=?,updated_at=? WHERE id=?", (status, timestamp, run_id)
            )

    def finish_crawl(self, run_id: str, status: str) -> None:
        with self.transaction():
            timestamp = _now()
            completed_at = timestamp if status in ("COMPLETED", "COMPLETE") else None
            self.conn.execute(
                "UPDATE crawl_runs SET status=?,updated_at=?,completed_at=? WHERE id=?",
                (status, timestamp, completed_at, run_id),
            )
            self.conn.execute(
                "UPDATE crawl_checkpoints SET status=?,updated_at=? WHERE run_id=?",
                (status, timestamp, run_id),
            )

    def record_crawl_work(self, run_id: str, identifier: str) -> bool:
        return (
            self.conn.execute(
                "INSERT OR IGNORE INTO crawl_work_ids VALUES (?,?,?)", (run_id, work_id(identifier), _now())
            ).rowcount
            == 1
        )

    def stats(self) -> dict:
        def scalar(sql, params=()):
            return self.conn.execute(sql, params).fetchone()[0]

        total = scalar("SELECT COUNT(*) FROM works")
        statuses = {state.value: 0 for state in WorkStatus}
        statuses.update(
            {row[0]: row[1] for row in self.conn.execute("SELECT status,COUNT(*) FROM works GROUP BY status")}
        )
        download_attempted = scalar("SELECT COUNT(*) FROM download_jobs")
        download_success = scalar(
            "SELECT COUNT(*) FROM download_jobs WHERE status IN ('DOWNLOADED','REUSED','COMPLETE','COMPLETED','SUCCESS')"
        )
        # Mirror the pipeline's non-regression promotion rule: the latest run at
        # the highest achieved gate rank represents preserved usable extraction.
        latest = """SELECT d.id FROM documents d JOIN extraction_runs e ON e.id=d.extraction_run_id
            WHERE e.rowid=(SELECT e2.rowid FROM extraction_runs e2 WHERE e2.work_id=e.work_id ORDER BY
                CASE e2.status WHEN 'COMPLETE' THEN 3 WHEN 'COMPLETE_WITH_WARNINGS' THEN 2 WHEN 'PARTIAL' THEN 1 ELSE 0 END DESC,
                e2.completed_at DESC,e2.rowid DESC LIMIT 1)"""
        counts = {
            table: scalar(f"SELECT COUNT(*) FROM {table} WHERE document_id IN ({latest})")
            for table in ("tables", "figures", "equations", "extracted_references")
        }
        candidate_groups = {
            table: scalar(f"""SELECT COUNT(*) FROM (SELECT DISTINCT document_id,
                COALESCE(json_extract(payload_json,'$.candidate_group_id'),'record:'||id) AS candidate_group
                FROM {table} WHERE document_id IN ({latest}))""")
            for table in ("tables", "figures")
        }
        figure_kinds = {
            row[0]: row[1]
            for row in self.conn.execute(f"""SELECT
            COALESCE(json_extract(payload_json,'$.kind'),'unspecified'),COUNT(*) FROM figures
            WHERE document_id IN ({latest}) GROUP BY COALESCE(json_extract(payload_json,'$.kind'),'unspecified')""")
        }
        return {
            "works_total": total,
            "metadata_success": total,
            "discovered_unique_works": scalar("SELECT COUNT(DISTINCT work_id) FROM crawl_work_ids"),
            "discovered_occurrences": scalar("SELECT COUNT(*) FROM crawl_work_ids"),
            "reported_search_totals": [
                {"run_id": row[0], "total_count": row[1]}
                for row in self.conn.execute("SELECT run_id,total_count FROM crawl_checkpoints")
            ],
            "duplicate_doi_groups": scalar(
                "SELECT COUNT(*) FROM (SELECT doi FROM works WHERE doi IS NOT NULL GROUP BY doi HAVING COUNT(*)>1)"
            ),
            "duplicate_doi_works": scalar(
                "SELECT COALESCE(SUM(n),0) FROM (SELECT COUNT(*) n FROM works WHERE doi IS NOT NULL GROUP BY doi HAVING COUNT(*)>1)"
            ),
            "download_attempted": download_attempted,
            "download_success": download_success,
            "download_failed": scalar(
                "SELECT COUNT(*) FROM download_jobs WHERE status NOT IN ('DOWNLOADED','REUSED','COMPLETE','COMPLETED','SUCCESS','PDF_NOT_AVAILABLE','PENDING','RUNNING') AND COALESCE(error_code,'')<>'PDF_NOT_AVAILABLE'"
            ),
            "pdf_not_provided": scalar(
                "SELECT COUNT(*) FROM download_jobs WHERE status='PDF_NOT_AVAILABLE' OR error_code='PDF_NOT_AVAILABLE'"
            ),
            "markdown_success": scalar(
                "SELECT COUNT(DISTINCT work_id) FROM extraction_runs WHERE status IN ('COMPLETE','COMPLETE_WITH_WARNINGS')"
            ),
            "partial_parsing": statuses["PARTIAL"],
            "parsing_failed": scalar("SELECT COUNT(*) FROM parse_jobs WHERE status='FAILED'"),
            "pages_needing_review": scalar(f"""SELECT COUNT(*) FROM pages WHERE document_id IN ({latest}) AND
                (status NOT IN ('COMPLETE','complete','OK','ok','blank','BLANK','parsed','success','extracted')
                OR COALESCE(json_array_length(json_extract(payload_json,'$.missing_regions')),0)>0
                OR COALESCE(json_array_length(json_extract(payload_json,'$.validation_messages')),0)>0)"""),
            "extracted": counts,
            "candidate_groups": candidate_groups,
            "figure_kinds": figure_kinds,
            "status_counts": statuses,
            "status_total_consistent": sum(statuses.values()) == total,
            "missing_reasons": {
                row[0]: row[1]
                for row in self.conn.execute(
                    "SELECT missing_reason,COUNT(*) FROM works WHERE missing_reason IS NOT NULL GROUP BY missing_reason"
                )
            },
            "retry_pending": scalar(
                "SELECT COUNT(*) FROM retry_queue WHERE status IN ('PENDING','RUNNING','FAILED')"
            ),
            "complete_rate": {
                "numerator": statuses["COMPLETE"],
                "denominator": total,
                "percent": round(100 * statuses["COMPLETE"] / total, 2) if total else None,
            },
            "download_success_rate": {
                "numerator": download_success,
                "denominator": download_attempted,
                "percent": round(100 * download_success / download_attempted, 2)
                if download_attempted
                else None,
            },
            "definitions": {
                "works_total": "Unique collected OpenAlex Work IDs; every work has exactly one current status.",
                "reported_search_totals": "Per-query API result counts, overlapping and not summed into a unique corpus size.",
                "download_success_rate": "Latest successful or reused download jobs / all works with a download job (including unavailable PDFs).",
                "complete_rate": "Current COMPLETE works / all collected works, including metadata-only records.",
                "extracted": "Elements in the newest run at the highest achieved quality rank per Work ID; source candidates may overlap. Mirrors the pipeline non-regression promotion rule.",
                "candidate_groups": "Distinct parser-assigned candidate_group_id values within each chosen document; ungrouped records count individually. This is not a verified count of physical tables or figures.",
                "figure_kinds": "Parser-labelled image objects and semantic figure-region candidates are counted separately; categories may depict the same source content.",
                "markdown_success": "Works with an extraction run passing COMPLETE or COMPLETE_WITH_WARNINGS; integrity is checked separately by verify.",
            },
        }

    def integrity_check(self) -> dict:
        integrity = [row[0] for row in self.conn.execute("PRAGMA integrity_check")]
        foreign_keys = [tuple(row) for row in self.conn.execute("PRAGMA foreign_key_check")]
        return {
            "ok": integrity == ["ok"] and not foreign_keys,
            "integrity_check": integrity,
            "foreign_key_violations": foreign_keys,
        }

    def backup(self, path: str | Path) -> Path:
        path = Path(path).resolve()
        if path == self.db_path:
            raise ValueError("Backup destination must differ from the live database")
        if path.exists():
            raise FileExistsError("Refusing to overwrite an existing database backup")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            with sqlite3.connect(temporary) as target:
                self.conn.backup(target)
                if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("Backup integrity check failed")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return path
