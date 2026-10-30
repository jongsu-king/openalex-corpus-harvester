"""Mock HTTP downloads plus real PyMuPDF validation; no external services."""

import asyncio
import hashlib
import json

import httpx
import pymupdf
import pytest

from openalex_corpus.config import DownloadSettings
from openalex_corpus.download.pdf_downloader import PDFDownloader, pdf_candidates
from openalex_corpus.download.validator import validate_pdf


def pdf_bytes(text="Original document"):
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((50, 50), text)
    result = document.tobytes()
    document.close()
    return result


def work(url="https://repository.example/paper.pdf", **extra):
    return {
        "id": "https://openalex.org/W1",
        "best_oa_location": {
            "pdf_url": url,
            "is_oa": True,
            "license": "cc-by",
            "version": "publishedVersion",
        },
        **extra,
    }


def download(tmp_path, handler, item=None, **options):
    async def run():
        async with PDFDownloader(
            DownloadSettings(max_attempts=2, **options),
            api_key="test",
            transport=httpx.MockTransport(handler),
            state_dir=tmp_path / "state",
        ) as downloader:
            return await downloader.download(item or work(), tmp_path / "paper")

    return asyncio.run(run())


def test_download_validate_and_reuse(tmp_path):
    content = pdf_bytes()
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, content=content, headers={"Content-Type": "application/pdf"})

    result = download(tmp_path, handler)
    assert result["status"] == "DOWNLOADED"
    assert result["sha256"] == hashlib.sha256(content).hexdigest()
    assert result["page_count"] == 1
    assert (tmp_path / "paper" / "original.pdf").read_bytes() == content
    second = download(tmp_path, handler)
    assert second["status"] == "REUSED"
    assert len(calls) == 1


def test_download_connection_failure(tmp_path):
    def handler(request):
        raise httpx.ConnectError("secret URL must not be echoed", request=request)

    result = download(tmp_path, handler)
    assert result["status"] == "DOWNLOAD_FAILED"
    assert len(result["attempts"]) == 2
    assert result["retryable"]
    assert not (tmp_path / "paper" / "original.pdf").exists()


@pytest.mark.parametrize("content_type", ["text/html", "application/pdf"])
def test_html_is_never_published_as_pdf(tmp_path, content_type):
    result = download(
        tmp_path,
        lambda r: httpx.Response(
            200, content=b"<html>Please login</html>", headers={"Content-Type": content_type}
        ),
    )
    assert result["status"] == "INVALID_PDF"
    assert not (tmp_path / "paper" / "original.pdf").exists()


def test_corrupt_pdf(tmp_path):
    result = download(
        tmp_path,
        lambda r: httpx.Response(
            200, content=b"%PDF-1.7\n corrupt", headers={"Content-Type": "application/pdf"}
        ),
    )
    assert result["status"] == "INVALID_PDF"
    assert not (tmp_path / "paper" / "original.pdf").exists()


def test_encrypted_pdf(tmp_path):
    document = pymupdf.open()
    document.new_page()
    encrypted = document.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="user")
    document.close()
    result = download(
        tmp_path,
        lambda r: httpx.Response(200, content=encrypted, headers={"Content-Type": "application/pdf"}),
    )
    assert result["status"] == "ENCRYPTED_PDF"


def test_content_disabled_still_falls_back_to_oa(tmp_path):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        assert request.url.host == "repository.example"
        assert "Authorization" not in request.headers
        return httpx.Response(200, content=pdf_bytes())

    result = download(
        tmp_path, handler, work(content_urls={"pdf": "https://content.openalex.org/works/W1.pdf"})
    )
    assert result["status"] == "DOWNLOADED"
    assert result["attempts"][0]["reason"] == "content_budget_exhausted_or_disabled"
    assert len(calls) == 1


def test_content_budget_is_durable_and_secrets_not_forwarded(tmp_path):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if request.url.host == "content.openalex.org":
            assert request.headers["Authorization"] == "Bearer test"
            return httpx.Response(
                302, headers={"Location": "https://archive.example/signed.pdf?token=secret"}
            )
        assert "Authorization" not in request.headers
        return httpx.Response(200, content=pdf_bytes())

    item = {
        "id": "https://openalex.org/W1",
        "content_urls": {"pdf": "https://content.openalex.org/works/W1.pdf"},
    }
    first = download(tmp_path, handler, item, max_content_downloads=1, max_content_cost_usd=0.01)
    assert first["status"] == "DOWNLOADED"
    item["content_urls"]["pdf"] = "https://content.openalex.org/works/W2.pdf"
    second = download(tmp_path, handler, item, max_content_downloads=1, max_content_cost_usd=0.01)
    assert second["status"] == "RATE_LIMITED"
    assert len(calls) == 2
    assert "token=secret" not in (tmp_path / "paper" / "download_manifest.json").read_text()


def test_distinct_manuscript_versions_preserve_original(tmp_path):
    first_bytes, second_bytes = pdf_bytes("submitted"), pdf_bytes("published")
    first = download(
        tmp_path,
        lambda r: httpx.Response(200, content=first_bytes),
        work("https://repository.example/submitted.pdf"),
    )
    second = download(
        tmp_path,
        lambda r: httpx.Response(200, content=second_bytes),
        work("https://repository.example/published.pdf"),
    )
    assert first["sha256"] != second["sha256"]
    assert (tmp_path / "paper" / "original.pdf").read_bytes() == first_bytes
    assert len(list((tmp_path / "paper" / "pdf_versions").glob("*.pdf"))) == 2
    assert len(json.loads((tmp_path / "paper" / "download_manifest.json").read_text())["versions"]) == 2


