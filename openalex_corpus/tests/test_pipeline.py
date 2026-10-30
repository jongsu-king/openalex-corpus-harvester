"""Filesystem + SQLite + real mocked HTTP integration. Never uses the network."""

import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
import pymupdf
import pytest

from openalex_corpus.api.client import APIError
from openalex_corpus.api.search import SearchSpec
from openalex_corpus.config import DownloadSettings, Settings
from openalex_corpus.download.pdf_downloader import PDFDownloader
from openalex_corpus.pipeline import Pipeline


def work(number, url=None):
    result = {
        "id": f"https://openalex.org/W{number}",
        "title": f"Paper {number}",
        "abstract_inverted_index": None,
        "unexpected_source_extension": {"empty": "", "null": None},
    }
    if url:
        result["best_oa_location"] = {
            "pdf_url": url,
            "is_oa": True,
            "license": "cc-by",
            "version": "publishedVersion",
        }
    return result


def page(rows, cursor=None, count=None):
    return {"meta": {"next_cursor": cursor, "count": len(rows) if count is None else count}, "results": rows}


class FakeClient:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    async def get_page(self, params, cursor="*"):
        self.calls.append(cursor)
        result = self.pages[cursor]
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture
def pipeline(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        download=DownloadSettings(concurrency=1, max_attempts=1),
    )
    result = Pipeline(settings)
    yield result
    result.close()


def test_midpage_limit_resume_has_no_skipped_work(pipeline):
    client = FakeClient({"*": page([work(1), work(2), work(3)], "next", 4), "next": page([work(4)], None, 4)})

    async def run():
        first = await pipeline.harvest(SearchSpec(query="fixture"), max_works=1, client=client)
        assert first["status"] == "LIMIT_REACHED"
        checkpoint = pipeline.repo.get_checkpoint(first["run_id"])
        assert checkpoint["cursor"] == "*" and checkpoint["offset"] == 1
        second = await pipeline.harvest(SearchSpec(query="fixture"), max_works=10, client=client)
        assert second["run_id"] == first["run_id"]
        assert second["status"] == "COMPLETED"
        assert second["processed_total"] == 4
        assert second["processed_this_run"] == 3

    asyncio.run(run())
    assert client.calls == ["*", "next"]  # original response replayed from disk
    assert {row["id"] for row in pipeline.repo.iter_works()} == {"W1", "W2", "W3", "W4"}
    assert pipeline.repo.integrity_check()["ok"]
    raw = json.loads((pipeline.paper_dir("W1") / "metadata_raw.json").read_text())
    assert raw["unexpected_source_extension"] == {"empty": "", "null": None}


def test_rerun_idempotence_preserves_single_revision(pipeline):
    client = FakeClient({"*": page([work(1), work(2)])})

    async def run():
        await pipeline.harvest(SearchSpec(query="fixture"), client=client)
        await pipeline.harvest(SearchSpec(query="fixture"), client=client)

    asyncio.run(run())
    assert pipeline.repo.conn.execute("SELECT COUNT(*) FROM works").fetchone()[0] == 2
    assert pipeline.repo.conn.execute("SELECT COUNT(*) FROM metadata_revisions").fetchone()[0] == 2
    assert len(list(pipeline.paper_dir("W1").glob("metadata_history/*.json"))) == 1


def test_terminal_empty_page_is_successfully_checkpointed(pipeline):
    client = FakeClient({"*": page([work(1)], "end", 1), "end": page([], None, 1)})
    result = asyncio.run(pipeline.harvest(SearchSpec(query="fixture"), client=client))
    assert result["status"] == "COMPLETED"
    assert client.calls == ["*", "end"]
    checkpoint = pipeline.repo.get_checkpoint(result["run_id"])
    assert checkpoint["processed"] == 1 and checkpoint["status"] == "COMPLETED"


def test_failed_page_keeps_cursor_and_resumes(pipeline):
    client = FakeClient(
        {"*": page([work(1)], "next", 2), "next": APIError("transport failed", "API_TIMEOUT", True)}
    )

    async def run():
        result = await pipeline.harvest(SearchSpec(query="fixture"), client=client)
        assert result["status"] == "PAUSED"
        checkpoint = pipeline.repo.get_checkpoint(result["run_id"])
        assert checkpoint["cursor"] == "next" and checkpoint["offset"] == 0
        assert checkpoint["processed"] == 1
        client.pages["next"] = page([work(2)], None, 2)
        resumed = await pipeline.harvest(SearchSpec(query="fixture"), client=client)
        assert resumed["status"] == "COMPLETED" and resumed["processed_total"] == 2

    asyncio.run(run())
    assert client.calls == ["*", "next", "next"]
    errors = pipeline.repo.conn.execute("SELECT code FROM errors WHERE stage='crawl'").fetchall()
    assert any(row[0] == "API_TIMEOUT" for row in errors)


def test_crash_after_ingest_before_checkpoint_is_idempotent(pipeline, monkeypatch):
    client = FakeClient({"*": page([work(1), work(2)])})
    original_process = pipeline.process_work
    fail_once = True

    async def injected(raw, *args, **kwargs):
        nonlocal fail_once
        result = await original_process(raw, *args, **kwargs)
        if raw["id"].endswith("W2") and fail_once:
            fail_once = False
            raise RuntimeError("simulated crash after commit, before checkpoint")
        return result

    monkeypatch.setattr(pipeline, "process_work", injected)

    async def run():
        first = await pipeline.harvest(SearchSpec(query="fixture"), client=client)
        assert first["status"] == "PAUSED"
        assert pipeline.repo.get_checkpoint(first["run_id"])["offset"] == 1
        second = await pipeline.harvest(SearchSpec(query="fixture"), client=client)
        assert second["status"] == "COMPLETED"

    asyncio.run(run())
    assert pipeline.repo.conn.execute("SELECT COUNT(*) FROM works").fetchone()[0] == 2
    assert pipeline.repo.conn.execute("SELECT COUNT(*) FROM metadata_revisions").fetchone()[0] == 2
    assert client.calls == ["*"]


