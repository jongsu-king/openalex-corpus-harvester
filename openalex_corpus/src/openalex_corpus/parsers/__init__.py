"""Evidence-preserving multi-parser orchestration.

Public entrypoint ``parse_pdf`` writes only to a caller-owned staging directory.
The pipeline decides whether a run may replace the currently published result.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from ..utils import atomic_json, atomic_text, redact
from .docling_parser import parse_docling
from .equation_parser import literal_latex_candidate
from .grobid_parser import parse_tei, request_tei
from .ocr import run_ocr
from .pymupdf_parser import crop_region, extract_pdf, message
from .reference_parser import extract_references
from .table_parser import _overlap, extract_tables, same_cell_matrix, table_to_markdown, write_table


def _save(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(path, data)


def _words(text: str) -> Counter:
    # Comparison normalization only. Persisted source text is never corrected.
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", text)
    return Counter(re.findall(r"\w+", text.casefold()))


def _comparison(primary: str, secondary: str) -> dict:
    a, b = _words(primary), _words(secondary)
    missing = b - a
    extra = a - b
    # A signal for human review, not a proof based on a character threshold.
    return {
        "secondary_tokens": sum(b.values()),
        "primary_tokens": sum(a.values()),
        "only_secondary_tokens": dict(missing),
        "only_primary_tokens": dict(extra),
        "secondary_unmatched_fraction": sum(missing.values()) / max(1, sum(b.values())),
        "interpretation": "candidate_disagreement_requires_source_review",
    }


def _append_assets(markdown: str, number: int, result: dict) -> str:
    assets = []
    for table in result["tables"]:
        if table.get("page_number") == number and table.get("method") != "docling_standard":
            assets.append(
                f"<!-- Independent table extraction: {table['id']}; see JSON for uncertainty. -->\n"
                + table_to_markdown(table)
            )
    for figure in result["figures"]:
        if figure.get("page_number") == number and figure.get("file"):
            rel = figure.get("display_file") or figure["file"]
            if rel not in markdown:
                # Alt text is a source pointer, never an invented description.
                assets.append(f"<!-- {figure['kind']}; source page {number}; {figure['id']} -->\n![]({rel})")
    for eq in result["equations"]:
        if eq.get("page_number") == number and eq.get("image_file"):
            assets.append(
                f"<!-- equation_parse_failed; source region retained; {eq['id']} -->\n![]({eq['image_file']})"
            )
    return markdown + ("\n\n" + "\n\n".join(assets) if assets else "")


def parse_pdf(pdf_path: Path, output_dir: Path, settings: dict | None = None) -> dict:
    """Extract all pages with independent sources and explicit failure states.

    No LLM, generated captions, graph digitization, or guessed references are
    used. ``COMPLETE`` is left to the caller's quality policy; the fallback
    always exposes its uncertainty.
    """
    settings = dict(settings or {})
    pdf_path, output_dir = Path(pdf_path), Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    versions = {}
    for package in ("pymupdf", "pdfplumber", "docling", "lxml", "pillow"):
        try:
            versions[package] = version(package)
        except PackageNotFoundError:
            versions[package] = None
    result = {
        "engine": "pymupdf_fallback",
        "parser_versions": versions,
        "pages": [],
        "text_blocks": [],
        "tables": [],
        "figures": [],
        "equations": [],
        "references": [],
        "warnings": [],
        "errors": [],
        "status": "FAILED",
        "artifacts": [],
        "markdown": "",
        "parser_policy": {
            "generative_models": False,
            "formula_enrichment": False,
            "reference_matching": "exact_identifiers_only",
            "uncertain_results": "retain_candidates_and_original_regions",
        },
    }
    try:
        secondary = extract_pdf(pdf_path, output_dir, settings)
    except Exception as exc:
        result["errors"].append(
            message(
                "pdf_open_or_extract_failed", error_type=type(exc).__name__, detail=redact(str(exc))[:500]
            )
        )
        return _finish(result, output_dir)
    for key in ("pages", "text_blocks", "figures", "equations", "warnings", "errors"):
        result[key].extend(secondary[key])
    result["page_count"] = secondary["page_count"]
    result["image_resources"] = secondary["image_resources"]
    _save(output_dir / "pymupdf_raw.json", secondary)
    fallback_pages = dict(secondary["markdown_pages"])
    # OCR only pages with missing/sparse text and visible material. A digital
    # page never becomes OCR provenance simply because a neighboring page scans.
    scanned = [p["page_number"] for p in result["pages"] if p["status"] == "ocr_required"]
    ocr = run_ocr(pdf_path, output_dir / "ocr", scanned, settings)
    result["ocr"] = ocr
    parsing_pdf = pdf_path
    if ocr["status"] == "success":
        parsing_pdf = Path(ocr["path"])
        recovered = extract_pdf(
            parsing_pdf, output_dir / "ocr_evidence", {**settings, "render_all_pages": False}
        )
        _save(output_dir / "ocr_extraction.json", recovered)
        for number in scanned:
            new_page = recovered["pages"][number - 1]
            pg = result["pages"][number - 1]
            if new_page["char_count"] >= int(settings.get("text_min_chars", 20)):
                pg.update(
                    ocr_used=True,
                    source="ocr_extracted",
                    status="extracted",
                    ocr_char_count=new_page["char_count"],
                    missing_regions=[],
                )
                pg["validation_messages"].append(message("ocr_text_unverified", number))
                fallback_pages[number] = recovered["markdown_pages"].get(number, "")
                for block in recovered["text_blocks"]:
                    if block["page_number"] == number:
                        block = dict(block)
                        block.update(
                            id="ocr_" + block["id"],
                            source="ocr_extracted",
                            method="ocrmypdf_tesseract_text_layer",
                        )
                        result["text_blocks"].append(block)
            else:
                pg["validation_messages"].append(message("ocr_produced_insufficient_text", number))
    elif scanned:
        result["warnings"].append(message(ocr.get("missing_reason", "ocr_unavailable"), pages=scanned))
    try:
        table_result = extract_tables(parsing_pdf, output_dir, settings)
        result["tables"].extend(table_result["tables"])
        for table in result["tables"]:
            table["candidate_group_id"] = table["id"]
            if result["pages"][table["page_number"] - 1]["ocr_used"]:
                table["source"] = "ocr_extracted"
                for cell in table["cells"]:
                    cell["source"] = "ocr_extracted"
                write_table(table, output_dir)
        result["warnings"].extend(table_result["warnings"])
        result["errors"].extend(table_result["errors"])
        _save(output_dir / "pdfplumber_raw.json", table_result)
        for pg in result["pages"]:
            number = pg["page_number"]
            other = table_result["pages"].get(number)
            if not other or other["status"] == "FAILED":
                pg["validation_messages"].append(message("secondary_page_validation_failed", number))
                continue
            source_text = "\n".join(
                b["text"]
                for b in result["text_blocks"]
                if b["page_number"] == number and ((b["source"] == "ocr_extracted") == pg["ocr_used"])
            )
            comparison = _comparison(source_text, other["text"] or "")
            pg["text_parser_comparison"] = comparison
            if comparison["secondary_unmatched_fraction"] > 0.12 and comparison["secondary_tokens"] >= 10:
                pg["validation_messages"].append(
                    message("text_parser_disagreement", number, comparison=comparison)
                )
            pg["validation_messages"].extend(other["warnings"])
    except Exception as exc:
        result["warnings"].append(
            message(
                "pdfplumber_unavailable_or_failed",
                error_type=type(exc).__name__,
                detail=redact(str(exc))[:500],
            )
        )
    primary = parse_docling(parsing_pdf, output_dir, settings)
    result["primary_input_source"] = "ocr_derivative" if parsing_pdf != pdf_path else "original_pdf"
    if primary["available"] and parsing_pdf != pdf_path:
        for category in ("items", "tables", "equations"):
            for candidate in primary[category]:
                number = candidate.get("page_number")
                if number and result["pages"][number - 1]["ocr_used"]:
                    candidate["source"] = "ocr_extracted"
                    for cell in candidate.get("cells", []):
                        cell["source"] = "ocr_extracted"
    _save(
        output_dir / "docling_status.json",
        {
            k: v
            for k, v in primary.items()
            if k not in ("markdown", "page_markdown", "items", "tables", "figures", "equations")
        },
    )
    primary_pages: dict = {}
    if primary["available"]:
        result["engine"] = "docling+pymupdf+pdfplumber"
        _save(output_dir / "docling_items.json", primary["items"])
        primary_pages = primary["page_markdown"]
        result["figures"].extend(primary["figures"])
        result["equations"].extend(primary["equations"])
        # Preserve disagreeing tables as separate candidates, link overlapping
        # alternatives; they are never silently collapsed into a winner.
        for table in primary["tables"]:
            table["candidate_group_id"] = table["id"]
            counterparts = [
                t
                for t in result["tables"]
                if t["page_number"] == table["page_number"]
                and t.get("bbox")
                and table.get("bbox")
                and _overlap(t["bbox"], table["bbox"]) > 0.45
            ]
            for counterpart in counterparts:
                table["candidate_group_id"] = counterpart.get("candidate_group_id", counterpart["id"])
                table.setdefault("alternative_candidate_ids", []).append(counterpart["id"])
                counterpart.setdefault("alternative_candidate_ids", []).append(table["id"])
                if not same_cell_matrix(table["rows"], counterpart["rows"]):
                    result["warnings"].append(
                        message(
                            "docling_table_values_disagree",
                            table["page_number"],
                            candidates=[table["id"], counterpart["id"]],
                        )
                    )
                else:
                    table["validation_status"] = "cell_matrix_agreement"
                write_table(counterpart, output_dir)
            write_table(table, output_dir)
            result["tables"].append(table)
        if "success" != primary.get("status", "").lower():
            result["warnings"].append(
                message(
                    "docling_conversion_not_fully_successful",
                    status=primary.get("status"),
                    errors=primary.get("errors"),
                )
            )
    else:
        result["warnings"].append(message(primary["reason"]))
    # Preserve formula regions discovered only by Docling, including vector math.
    if any(not e.get("image_file") and e.get("bbox") for e in result["equations"]):
        import pymupdf as fitz

        with fitz.open(pdf_path) as original:
            for eq in result["equations"]:
                number = eq.get("page_number")
                if not eq.get("image_file") and eq.get("bbox") and number and 1 <= number <= len(original):
                    rel = Path("equations") / f"{eq['id']}.png"
                    if crop_region(
                        original[number - 1],
                        eq["bbox"],
                        output_dir / rel,
                        int(settings.get("render_dpi", 110)),
                    ):
                        eq["image_file"] = rel.as_posix()
    for equation in result["equations"]:
        literal = literal_latex_candidate(equation.get("text") or "")
        if literal:
            equation.update(literal)
    for figure in result["figures"]:
        if figure.get("validation_status") == "parse_failed":
            result["warnings"].append(
                message(
                    "figure_extraction_failed",
                    figure.get("page_number"),
                    figure_id=figure["id"],
                    missing_reason=figure.get("missing_reason"),
                )
            )
    markdown = []
    for pg in result["pages"]:
        number = pg["page_number"]
        pg["table_count"] = len(
            {t.get("candidate_group_id", t["id"]) for t in result["tables"] if t.get("page_number") == number}
        )
        pg["table_candidate_count"] = sum(t.get("page_number") == number for t in result["tables"])
        pg["figure_region_count"] = sum(
            f.get("page_number") == number and f["kind"] == "figure_region" for f in result["figures"]
        )
        pg["equation_count"] = sum(e.get("page_number") == number for e in result["equations"])
        candidate = primary_pages.get(number)
        selected = candidate if candidate is not None else fallback_pages.get(number, "")
        source = "docling" if candidate is not None else ("ocr_extracted" if pg["ocr_used"] else "pymupdf")
        if primary["available"]:
            if number not in primary.get("page_numbers", []):
                pg["validation_messages"].append(message("primary_page_missing", number))
                pg["missing_regions"].append(
                    {"bbox": [0, 0, pg["width"], pg["height"]], "reason": "primary_page_missing"}
                )
            elif candidate is not None:
                comparison = _comparison(candidate, fallback_pages.get(number, ""))
                pg["primary_parser_comparison"] = comparison
                if comparison["secondary_unmatched_fraction"] > 0.18 and comparison["secondary_tokens"] >= 10:
                    pg["validation_messages"].append(
                        message("primary_text_disagreement", number, comparison=comparison)
                    )
                    # Place the source text in the published page at its actual
                    # location. The alternate logical extraction stays on disk.
                    selected = fallback_pages.get(number, "")
                    source = "pymupdf_conflict_fallback"
        if pg["status"] == "ocr_required":
            selected += f"\n\n<!-- Text extraction unavailable on source page {number}; see page image. -->"
            if pg.get("image_file"):
                selected += f"\n![]({pg['image_file']})"
        selected = _append_assets(selected, number, result)
        markdown.append(f"<!-- source page: {number}; parser: {source} -->\n\n{selected.strip()}")
    result["markdown"] = "\n\n".join(markdown) + "\n"
    ref_blocks = [
        b
        for b in result["text_blocks"]
        if ((b["source"] == "ocr_extracted") == result["pages"][b["page_number"] - 1]["ocr_used"])
    ]
    result["references"], ref_warnings = extract_references(ref_blocks)
    result["warnings"].extend(ref_warnings)
    tei_path = settings.get("tei_path")
    result["academic_parser"] = {"status": "not_configured", "source": None, "reference_count": 0}
    try:
        if tei_path:
            tei = parse_tei(Path(tei_path), source="provided_tei")
            # The original TEI is immutable extraction evidence too.
            (output_dir / "source.tei.xml").write_bytes(Path(tei_path).read_bytes())
        elif settings.get("grobid_url"):
            tei = request_tei(
                parsing_pdf,
                output_dir / "grobid.tei.xml",
                settings["grobid_url"],
                float(settings.get("grobid_timeout", 120)),
            )
        else:
            tei = None
        if tei:
            result["academic_parser"] = {
                "status": "success",
                "source": "provided_tei" if tei_path else "grobid",
                "reference_count": len(tei["references"]),
                "section_count": len(tei.get("sections", [])),
                "validation_status": "extracted_candidates",
                "software": tei.get("software", []),
            }
            _save(output_dir / "tei_document.json", tei)
            result["references"].extend(tei["references"])
            result["tei_metadata"] = tei["metadata"]
            result["tei_input_source"] = "provided_tei" if tei_path else result["primary_input_source"]
            if tei["references"]:
                result["warnings"] = [
                    w for w in result["warnings"] if w["code"] != "references_structural_parser_unavailable"
                ]
    except Exception as exc:
        result["academic_parser"] = {
            "status": "failed",
            "source": "provided_tei" if tei_path else "grobid",
            "error_type": type(exc).__name__,
            "missing_reason": redact(str(exc))[:500],
            "reference_count": 0,
        }
        result["warnings"].append(
            message("tei_parser_failed", error_type=type(exc).__name__, detail=redact(str(exc))[:500])
        )
    result["warnings"].extend(warning for pg in result["pages"] for warning in pg["validation_messages"])
    unresolved = any(
        p["status"] in {"failed", "ocr_required"} or p["missing_regions"] for p in result["pages"]
    )
    unresolved = unresolved or bool(result["errors"]) or bool(result["equations"])
    unresolved = unresolved or any(
        item.get("validation_status") == "parse_failed"
        for category in ("tables", "figures", "image_resources")
        for item in result.get(category, [])
    )
    if result["ocr"].get("path"):
        result["ocr"]["path"] = Path(result["ocr"]["path"]).relative_to(output_dir).as_posix()
    result["status"] = (
        "PARTIAL" if unresolved else "COMPLETE_WITH_WARNINGS" if result["warnings"] else "COMPLETE"
    )
    return _finish(result, output_dir)


def _finish(result: dict, out: Path) -> dict:
    for figure in result["figures"]:
        figure["image_file"] = figure.get("display_file") or figure.get("file")
    atomic_text(out / "paper_body.md", result["markdown"])
    _save(out / "pages.json", result["pages"])
    _save(out / "references.json", result["references"])
    _save(out / "equations.json", result["equations"])
    _save(out / "figures.json", result["figures"])
    result["artifacts"] = [
        {"path": p.relative_to(out).as_posix(), "size_bytes": p.stat().st_size}
        for p in sorted(out.rglob("*"))
        if p.is_file() and p.name != "document.json"
    ]
    _save(out / "document.json", result)
    return result