def test_explicit_refresh_same_url_keeps_old_bytes_and_reuses_newest(tmp_path):
    first_bytes, second_bytes = pdf_bytes("First manuscript bytes"), pdf_bytes("Revised manuscript bytes")
    first = download(tmp_path, lambda r: httpx.Response(200, content=first_bytes))
    # Explicit refresh bypasses a valid receipt, but not request/cost budgets.
    second = download(tmp_path, lambda r: httpx.Response(200, content=second_bytes), refresh_existing=True)
    assert first["status"] == second["status"] == "DOWNLOADED"
    assert first["sha256"] != second["sha256"]
    from pathlib import Path

    assert Path(first["path"]).read_bytes() == first_bytes
    assert Path(second["path"]).read_bytes() == second_bytes
    assert (tmp_path / "paper" / "original.pdf").read_bytes() == first_bytes
    manifest = json.loads((tmp_path / "paper" / "download_manifest.json").read_text())
    assert len(manifest["versions"]) == 2
    assert manifest["versions"][0]["source_url"] == manifest["versions"][1]["source_url"]
    third = download(tmp_path, lambda r: pytest.fail("Latest validated receipt must be reused"))
    assert third["status"] == "REUSED"
    assert third["sha256"] == second["sha256"]


def test_resume_requires_validator_and_matching_range(tmp_path):
    content = pdf_bytes()
    url = "https://repository.example/paper.pdf"
    url_hash = hashlib.sha256(url.encode()).hexdigest()
    partials = tmp_path / "paper" / ".downloads"
    partials.mkdir(parents=True)
    (partials / f"{url_hash}.part").write_bytes(content[:150])
    (partials / f"{url_hash}.json").write_text(json.dumps({"validator": '"version-1"'}))

    def handler(request):
        assert request.headers["Range"] == "bytes=150-"
        assert request.headers["If-Range"] == '"version-1"'
        return httpx.Response(
            206,
            content=content[150:],
            headers={"Content-Range": f"bytes 150-{len(content) - 1}/{len(content)}", "ETag": '"version-1"'},
        )

    result = download(tmp_path, handler)
    assert result["status"] == "DOWNLOADED"
    assert result["attempts"][0]["resumed_from"] == 150
    assert (tmp_path / "paper" / "original.pdf").read_bytes() == content


def test_server_ignoring_range_restarts_safely(tmp_path):
    content = pdf_bytes()
    url_hash = hashlib.sha256(b"https://repository.example/paper.pdf").hexdigest()
    partials = tmp_path / "paper" / ".downloads"
    partials.mkdir(parents=True)
    (partials / f"{url_hash}.part").write_bytes(b"stale partial")
    (partials / f"{url_hash}.json").write_text(json.dumps({"validator": '"old"'}))
    result = download(tmp_path, lambda r: httpx.Response(200, content=content))
    assert result["status"] == "DOWNLOADED"
    assert (tmp_path / "paper" / "original.pdf").read_bytes() == content


def test_license_allowlist_and_non_oa_are_recorded(tmp_path):
    result = download(tmp_path, lambda r: pytest.fail("No network expected"), allowed_licenses=["cc0"])
    assert result["status"] == "LICENSE_RESTRICTED"
    item = work()
    item["best_oa_location"]["is_oa"] = False
    result = download(tmp_path, lambda r: pytest.fail("No network expected"), item)
    assert result["status"] == "ACCESS_RESTRICTED"


def test_missing_pdf_and_size_cap(tmp_path):
    result = download(
        tmp_path, lambda r: pytest.fail("No network expected"), {"id": "https://openalex.org/W1"}
    )
    assert result["status"] == "PDF_NOT_AVAILABLE"
    result = download(tmp_path, lambda r: httpx.Response(200, content=b"x" * 4096), max_file_bytes=1024)
    assert result["status"] == "DOWNLOAD_FAILED"
    assert result["attempts"][0]["reason"] == "max_file_bytes_exceeded"


def test_candidate_priority_and_dedup():
    item = work(
        content_urls={"pdf": "https://content.openalex.org/works/W1.pdf"},
        primary_location={"pdf_url": "https://publisher.example/a.pdf", "is_oa": True},
        locations=[
            {"pdf_url": "https://repository.example/paper.pdf", "is_oa": True},
            {"pdf_url": "https://repository2.example/b.pdf", "is_oa": True},
        ],
    )
    candidates = pdf_candidates(item)
    assert len(candidates) == 4
    assert [c["source"] for c in candidates] == [
        "openalex_content",
        "best_oa_location",
        "primary_location",
        "locations[1]",
    ]


def test_download_429_is_bounded(tmp_path):
    result = download(tmp_path, lambda r: httpx.Response(429, headers={"Retry-After": "120"}))
    assert result["status"] == "RATE_LIMITED"
    assert len(result["attempts"]) == 1
    assert result["retry_at"]


def test_tampered_cached_version_is_not_reused(tmp_path):
    content = pdf_bytes()
    first = download(tmp_path, lambda r: httpx.Response(200, content=content))
    from pathlib import Path

    Path(first["path"]).write_bytes(b"tampered")
    second = download(tmp_path, lambda r: httpx.Response(200, content=content))
    assert second["status"] == "DOWNLOADED"
    assert "corrupted_cached_version_quarantined_and_redownloaded" in second["warnings"]
    quarantined = list((tmp_path / "paper" / "pdf_versions").glob("*.corrupt.*.bin"))
    assert len(quarantined) == 1
    assert quarantined[0].read_bytes() == b"tampered"
    assert validate_pdf(tmp_path / "paper" / "original.pdf")["status"] == "VALID"
