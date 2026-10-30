import pytest

from openalex_corpus.config import Settings
from openalex_corpus.opv import run_inventory


async def test_download_cap_does_not_reset_across_inventory_chunks(tmp_path, monkeypatch):
    created = []

    class Downloader:
        def __init__(self, settings, **kwargs):
            self.limit = settings.max_downloads
            self.network_attempts = 0
            created.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def download(self, raw, directory):
            if self.network_attempts >= self.limit:
                return {"status": "RATE_LIMITED", "retryable": True, "attempts": []}
            self.network_attempts += 1
            return {"status": "PDF_NOT_AVAILABLE", "retryable": False, "attempts": []}

    class Client:
        async def get_page(self, params, cursor="*"):
            index = 1 if cursor == "*" else 2
            return {
                "meta": {"count": 2, "next_cursor": "next" if index == 1 else None},
                "results": [{"id": f"https://openalex.org/W{index}", "title": "OPV source"}],
            }

    monkeypatch.setattr("openalex_corpus.download.pdf_downloader.PDFDownloader", Downloader)
    settings = Settings(
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        search={"query": '"organic solar cells"', "scope": "title_abstract_keywords", "exact": True},
        download={"max_downloads": 1},
    )
    result = await run_inventory(settings, client=Client(), all_matches=True, chunk_size=1, download_pdf=True)
    assert result["unique_collected_for_query"] == 2
    assert len(created) == 1
    assert sum(item.network_attempts for item in created) == 1
    assert result["corpus_stats"]["status_counts"]["PENDING_RETRY"] == 1
    assert result["query_exhausted"] is True
    assert result["metadata_status"] == "COMPLETE_QUERY_MATCHES"
    assert result["status"] == "QUERY_COMPLETE_PROCESSING_INCOMPLETE"
    assert result["requires_attention"] is True
    assert result["requested_processing"]["failed_or_pending_works"] == 1
    assert result["requested_processing"]["pdf_unavailable_works"] == 1


@pytest.mark.parametrize(
    ("download_status", "needs_attention"),
    [
        ("PDF_NOT_AVAILABLE", False),
        ("INVALID_PDF", True),
        ("DOWNLOAD_FAILED", True),
        ("ACCESS_RESTRICTED", True),
        ("RATE_LIMITED", True),
    ],
)
async def test_terminal_query_distinguishes_unavailable_pdf_from_nonretryable_failure(
    tmp_path, download_status, needs_attention
):
    class Client:
        async def get_page(self, params, cursor="*"):
            return {
                "meta": {"count": 1, "next_cursor": None},
                "results": [{"id": "https://openalex.org/W1", "title": "OPV source"}],
            }

    class Downloader:
        async def download(self, raw, directory):
            return {"status": download_status, "retryable": False, "attempts": []}

    settings = Settings(
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        search={"query": '"organic solar cells"', "scope": "title_abstract_keywords", "exact": True},
    )
    report = await run_inventory(
        settings, client=Client(), downloader=Downloader(), all_matches=True, download_pdf=True
    )
    assert report["query_exhausted"] is True
    assert report["metadata_status"] == "COMPLETE_QUERY_MATCHES"
    assert report["corpus_stats"]["status_counts"]["PDF_UNAVAILABLE"] == 1
    assert report["requires_attention"] is needs_attention
    assert report["status"] == (
        "QUERY_COMPLETE_PROCESSING_INCOMPLETE" if needs_attention else "COMPLETE_QUERY_MATCHES"
    )
    assert report["requested_processing"]["failed_or_pending_works"] == int(needs_attention)
    assert report["requested_processing"]["pdf_unavailable_works"] == int(not needs_attention)
    assert report["requested_processing"]["download_error_counts"] == {download_status: 1}
