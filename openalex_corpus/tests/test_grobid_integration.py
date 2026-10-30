"""Opt-in local service test using a retained real OA paper; no paid API calls."""

import os
from pathlib import Path

import httpx
import pytest

from openalex_corpus.parsers.grobid_parser import request_tei
from openalex_corpus.utils import sha256_file


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("OPENALEX_RUN_GROBID") != "1", reason="Set OPENALEX_RUN_GROBID=1 for local service"
)
def test_actual_grobid_fulltext_with_raw_references(tmp_path):
    source = Path(os.getenv("GROBID_TEST_PDF", "data/papers/W4402950921/original.pdf")).resolve()
    if not source.is_file():
        pytest.skip("Set GROBID_TEST_PDF to a retained scientific PDF containing references")
    service = os.getenv("GROBID_URL", "http://127.0.0.1:8070")
    before = sha256_file(source)
    response = httpx.get(service.rstrip("/") + "/api/isalive", timeout=10)
    response.raise_for_status()
    assert response.text.strip().lower() == "true"
    output = tmp_path / "actual.tei.xml"
    result = request_tei(source, output, service, timeout=180)
    assert output.is_file() and output.stat().st_size > 1000
    assert result["metadata"]["title"]
    assert any(app["ident"] == "GROBID" and app["version"] for app in result["software"])
    assert result["sections"]
    assert result["references"]
    assert any(ref["original_reference_string"] for ref in result["references"])
    assert any(ref["authors"] for ref in result["references"])
    assert any(ref["evidence"] for ref in result["references"])
    assert all(ref["source"] == "grobid" and ref["openalex_id"] is None for ref in result["references"])
    assert sha256_file(source) == before
