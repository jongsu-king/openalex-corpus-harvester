from __future__ import annotations

import io
import json
import os
import re
from pathlib import Path

import pymupdf as fitz
import pytest
from PIL import Image, ImageDraw

from openalex_corpus.parsers import parse_pdf
from openalex_corpus.parsers.grobid_parser import parse_tei
from openalex_corpus.parsers.pymupdf_parser import extract_pdf
from openalex_corpus.parsers.reference_parser import extract_references, link_exact_references

SETTINGS = {"enable_docling": False, "enable_ocr": False, "render_dpi": 72}


def make_pdf(path: Path, pages: list[str]) -> Path:
    with fitz.open() as doc:
        for text in pages:
            page = doc.new_page(width=600, height=800)
            if text:
                page.insert_textbox((45, 70, 555, 700), text, fontsize=11)
        doc.save(path)
    return path


def test_all_pages_and_blank_page(tmp_path):
    pdf = make_pdf(
        tmp_path / "pages.pdf",
        [
            "First page text with enough characters for reliable extraction.",
            "",
            "Third page text with independently preserved source content.",
        ],
    )
    out = tmp_path / "out"
    result = parse_pdf(pdf, out, SETTINGS)
    assert [p["page_number"] for p in result["pages"]] == [1, 2, 3]
    assert result["pages"][1]["status"] == "blank"
    assert result["pages"][0]["status"] == "extracted"
    assert "Third page text" in result["markdown"]
    assert json.loads((out / "pages.json").read_text())[1]["blank_evidence"]
    assert result["status"] == "COMPLETE_WITH_WARNINGS"


def test_two_column_reading_order(tmp_path):
    pdf = tmp_path / "columns.pdf"
    with fitz.open() as doc:
        p = doc.new_page(width=600, height=800)
        p.insert_text((40, 55), "Two column fixture", fontsize=18)
        for y, text in [
            (110, "LEFT-FIRST paragraph one."),
            (210, "LEFT-SECOND paragraph two."),
            (310, "LEFT-THIRD paragraph three."),
        ]:
            p.insert_text((40, y), text, fontsize=10)
        for y, text in [
            (110, "RIGHT-FIRST paragraph one."),
            (210, "RIGHT-SECOND paragraph two."),
            (310, "RIGHT-THIRD paragraph three."),
        ]:
            p.insert_text((325, y), text, fontsize=10)
        doc.save(pdf)
    result = parse_pdf(pdf, tmp_path / "out", SETTINGS)
    assert result["pages"][0]["reading_order_method"] == "two_column_band_heuristic"
    assert result["markdown"].index("LEFT-THIRD") < result["markdown"].index("RIGHT-FIRST")
    assert any(w["code"] == "reading_order_requires_review" for w in result["warnings"])
    assert result["text_blocks"][0]["raw"]["lines"]


def test_merged_table_exports_geometry_and_empty_cells(tmp_path):
    pdf = tmp_path / "table.pdf"
    with fitz.open() as doc:
        p = doc.new_page(width=600, height=800)
        p.insert_text((40, 55), "Table 1. A merged header and an empty cell.")
        for y in [100, 140, 180, 220]:
            p.draw_line((50, y), (450, y))
        p.draw_line((50, 100), (50, 220))
        p.draw_line((450, 100), (450, 220))
        p.draw_line((250, 140), (250, 220))
        for x, y, t in [(80, 125, "Merged heading"), (80, 165, "A"), (280, 165, "B"), (80, 205, "10")]:
            p.insert_text((x, y), t)
        doc.save(pdf)
    out = tmp_path / "out"
    result = parse_pdf(pdf, out, SETTINGS)
    assert result["tables"]
    table = result["tables"][0]
    assert any(c["col_span"] == 2 for c in table["cells"])
    assert table["rows"][-1][-1] == ""
    assert 'colspan="2"' in (out / table["files"]["html"]).read_text()
    assert (out / table["files"]["csv"]).is_file()
    assert (out / table["files"]["json"]).is_file()
    assert (out / table["image_file"]).is_file()


