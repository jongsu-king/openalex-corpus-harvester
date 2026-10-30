import json
import sqlite3
from copy import deepcopy

import pytest

from openalex_corpus.database import Repository
from openalex_corpus.metadata import normalize_work


@pytest.fixture
def repo(tmp_path):
    with Repository(tmp_path / "corpus.sqlite") as repository:
        yield repository


def raw_work(identifier="W1", doi="https://doi.org/10.1000/Example"):
    return {
        "id": "https://openalex.org/" + identifier,
        "doi": doi,
        "title": "A real source title",
        "publication_year": 2024,
        "authorships": [
            {
                "author": {"id": "https://openalex.org/A1", "display_name": "First Author", "orcid": None},
                "author_position": "first",
                "is_corresponding": False,
                "institutions": [
                    {
                        "id": "https://openalex.org/I1",
                        "display_name": "One",
                        "ror": "https://ror.org/01",
                        "country_code": "KR",
                    },
                    {"id": "https://openalex.org/I2", "display_name": "Two"},
                ],
            },
            {
                "author": {"id": "https://openalex.org/A2", "display_name": "Last Author"},
                "author_position": "last",
                "institutions": [],
            },
        ],
        "locations": [
            {
                "source": {"id": "https://openalex.org/S1", "display_name": "Journal", "issn_l": "1234-5678"},
                "license": "cc-by",
                "version": "publishedVersion",
                "pdf_url": "https://example.org/a.pdf",
                "is_oa": True,
            }
        ],
        "topics": [{"id": "https://openalex.org/T1", "display_name": "Topic", "score": 0.9}],
        "referenced_works": ["https://openalex.org/W100", "https://openalex.org/W101"],
        "unrecognized_payload": {"nested": [None, {}, "π"]},
    }


def ingest(repo, tmp_path, raw=None):
    raw = raw or raw_work()
    return repo.ingest_work(
        raw, normalize_work(raw), tmp_path / raw["id"].rsplit("/", 1)[-1] / "metadata_raw.json"
    )


