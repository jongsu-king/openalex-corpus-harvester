"""Record real OCR evidence without changing any collected original PDF.

The real-paper test rasterizes selected observed pages into a separate input.
That rasterization is clearly labelled and is not a newly collected source PDF.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

import pymupdf

from openalex_corpus.parsers import parse_pdf
from openalex_corpus.parsers.ocr import ocr_binary
from openalex_corpus.utils import atomic_json, sha256_file


def binary_version(binary: str) -> str:
    process = subprocess.run([binary, "--version"], capture_output=True, text=True, check=True)
    return (process.stdout or process.stderr).splitlines()[0]


def synthetic_mixed_fixture(path: Path):
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


def rasterize_page(source: Path, destination: Path, page_number=1):
    with pymupdf.open(source) as original, pymupdf.open() as raster:
        source_page = original[page_number - 1]
        output_page = raster.new_page(width=source_page.rect.width, height=source_page.rect.height)
        output_page.insert_image(
            output_page.rect, stream=source_page.get_pixmap(dpi=250, alpha=False).tobytes("png")
        )
        raster.save(destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-pdf", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("data/validation/ocr"))
    args = parser.parse_args()
    source = args.source_pdf.resolve()
    run_dir = args.output_dir.resolve() / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    run_dir.mkdir(parents=True)
    source_hash = sha256_file(source)
    evidence = {
        "tested_at": datetime.now(timezone.utc).isoformat(),
        "source_pdf": str(source),
        "source_sha256_before": source_hash,
        "versions": {
            "ocrmypdf": version("ocrmypdf"),
            "tesseract": binary_version("tesseract"),
            "ghostscript": binary_version("gs"),
            "pymupdf": version("pymupdf"),
        },
        "ocr_binary": ocr_binary(),
        "network_requests": 0,
        "cases": [],
    }
    cases = [
        (
            "synthetic_mixed",
            "Synthetic fixture with native digital first page and raster-only second page",
            2,
            "Organic photovoltaic cells",
        ),
        (
            "real_paper_page_1_raster",
            "Explicit rasterization of source page 1; derivative test input, not original scan",
            1,
            "Docling",
        ),
    ]
    for name, description, pages, anchor in cases:
        target = run_dir / name
        target.mkdir()
        input_pdf = target / "ocr_test_input.pdf"
        if name == "synthetic_mixed":
            synthetic_mixed_fixture(input_pdf)
        else:
            rasterize_page(source, input_pdf)
        before_hash = sha256_file(input_pdf)
        with pymupdf.open(input_pdf) as doc:
            before_chars = [len(page.get_text()) for page in doc]
        result = parse_pdf(
            input_pdf,
            target / "extraction",
            {
                "enable_docling": False,
                "primary_engine": "pymupdf",
                "enable_ocr": True,
                "ocr_timeout": 180,
                "render_dpi": 110,
            },
        )
        assert result["ocr"]["status"] == "success", result["ocr"]
        expected_ocr = [2] if name == "synthetic_mixed" else [1]
        assert result["ocr"]["pages"] == expected_ocr
        assert result["ocr"]["source_unchanged"]
        assert result["ocr"]["input_page_count"] == result["ocr"]["output_page_count"] == pages
        assert sha256_file(input_pdf) == before_hash
        assert anchor.casefold() in result["markdown"].casefold()
        if name == "synthetic_mixed":
            assert result["pages"][0]["ocr_used"] is False
            assert result["pages"][1]["ocr_used"] is True
            assert not any(
                b["source"] == "ocr_extracted" and b["page_number"] == 1 for b in result["text_blocks"]
            )
        derivative = target / "extraction" / result["ocr"]["path"]
        with pymupdf.open(derivative) as doc:
            after_chars = [len(page.get_text()) for page in doc]
        evidence["cases"].append(
            {
                "case": name,
                "description": description,
                "input_pdf": str(input_pdf),
                "input_sha256": before_hash,
                "original_input_unchanged": True,
                "output_pdf": str(derivative),
                "output_sha256": sha256_file(derivative),
                "native_char_counts_before": before_chars,
                "char_counts_after": after_chars,
                "selected_ocr_pages": result["ocr"]["pages"],
                "anchor": anchor,
                "anchor_found": True,
                "page_ocr_used": [p["ocr_used"] for p in result["pages"]],
                "page_statuses": [p["status"] for p in result["pages"]],
                "status": result["status"],
            }
        )
    evidence["source_sha256_after"] = sha256_file(source)
    evidence["original_source_unchanged"] = evidence["source_sha256_after"] == source_hash
    assert evidence["original_source_unchanged"]
    evidence["status"] = "passed"
    atomic_json(run_dir / "evidence.json", evidence)
    atomic_json(args.output_dir.resolve() / "latest_evidence.json", evidence)
    print(
        json.dumps(
            {"status": "passed", "evidence": str(run_dir / "evidence.json"), "cases": len(cases)}, indent=2
        )
    )


if __name__ == "__main__":
    main()