def test_equation_keeps_source_crop_no_invented_latex(tmp_path):
    pdf = make_pdf(
        tmp_path / "equation.pdf",
        ["The original equation follows.\nE = h nu (1)\nMeasured energy is shown above."],
    )
    out = tmp_path / "out"
    result = parse_pdf(pdf, out, SETTINGS)
    assert result["equations"]
    eq = result["equations"][0]
    assert eq["latex"] is None
    assert eq["status"] == "equation_parse_failed"
    assert (out / eq["image_file"]).is_file()
    assert result["status"] == "PARTIAL"


def image_pdf(path: Path) -> Path:
    im = Image.new("RGB", (500, 650), "white")
    ImageDraw.Draw(im).text((30, 40), "Scanned source without a digital text layer", fill="black")
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    with fitz.open() as doc:
        p = doc.new_page(width=500, height=650)
        p.insert_image(p.rect, stream=buf.getvalue())
        doc.save(path)
    return path


def test_scanned_pdf_never_claims_complete(tmp_path):
    out = tmp_path / "out"
    result = parse_pdf(image_pdf(tmp_path / "scan.pdf"), out, SETTINGS)
    assert result["pages"][0]["status"] == "ocr_required"
    assert result["status"] == "PARTIAL"
    assert result["figures"][0]["kind"] == "image_object"
    assert result["figures"][0]["caption"] is None
    assert (out / result["figures"][0]["file"]).is_file()
    assert (out / result["pages"][0]["image_file"]).is_file()
    assert result["ocr"]["missing_reason"] == "ocr_disabled"


def test_no_references_explicit_reason(tmp_path):
    pdf = make_pdf(
        tmp_path / "no_refs.pdf", ["This source contains enough text to parse but does not list references."]
    )
    result = parse_pdf(pdf, tmp_path / "out", SETTINGS)
    assert result["references"] == []
    assert any(w["code"] == "references_not_detected" for w in result["warnings"])


def test_reference_strings_and_exact_match_only():
    blocks = [
        {
            "page_number": 2,
            "bbox": [10, 10, 50, 50],
            "text": "References\n[1] Original, A. Title. Journal 2020. doi:10.1234/test\n[2] Other source without a DOI.",
        }
    ]
    refs, _ = extract_references(blocks)
    assert len(refs) == 2
    assert refs[0]["doi"] is None
    assert refs[0]["doi_candidate"] == "10.1234/test"
    link_exact_references(refs, {"10.1234/test": ["W1"]})
    assert refs[0]["openalex_id"] is None
    refs[0]["doi"] = "10.1234/test"
    link_exact_references(refs, {"10.1234/test": ["W1", "W2"]})
    assert refs[0]["openalex_id"] is None
    link_exact_references(refs, {"10.1234/test": ["W1"]})
    assert refs[0]["openalex_id"] == "W1"


def test_markdown_image_references_exist(tmp_path):
    out = tmp_path / "out"
    result = parse_pdf(image_pdf(tmp_path / "scan.pdf"), out, SETTINGS)
    refs = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", result["markdown"])
    assert refs
    assert all((out / ref).is_file() for ref in refs)


def test_page_failure_is_retained(tmp_path, monkeypatch):
    pdf = make_pdf(
        tmp_path / "failure.pdf",
        ["Page one with enough text to extract.", "Page two with enough text to extract."],
    )
    real = fitz.Document.load_page

    def broken(self, page_id):
        if page_id == 1:
            raise RuntimeError("synthetic failure")
        return real(self, page_id)

    monkeypatch.setattr(fitz.Document, "load_page", broken)
    result = extract_pdf(pdf, tmp_path / "out", SETTINGS)
    assert len(result["pages"]) == 2
    assert result["pages"][1]["status"] == "failed"
    assert result["errors"][0]["page_number"] == 2


