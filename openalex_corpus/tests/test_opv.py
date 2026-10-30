from pathlib import Path

import pytest

from openalex_corpus.api.client import APIError
from openalex_corpus.api.search import SearchSpec
from openalex_corpus.config import Settings
from openalex_corpus.database.repository import Repository
from openalex_corpus.opv import load_api_key_file, run_inventory
from openalex_corpus.utils import atomic_json, sha256_file


def test_scoped_exact_search_is_not_sent_as_stemmed_wildcard():
    spec = SearchSpec(query='"organic photovoltaic*"', scope="title_abstract_keywords", exact=True)
    assert spec.params() == {
        "per_page": 100,
        "search.title_abstract_keywords.exact": '"organic photovoltaic*"',
    }
    with pytest.raises(ValueError):
        SearchSpec(query="one", title="two", scope="title").params()
    with pytest.raises(ValueError):
        SearchSpec(query="one", scope="invented_field")


def test_explicit_key_file_supports_bom_without_shell_or_interpolation(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    monkeypatch.setenv("INTERPOLATED", "do-not-use")
    key = "literal${INTERPOLATED}$(touch should-not-exist)"
    file = tmp_path / "private.env"
    file.write_text("OPENALEX_API_KEY='" + key + "'\n", encoding="utf-8-sig")
    load_api_key_file(file)
    import os

    assert os.environ["OPENALEX_API_KEY"] == key
    assert not Path("should-not-exist").exists()


def test_key_file_missing_or_placeholder_rejected_without_value(tmp_path):
    with pytest.raises(ValueError, match="does not exist"):
        load_api_key_file(tmp_path / "absent.env")
    file = tmp_path / "key.env"
    file.write_text("OPENALEX_API_KEY=YOUR_KEY\n")
    with pytest.raises(ValueError, match="placeholder"):
        load_api_key_file(file)


def settings(tmp_path):
    return Settings(
        data_dir=tmp_path / "corpus",
        log_dir=tmp_path / "logs",
        search={
            "query": '"organic solar cell" OR "polymer solar cell"',
            "scope": "title_abstract_keywords",
            "exact": True,
        },
    )


def work(index):
    return {
        "id": f"https://openalex.org/W{index}",
        "title": f"Source paper {index}",
        "unknown_source_field": [None, ""],
    }


class Client:
    last_rate_headers = {}

    def __init__(self):
        self.calls = []

    async def get_page(self, params, cursor="*"):
        self.calls.append(cursor)
        if cursor == "*":
            return {"meta": {"count": 4, "next_cursor": "next"}, "results": [work(1), work(2), work(3)]}
        assert cursor == "next"
        return {"meta": {"count": 4, "next_cursor": None}, "results": [work(4)]}


async def test_all_inventory_crosses_chunks_without_restarting_query(tmp_path):
    configured = settings(tmp_path)
    client = Client()
    report = await run_inventory(configured, all_matches=True, chunk_size=2, client=client)
    assert report["status"] == "COMPLETE_QUERY_MATCHES"
    assert report["unique_collected_for_query"] == 4
    assert report["metadata_coverage_percent"] == 100
    assert report["all_world_literature_complete"] is False
    assert client.calls == ["*", "next"]
    repeated = await run_inventory(configured, all_matches=True, client=client)
    assert repeated["reused_completed_inventory"]
    assert client.calls == ["*", "next"]


async def test_bounded_run_resumes_inside_retained_page(tmp_path):
    configured = settings(tmp_path)
    client = Client()
    first = await run_inventory(configured, max_works=2, chunk_size=2, client=client)
    assert first["status"] == "LIMIT_REACHED"
    assert first["retained_page_offset"] == 2
    assert first["unique_collected_for_query"] == 2
    resumed = await run_inventory(configured, all_matches=True, chunk_size=2, client=client)
    assert resumed["unique_collected_for_query"] == 4
    assert client.calls == ["*", "next"]
    with Repository(configured.db_path) as repository:
        assert repository.stats()["works_total"] == 4
        assert repository.get_work("W3")["raw"]["unknown_source_field"] == [None, ""]


async def test_keyless_seed_retains_first_page_then_pauses_without_requery(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    configured = settings(tmp_path)
    page_path = tmp_path / "seed.json"
    first = await Client().get_page({}, "*")
    atomic_json(page_path, first)
    atomic_json(
        page_path.with_suffix(".receipt.json"),
        {"params": SearchSpec(**configured.search).params(), "cursor": "*", "sha256": sha256_file(page_path)},
    )

    class MissingKey:
        async def get_page(self, params, cursor="*"):
            assert cursor == "next"
            raise APIError(
                "OpenAlex requires OPENALEX_API_KEY for cursor pages after the first page", "API_KEY_REQUIRED"
            )

    report = await run_inventory(configured, all_matches=True, seed_page=page_path, client=MissingKey())
    assert report["status"] == "PAUSED_API_KEY_REQUIRED"
    assert report["query_exhausted"] is False
    assert report["unique_collected_for_query"] == 3
    assert report["retained_cursor"] == "next"


async def test_seed_tamper_does_not_enter_corpus(tmp_path):
    configured = settings(tmp_path)
    page_path = tmp_path / "seed.json"
    atomic_json(page_path, await Client().get_page({}, "*"))
    atomic_json(
        page_path.with_suffix(".receipt.json"),
        {"params": SearchSpec(**configured.search).params(), "cursor": "*", "sha256": sha256_file(page_path)},
    )
    atomic_json(page_path, {"results": [], "meta": {"count": 0, "next_cursor": None}})
    with pytest.raises(ValueError, match="checksum"):
        await run_inventory(configured, seed_page=page_path, client=Client())
    with Repository(configured.db_path) as repository:
        assert repository.stats()["works_total"] == 0


async def test_mismatched_live_count_does_not_claim_complete_coverage(tmp_path):
    class ChangedCount:
        async def get_page(self, params, cursor="*"):
            return {"meta": {"count": 2, "next_cursor": None}, "results": [work(1)]}

    report = await run_inventory(settings(tmp_path), all_matches=True, client=ChangedCount())
    assert report["status"] == "COMPLETED_WITH_COUNT_MISMATCH_REVIEW"
    assert report["metadata_coverage_percent"] == 50
    assert report["all_world_literature_complete"] is False


async def test_completed_report_rechecks_current_stats_and_integrity(tmp_path, monkeypatch):
    configured = settings(tmp_path)
    client = Client()
    report = await run_inventory(configured, all_matches=True, client=client)
    assert report["database_integrity"]["ok"] is True
    with Repository(configured.db_path) as repository:
        repository.set_work_status("W1", "PENDING_RETRY", "test_failure")
    monkeypatch.setattr(
        Repository, "integrity_check", lambda self: {"ok": False, "integrity_check": ["test"]}
    )
    repeated = await run_inventory(configured, all_matches=True, client=client)
    assert repeated["reused_completed_inventory"] is True
    assert repeated["status"] == "FAILED_DATABASE_INTEGRITY"
    assert repeated["requires_attention"] is True
    assert repeated["corpus_stats"]["status_counts"]["PENDING_RETRY"] == 1
    assert client.calls == ["*", "next"]