def test_schema_all_required_tables_wal_foreign_keys(repo):
    names = {r[0] for r in repo.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {
        "works",
        "authors",
        "work_authors",
        "institutions",
        "work_institutions",
        "sources",
        "topics",
        "work_topics",
        "identifiers",
        "work_locations",
        "references",
        "work_references",
        "artifacts",
        "documents",
        "pages",
        "text_blocks",
        "tables",
        "table_cells",
        "figures",
        "equations",
        "extracted_references",
        "extraction_runs",
        "validation_results",
        "download_jobs",
        "parse_jobs",
        "crawl_runs",
        "crawl_checkpoints",
        "errors",
        "retry_queue",
        "file_checksums",
    } <= names
    assert repo.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert repo.conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert repo.integrity_check()["ok"]
    with pytest.raises(sqlite3.IntegrityError):
        repo.conn.execute("INSERT INTO work_authors VALUES ('W404',0,NULL,NULL,NULL,'{}')")


def test_idempotent_ingest_unknown_fields_author_order_and_affiliations(repo, tmp_path):
    raw = raw_work()
    assert ingest(repo, tmp_path, raw)["is_new"] is True
    repeated = ingest(repo, tmp_path, raw)
    assert repeated["is_new"] is False
    assert repeated["changed"] is False
    assert repo.get_work("W1")["raw"] == raw
    assert repo.get_work("W1")["metadata"]["unrecognized_payload"] == raw["unrecognized_payload"]
    assert [
        tuple(r)
        for r in repo.conn.execute("SELECT author_index,author_id FROM work_authors ORDER BY author_index")
    ] == [(0, "A1"), (1, "A2")]
    assert repo.conn.execute("SELECT COUNT(*) FROM work_institutions").fetchone()[0] == 2
    assert repo.conn.execute("SELECT COUNT(*) FROM metadata_revisions").fetchone()[0] == 1
    assert repo.conn.execute("SELECT COUNT(*) FROM work_references").fetchone()[0] == 2


def test_duplicate_doi_flags_do_not_merge_works(repo, tmp_path):
    ingest(repo, tmp_path)
    duplicate = ingest(repo, tmp_path, raw_work("W2", "10.1000/EXAMPLE"))
    assert duplicate["duplicate_doi_ids"] == ["W1"]
    assert repo.stats()["works_total"] == 2
    assert repo.stats()["duplicate_doi_groups"] == 1


def test_metadata_revision_history_is_immutable(repo, tmp_path):
    old = raw_work()
    ingest(repo, tmp_path, old)
    newer = deepcopy(old)
    newer["title"] = "Source corrected its title"
    newer["new_future_field"] = 7
    assert ingest(repo, tmp_path, newer)["changed"] is True
    history = [
        json.loads(r[0]) for r in repo.conn.execute("SELECT raw_json FROM metadata_revisions ORDER BY id")
    ]
    assert history == [old, newer]
    assert repo.get_work("W1")["raw"] == newer


def test_paper_dir_uses_work_directory_for_archived_raw_path(repo, tmp_path):
    raw = raw_work()
    path = tmp_path / "papers" / "W1" / "metadata_history" / "hash.json"
    repo.ingest_work(raw, normalize_work(raw), path)
    assert repo.get_work("W1")["paper_dir"] == str(path.parents[1])


def test_transaction_rollback_keeps_previous_raw_and_relations(repo, tmp_path):
    raw = raw_work()
    ingest(repo, tmp_path, raw)
    bad = deepcopy(raw)
    bad["title"] = "Must roll back"
    bad["authorships"] = [None]
    with pytest.raises(AttributeError):
        ingest(repo, tmp_path, bad)
    assert repo.get_work("W1")["raw"] == raw
    assert repo.conn.execute("SELECT COUNT(*) FROM work_authors").fetchone()[0] == 2


def test_cursor_checkpoint_resume_and_new_run_after_completion(repo, tmp_path):
    ingest(repo, tmp_path)
    run = repo.begin_crawl({"query": "energy", "per_page": 100})
    assert run["cursor"] == "*" and run["offset"] == 0
    assert repo.record_crawl_work(run["id"], "W1") is True
    assert repo.record_crawl_work(run["id"], "W1") is False
    repo.save_checkpoint(run["id"], "cursor-two", 8, 108, 1200, status="PAUSED", response_sha256="abc")
    resumed = repo.begin_crawl({"query": "energy", "per_page": 100})
    assert resumed["id"] == run["id"]
    assert (resumed["cursor"], resumed["offset"], resumed["processed"]) == ("cursor-two", 8, 108)
    assert resumed["extras"]["response_sha256"] == "abc"
    repo.finish_crawl(run["id"], "COMPLETED")
    assert repo.begin_crawl({"query": "energy", "per_page": 100})["id"] != run["id"]


def test_crash_reopen_and_backup(repo, tmp_path):
    ingest(repo, tmp_path)
    checkpoint = repo.begin_crawl({"title": "A"})
    repo.save_checkpoint(checkpoint["id"], "abc", 3, 3, 20)
    backup = repo.backup(tmp_path / "backup.sqlite")
    with Repository(backup) as recovered:
        assert recovered.integrity_check()["ok"]
        assert recovered.get_checkpoint(checkpoint["id"])["offset"] == 3
        assert recovered.get_work("W1")["doi"] == "10.1000/example"
    with pytest.raises(FileExistsError):
        repo.backup(backup)


def test_pdf_versions_and_provenance_are_not_overwritten(repo, tmp_path):
    ingest(repo, tmp_path)
    first = repo.upsert_artifact(
        "W1", "pdf", tmp_path / "v1.pdf", "hash1", {"version": "acceptedVersion", "license": "cc-by"}
    )
    second = repo.upsert_artifact("W1", "pdf", tmp_path / "v2.pdf", "hash2", {"version": "publishedVersion"})
    assert first != second
    assert repo.conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 2
    assert repo.conn.execute("SELECT COUNT(*) FROM file_checksums").fetchone()[0] == 2


def test_extraction_stores_pages_cells_and_uncertain_candidates(repo, tmp_path):
    ingest(repo, tmp_path)
    result = {
        "engine": "pymupdf",
        "parser_versions": {"pymupdf": "test"},
        "status": "PARTIAL",
        "pages": [
            {"page_number": 1, "status": "parsed", "char_count": 20, "ocr_used": False},
            {"page_number": 2, "status": "failed", "char_count": 0},
        ],
        "text_blocks": [
            {"page_number": 1, "text": "Observed text", "bbox": [1, 2, 3, 4], "source": "pdf_extracted"}
        ],
        "tables": [{"page_number": 1, "rows": [["a", None], ["b", "4"]], "source": "pdfplumber"}],
        "figures": [{"page_number": 1, "image_file": "figures/f1.png", "source": "image_object"}],
        "equations": [
            {"page_number": 1, "status": "equation_parse_failed", "text": "x=y", "bbox": [1, 2, 3, 4]}
        ],
        "references": [
            {
                "original_text": "Observed reference",
                "matched_work_id": "W123",
                "match_evidence": {"verified": False},
            }
        ],
    }
    run_id = repo.record_extraction(
        "W1",
        result,
        tmp_path / "run1",
        {"status": "PARTIAL", "issues": [{"code": "page_failed", "page_number": 2}]},
    )
    assert run_id
    assert repo.conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0] == 2
    assert [
        r[0] for r in repo.conn.execute("SELECT value_json FROM table_cells ORDER BY row_index,column_index")
    ] == ['"a"', "null", '"b"', '"4"']
    assert repo.conn.execute("SELECT matched_work_id FROM extracted_references").fetchone()[0] is None
    assert repo.stats()["extracted"]["tables"] == 1
    assert repo.stats()["pages_needing_review"] == 1