def test_docling_missing_page_flagged(tmp_path, monkeypatch):
    pdf = make_pdf(
        tmp_path / "coverage.pdf",
        [
            "First page preserved with enough literal words to check.",
            "Second page preserved with enough literal words to check.",
        ],
    )
    monkeypatch.setattr(
        "openalex_corpus.parsers.parse_docling",
        lambda *args: {
            "available": True,
            "status": "success",
            "page_markdown": {1: "First page preserved with enough literal words to check."},
            "page_numbers": [1],
            "items": [],
            "tables": [],
            "figures": [],
            "equations": [],
            "errors": [],
        },
    )
    result = parse_pdf(pdf, tmp_path / "out", SETTINGS)
    assert result["status"] == "PARTIAL"
    assert "Second page preserved" in result["markdown"]
    assert any(w["code"] == "primary_page_missing" for w in result["warnings"])


def test_docling_token_disagreement_retains_secondary(tmp_path, monkeypatch):
    pdf = make_pdf(
        tmp_path / "mismatch.pdf",
        [
            "A source paragraph with many literal tokens that cannot be discarded without evidence being retained."
        ],
    )
    monkeypatch.setattr(
        "openalex_corpus.parsers.parse_docling",
        lambda *args: {
            "available": True,
            "status": "success",
            "page_markdown": {1: "A source paragraph."},
            "page_numbers": [1],
            "items": [],
            "tables": [],
            "figures": [],
            "equations": [],
            "errors": [],
        },
    )
    out = tmp_path / "out"
    result = parse_pdf(pdf, out, SETTINGS)
    assert "cannot be discarded" in result["markdown"]
    assert any(w["code"] == "primary_text_disagreement" for w in result["warnings"])
    assert (out / "pymupdf_raw.json").is_file()


def test_tei_structured_references_and_raw(tmp_path):
    path = tmp_path / "sample.xml"
    path.write_text(
        """<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><back><listBibl><biblStruct xml:id="b1" coords="2,10,20,50,30"><analytic><title>Actual reference title</title><author><persName><forename>Alex</forename><surname>Author</surname></persName></author></analytic><monogr><title>Journal</title><imprint><date when="2022"/><biblScope unit="volume">5</biblScope><biblScope unit="page" from="10" to="15"/></imprint></monogr><idno type="DOI">10.1234/example</idno><note type="raw_reference">Author. Actual reference title. Journal 5:10-15 (2022).</note></biblStruct></listBibl></back></text></TEI>"""
    )
    ref = parse_tei(path)["references"][0]
    assert ref["doi"] == "10.1234/example"
    assert ref["pages"] == "10-15"
    assert ref["page_number"] == 2
    assert ref["bbox"] == [10, 20, 60, 50]
    assert ref["original_reference_string"].startswith("Author.")
    assert ref["openalex_id"] is None


def test_tei_external_entity_rejected(tmp_path):
    path = tmp_path / "unsafe.xml"
    path.write_text('<!DOCTYPE TEI [<!ENTITY x SYSTEM "file:///etc/passwd">]><TEI>&x;</TEI>')
    with pytest.raises(ValueError, match="DTD/entity"):
        parse_tei(path)


def test_corrupt_pdf_structured_failure(tmp_path):
    pdf = tmp_path / "bad.pdf"
    pdf.write_bytes(b"%PDF-1.4\nbroken")
    result = parse_pdf(pdf, tmp_path / "out", SETTINGS)
    assert result["status"] == "FAILED"
    assert result["errors"][0]["code"] == "pdf_open_or_extract_failed"
    assert (tmp_path / "out" / "document.json").is_file()


def test_encrypted_pdf_structured_failure(tmp_path):
    pdf = tmp_path / "encrypted.pdf"
    with fitz.open() as doc:
        doc.new_page()
        doc.save(pdf, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="secret")
    result = parse_pdf(pdf, tmp_path / "out", SETTINGS)
    assert result["status"] == "FAILED"
    assert not result["pages"]