def small_pdf():
    document = pymupdf.open()
    document.new_page().insert_text((50, 50), "Corpus integration fixture")
    data = document.tobytes()
    document.close()
    return data


def test_pipeline_download_cache_and_database_artifacts(pipeline):
    data = small_pdf()
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, content=data, headers={"Content-Type": "application/pdf"})

    client = FakeClient({"*": page([work(1, "https://repository.example/paper.pdf")])})

    async def run():
        async with PDFDownloader(
            pipeline.settings.download,
            transport=httpx.MockTransport(handler),
            state_dir=pipeline.settings.data_dir / "state",
        ) as downloader:
            first = await pipeline.harvest(
                SearchSpec(query="fixture"), client=client, download_pdf=True, downloader=downloader
            )
            second = await pipeline.harvest(
                SearchSpec(query="fixture"), client=client, download_pdf=True, downloader=downloader
            )
            assert first["outcomes"][0]["download_status"] == "DOWNLOADED"
            assert second["outcomes"][0]["download_status"] == "REUSED"

    asyncio.run(run())
    assert len(calls) == 1
    assert pipeline.repo.conn.execute("SELECT COUNT(*) FROM artifacts WHERE kind='pdf'").fetchone()[0] == 1
    assert pipeline.repo.get_work("W1")["status"] == "PARTIAL"
    assert pipeline.verify()["valid"]


def test_empty_nonterminal_page_is_not_claimed_complete(pipeline):
    client = FakeClient({"*": page([], "unexpected", 5)})
    result = asyncio.run(pipeline.harvest(SearchSpec(query="fixture"), client=client))
    assert result["status"] == "PAUSED"
    assert pipeline.repo.get_checkpoint(result["run_id"])["status"] == "PAUSED"


def test_empty_search_rejected_without_network(pipeline):
    client = FakeClient({})
    with pytest.raises(ValueError, match="search|filter|Snapshot"):
        asyncio.run(pipeline.harvest(SearchSpec(), client=client))
    assert client.calls == []


def test_tampered_cached_page_is_detected_on_resume(pipeline):
    client = FakeClient({"*": page([work(1), work(2)])})
    first = asyncio.run(pipeline.harvest(SearchSpec(query="fixture"), max_works=1, client=client))
    checkpoint = pipeline.repo.get_checkpoint(first["run_id"])
    from pathlib import Path

    path = Path(checkpoint["extras"]["page_path"])
    path.write_text(json.dumps(page([work(1), work(99)])))
    second = asyncio.run(pipeline.harvest(SearchSpec(query="fixture"), client=client))
    assert second["status"] == "PAUSED"
    assert pipeline.repo.get_work("W99") is None


def test_retry_after_is_preserved_in_download_queue(pipeline):
    when = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()

    class LimitedDownloader:
        async def download(self, raw, directory):
            return {"status": "RATE_LIMITED", "attempts": [], "retryable": True, "retry_at": when}

    asyncio.run(pipeline.process_work(work(1), LimitedDownloader()))
    row = pipeline.repo.conn.execute("SELECT next_retry_at FROM retry_queue WHERE work_id='W1'").fetchone()
    assert datetime.fromisoformat(row[0]) >= datetime.fromisoformat(when)


def test_successful_existing_extraction_not_downgraded_on_pdf_reuse(pipeline):
    data = small_pdf()
    item = work(1, "https://repository.example/paper.pdf")

    async def run():
        async with PDFDownloader(
            pipeline.settings.download,
            transport=httpx.MockTransport(lambda r: httpx.Response(200, content=data)),
            state_dir=pipeline.settings.data_dir / "state",
        ) as downloader:
            await pipeline.process_work(item, downloader)
            report = json.loads((pipeline.paper_dir("W1") / "download_report.json").read_text())
            # This is the durable selection boundary written after a successful extraction.
            (pipeline.paper_dir("W1") / "current.json").write_text(
                json.dumps(
                    {
                        "pdf_sha256": report["sha256"],
                        "status": "COMPLETE_WITH_WARNINGS",
                        "run": "extractions/previous",
                    }
                )
            )
            pipeline.repo.set_work_status("W1", "COMPLETE_WITH_WARNINGS")
            await pipeline.process_work(item, downloader)

    asyncio.run(run())
    assert pipeline.repo.get_work("W1")["status"] == "COMPLETE_WITH_WARNINGS"


def test_original_pdf_tamper_is_detected_even_without_parse(pipeline):
    data = small_pdf()

    async def run():
        async with PDFDownloader(
            pipeline.settings.download,
            transport=httpx.MockTransport(lambda r: httpx.Response(200, content=data)),
            state_dir=pipeline.settings.data_dir / "state",
        ) as downloader:
            await pipeline.process_work(work(1, "https://repository.example/paper.pdf"), downloader)

    asyncio.run(run())
    (pipeline.paper_dir("W1") / "original.pdf").write_bytes(b"tampered original")
    result = pipeline.verify()
    assert not result["valid"]
    assert any(
        issue["code"] in {"CHECKSUM_MISMATCH", "ORIGINAL_PDF_CHANGED", "INVALID_PDF"}
        for issue in result["works"][0]["errors"]
    )