def test_retry_budget_and_error_redaction(repo, tmp_path, monkeypatch):
    ingest(repo, tmp_path)
    monkeypatch.setenv("OPENALEX_API_KEY", "super-secret")
    repo.record_error("W1", "download", "TIMEOUT", "URL api_key=super-secret&x=1", True)
    assert "super-secret" not in repo.conn.execute("SELECT message FROM errors").fetchone()[0]
    retry_id = repo.enqueue_retry("W1", "download", "TIMEOUT")
    assert len(repo.pending_retries()) == 1
    repo.update_retry(retry_id, "FAILED", attempts=3)
    assert repo.pending_retries(max_attempts=3) == []
    # Re-enqueuing must never reset an already consumed retry budget.
    repo.enqueue_retry("W1", "download", "TIMEOUT", attempt=0)
    assert repo.pending_retries(max_attempts=3) == []


def test_extraction_replay_is_idempotent_and_identity_conflicts_fail(repo, tmp_path):
    ingest(repo, tmp_path)
    ingest(repo, tmp_path, raw_work("W2", None))
    result = {"run_id": "stable-run", "status": "PARTIAL", "pages": [{"page_number": 1, "status": "blank"}]}
    quality = {"status": "PARTIAL"}
    first = repo.record_extraction("W1", result, tmp_path / "run", quality)
    assert repo.record_extraction("W1", result, tmp_path / "run", quality) == first
    assert repo.conn.execute("SELECT COUNT(*) FROM extraction_runs").fetchone()[0] == 1
    assert repo.conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0] == 1
    with pytest.raises(ValueError, match="different work"):
        repo.record_extraction("W2", result, tmp_path / "run", quality)
    with pytest.raises(ValueError, match="different work"):
        repo.record_extraction("W1", result, tmp_path / "elsewhere", quality)
    changed = {**result, "changed": True}
    with pytest.raises(ValueError, match="immutable"):
        repo.record_extraction("W1", changed, tmp_path / "run", quality)


def test_source_reported_unavailable_pdf_classification(repo, tmp_path):
    ingest(repo, tmp_path)
    repo.set_job("download", "W1", "FAILED", error_code="PDF_NOT_AVAILABLE")
    stats = repo.stats()
    assert stats["pdf_not_provided"] == 1
    assert stats["download_failed"] == 0


def test_failed_reparse_does_not_zero_preserved_extraction_statistics(repo, tmp_path):
    ingest(repo, tmp_path)
    repo.record_extraction(
        "W1",
        {
            "run_id": "good",
            "pages": [{"page_number": 1, "status": "parsed"}],
            "figures": [{"page_number": 1}],
        },
        tmp_path / "good",
        {"status": "COMPLETE_WITH_WARNINGS"},
    )
    repo.record_extraction("W1", {"run_id": "bad", "pages": []}, tmp_path / "bad", {"status": "FAILED"})
    assert repo.stats()["extracted"]["figures"] == 1
    assert repo.stats()["markdown_success"] == 1


def test_candidate_group_counts_and_page_review_flags(repo, tmp_path):
    ingest(repo, tmp_path)
    result = {
        "pages": [
            {"page_number": 1, "status": "extracted"},
            {"page_number": 2, "status": "extracted", "validation_messages": ["reading_order_unverified"]},
        ],
        "tables": [
            {"candidate_group_id": "p1_t1", "source": "docling"},
            {"candidate_group_id": "p1_t1", "source": "pdfplumber"},
            {"candidate_group_id": "p2_t1"},
        ],
        "figures": [{"kind": "image_object"}, {"kind": "figure_region"}],
    }
    repo.record_extraction("W1", result, tmp_path / "run", {"status": "COMPLETE_WITH_WARNINGS"})
    stats = repo.stats()
    assert stats["extracted"]["tables"] == 3
    assert stats["candidate_groups"]["tables"] == 2
    assert stats["candidate_groups"]["figures"] == 2
    assert stats["figure_kinds"] == {"image_object": 1, "figure_region": 1}
    assert stats["pages_needing_review"] == 1


def test_status_totals_and_percent_denominators(repo, tmp_path):
    ingest(repo, tmp_path)
    ingest(repo, tmp_path, raw_work("W2", None))
    repo.set_work_status("W1", "COMPLETE")
    repo.set_work_status("W2", "PDF_UNAVAILABLE", "PDF_NOT_AVAILABLE")
    repo.set_job("download", "W1", "DOWNLOADED", attempts=1)
    repo.set_job("download", "W2", "PDF_NOT_AVAILABLE", attempts=0)
    stats = repo.stats()
    assert stats["status_total_consistent"] is True
    assert stats["complete_rate"] == {"numerator": 1, "denominator": 2, "percent": 50.0}
    assert stats["download_success_rate"] == {"numerator": 1, "denominator": 2, "percent": 50.0}
    assert stats["missing_reasons"] == {"PDF_NOT_AVAILABLE": 1}
