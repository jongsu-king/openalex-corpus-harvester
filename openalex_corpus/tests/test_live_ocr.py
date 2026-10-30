"""Explicit opt-in tests execute real OCRmyPDF/Tesseract on raster-only pages."""

from __future__ import annotations

import os
from pathlib import Path

import pymupdf
import pytest

from openalex_corpus.parsers import parse_pdf
from openalex_corpus.parsers.ocr import ocr_binary, run_ocr
from openalex_corpus.utils import sha256_file


def make_mixed_fixture(path: Path) -> Path:
    """Page one is born digital; page two is an explicitly synthetic scan."""
    with pymupdf.open() as fixture:
        native = fixture.new_page(width=612, height=792)
        native.insert_text((50, 70), "Native digital control page remains unchanged.", fontsize=15)
        with pymupdf.open() as raster_source:
            page = raster_source.new_page(width=612, height=792)
            page.insert_text(
                (50, 70), "Organic photovoltaic cells convert sunlight to electricity.", fontsize=15
            )
            page.insert_text((50, 110), "Measured sample identifier OPV 12345.", fontsize=15)
            image = page.get_pixmap(dpi=250, alpha=False).tobytes("png")
        scan = fixture.new_page(width=612, height=792)
        scan.insert_image(scan.rect, stream=image)
        fixture.save(path)
    return path


requires_ocr = pytest.mark.skipif(
    os.getenv("OPENALEX_RUN_OCR") != "1",
    reason="Set OPENALEX_RUN_OCR=1 to run installed OCRmyPDF and Tesseract; no network requests",
)


@pytest.mark.optional
@requires_ocr
def test_real_ocr_selected_scan_preserves_digital_page_and_source(tmp_path):
    assert ocr_binary(), "Install the ocr extra and Tesseract before opting in"
    source = make_mixed_fixture(tmp_path / "mixed.pdf")
    original_hash = sha256_file(source)
    result = parse_pdf(
        source,
        tmp_path / "extraction",
        {
            "enable_docling": False,
            "primary_engine": "pymupdf",
            "enable_ocr": True,
            "ocr_timeout": 120,
            "render_dpi": 72,
        },
    )
    assert result["ocr"]["status"] == "success", result["ocr"]
    assert result["ocr"]["pages"] == [2]
    assert result["ocr"]["source_unchanged"]
    assert sha256_file(source) == original_hash
    assert result["ocr"]["input_page_count"] == result["ocr"]["output_page_count"] == 2
    assert result["pages"][0]["ocr_used"] is False
    assert result["pages"][1]["ocr_used"] is True
    assert result["pages"][1]["status"] == "extracted"
    assert result["pages"][1]["ocr_char_count"] > 50
    assert "Organic photovoltaic cells" in result["markdown"]
    assert "Native digital control page remains unchanged" in result["markdown"]
    assert not any(b["source"] == "ocr_extracted" and b["page_number"] == 1 for b in result["text_blocks"])
    assert any(b["source"] == "ocr_extracted" and b["page_number"] == 2 for b in result["text_blocks"])
    assert not result["pages"][1]["missing_regions"]
    assert (tmp_path / "extraction" / result["ocr"]["path"]).is_file()


@pytest.mark.optional
@requires_ocr
def test_real_ocr_rejects_out_of_range_page_without_altering_source(tmp_path):
    source = make_mixed_fixture(tmp_path / "mixed.pdf")
    digest = sha256_file(source)
    result = run_ocr(source, tmp_path / "out", [3], {"enable_ocr": True})
    assert result["status"] == "failed"
    assert result["missing_reason"] == "ocr_page_selection_invalid"
    assert sha256_file(source) == digest