@pytest.mark.optional
@pytest.mark.skipif(
    os.getenv("OPENALEX_RUN_DOCLING") != "1",
    reason="Set OPENALEX_RUN_DOCLING=1 for installed Docling; downloads require explicit OPENALEX_ALLOW_MODEL_DOWNLOADS=1",
)
def test_actual_docling_standard_pipeline(tmp_path):
    pdf = make_pdf(
        tmp_path / "docling.pdf",
        [
            "Original scientific fixture title\nAbstract\nThis deliberately synthetic source verifies the standard layout extraction engine and the preservation of original text."
        ],
    )
    result = parse_pdf(
        pdf,
        tmp_path / "out",
        {
            "enable_docling": True,
            "allow_model_downloads": os.getenv("OPENALEX_ALLOW_MODEL_DOWNLOADS") == "1",
            "docling_artifacts_path": os.getenv("DOCLING_ARTIFACTS_PATH"),
            "docling_threads": 2,
            "docling_timeout": 180,
            "render_dpi": 72,
        },
    )
    assert result["engine"].startswith("docling"), result["warnings"]
    assert result["pages"][0]["status"] == "extracted"
    assert "preservation of original text" in result["markdown"]
    assert not result["errors"]
    raw = json.loads((tmp_path / "out" / "docling_raw.json").read_text())
    renamed = tmp_path / "promoted"
    (tmp_path / "out").rename(renamed)
    for page in raw["pages"].values():
        uri = page.get("image", {}).get("uri")
        if uri:
            assert not Path(uri).is_absolute()
            assert (renamed / uri).is_file()


def test_literal_latex_candidate_does_not_infer_structure():
    from openalex_corpus.parsers.equation_parser import literal_latex_candidate

    candidate = literal_latex_candidate("E = h ν")
    assert candidate["latex_candidate"] == r"E = h \nu{}"
    assert candidate["latex_candidate_status"] == "unverified"
    assert candidate["latex_candidate_source_text"] == "E = h ν"
    assert literal_latex_candidate("E = h nu") is None  # The word is not a Greek glyph.
    assert literal_latex_candidate("x² = 4") is None
    assert literal_latex_candidate("x^2 = 4") is None
    assert literal_latex_candidate("a = 1\nb = 2") is None
    assert literal_latex_candidate("energy = 10") is None
    assert literal_latex_candidate("x = 4")["latex_candidate"] == "x = 4"


def test_failed_figure_never_complete(tmp_path, monkeypatch):
    pdf = make_pdf(
        tmp_path / "figure.pdf",
        ["A source paragraph with original text and a visual region that failed to extract."],
    )
    monkeypatch.setattr(
        "openalex_corpus.parsers.parse_docling",
        lambda *args: {
            "available": True,
            "status": "success",
            "page_markdown": {
                1: "A source paragraph with original text and a visual region that failed to extract."
            },
            "page_numbers": [1],
            "items": [],
            "tables": [],
            "figures": [
                {
                    "id": "figure_region_0001",
                    "page_number": 1,
                    "kind": "figure_region",
                    "bbox": [10, 10, 30, 30],
                    "file": None,
                    "validation_status": "parse_failed",
                    "missing_reason": "figure_region_image_unavailable",
                }
            ],
            "equations": [],
            "errors": [],
        },
    )
    result = parse_pdf(pdf, tmp_path / "out", SETTINGS)
    assert result["status"] == "PARTIAL"
    assert any(w["code"] == "figure_extraction_failed" for w in result["warnings"])


def test_table_agreement_checks_positions_multiplicity_and_nulls():
    from openalex_corpus.parsers.table_parser import same_cell_matrix

    assert same_cell_matrix([["1", "2"]], [["1", "2"]])
    assert not same_cell_matrix([["1", "2"]], [["2", "1"]])
    assert not same_cell_matrix([["1", "1"]], [["1"]])
    assert not same_cell_matrix([[None]], [[""]])


def test_model_preparation_disables_unrequested_families():
    import importlib.util

    script = Path(__file__).parents[1] / "scripts" / "prepare_models.py"
    spec = importlib.util.spec_from_file_location("prepare_models_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def downloader(
        *,
        with_layout=True,
        with_tableformer=True,
        with_code_formula=True,
        with_rapidocr=True,
        with_future_generation=True,
        whisper_models=None,
    ):
        pass

    flags = module.restricted_flags(downloader, layout=True, tableformer=True)
    assert flags == {
        "with_layout": True,
        "with_tableformer": True,
        "with_code_formula": False,
        "with_rapidocr": False,
        "with_future_generation": False,
        "whisper_models": None,
    }
