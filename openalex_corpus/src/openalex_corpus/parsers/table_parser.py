"""Independent pdfplumber table extraction with geometry-preserving exports."""

from __future__ import annotations

import csv
import io
import json
import re
from html import escape
from pathlib import Path

import pdfplumber
import pymupdf as fitz

from .pymupdf_parser import crop_region, message


def _edge_index(values: list[float], number: float) -> int:
    return min(range(len(values)), key=lambda i: abs(values[i] - number))


def _edges(values: list[float]) -> list[float]:
    result: list[float] = []
    for value in sorted(values):
        if not result or abs(result[-1] - value) > 0.5:
            result.append(value)
    return result


def table_to_html(table: dict) -> str:
    occupied: set[tuple[int, int]] = set()
    origins = {(c["row_start"], c["col_start"]): c for c in table["cells"]}
    lines = ["<table>"]
    for ri in range(table["row_count"]):
        lines.append("<tr>")
        for ci in range(table["column_count"]):
            if (ri, ci) in occupied:
                continue
            cell = origins.get((ri, ci))
            if not cell:
                lines.append('<td data-missing="true"></td>')
                continue
            rs, cs = cell["row_span"], cell["col_span"]
            for r in range(ri, ri + rs):
                for c in range(ci, ci + cs):
                    occupied.add((r, c))
            attrs = (f' rowspan="{rs}"' if rs > 1 else "") + (f' colspan="{cs}"' if cs > 1 else "")
            value = "" if cell["value"] is None else str(cell["value"])
            lines.append(f"<td{attrs}>{escape(value).replace(chr(10), '<br>')}</td>")
        lines.append("</tr>")
    lines.append("</table>")
    return "\n".join(lines)


def table_to_markdown(table: dict) -> str:
    # Markdown syntax requires a header; where none is established, HTML keeps
    # the first row as data rather than inventing a semantic header.
    return table_to_html(table)


def write_table(table: dict, out_dir: Path) -> None:
    stem = Path("tables") / table["id"]
    (out_dir / stem).parent.mkdir(parents=True, exist_ok=True)
    table["files"] = {ext: f"{stem.as_posix()}.{ext}" for ext in ("csv", "json", "html", "md")}
    (out_dir / table["files"]["html"]).write_text(table_to_html(table), encoding="utf-8")
    (out_dir / table["files"]["md"]).write_text(table_to_markdown(table), encoding="utf-8")
    stream = io.StringIO(newline="")
    csv.writer(stream).writerows(table["rows"])
    (out_dir / table["files"]["csv"]).write_text(stream.getvalue(), encoding="utf-8")
    (out_dir / table["files"]["json"]).write_text(
        json.dumps(table, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def extract_tables(pdf_path: Path, out_dir: Path, settings: dict) -> dict:
    result: dict = {"tables": [], "pages": {}, "warnings": [], "errors": []}
    with pdfplumber.open(pdf_path) as pl, fitz.open(pdf_path) as doc:
        for number, page in enumerate(pl.pages, 1):
            page_result = {
                "page_number": number,
                "text": None,
                "tables_count": 0,
                "status": "EXTRACTED",
                "warnings": [],
            }
            result["pages"][number] = page_result
            try:
                page_result["text"] = page.extract_text() or ""
                found = page.find_tables()
                page_result["tables_count"] = len(found)
                for idx, tab in enumerate(found, 1):
                    rows = tab.extract()
                    xs = _edges([coord for c in tab.cells for coord in (c[0], c[2])])
                    ys = _edges([coord for c in tab.cells for coord in (c[1], c[3])])
                    cells = []
                    # pdfplumber returns each physical merged cell once; None
                    # slots indicate absent/covered matrix cells and remain None.
                    seen = set()
                    for ri, row in enumerate(tab.rows):
                        for ci, bbox in enumerate(row.cells):
                            if bbox is None or tuple(bbox) in seen:
                                continue
                            seen.add(tuple(bbox))
                            r0, r1 = _edge_index(ys, bbox[1]), _edge_index(ys, bbox[3])
                            c0, c1 = _edge_index(xs, bbox[0]), _edge_index(xs, bbox[2])
                            value = rows[ri][ci] if ri < len(rows) and ci < len(rows[ri]) else None
                            cells.append(
                                {
                                    "row": r0,
                                    "column": c0,
                                    "row_start": r0,
                                    "row_end": r1,
                                    "col_start": c0,
                                    "col_end": c1,
                                    "row_span": max(1, r1 - r0),
                                    "col_span": max(1, c1 - c0),
                                    "value": value,
                                    "bbox": list(bbox),
                                    "is_header": None,
                                    "source": "pdf_extracted",
                                }
                            )
                    tid = f"table_p{number:04d}_{idx:03d}"
                    image = Path("tables") / f"{tid}.png"
                    crop_region(
                        doc[number - 1], tab.bbox, out_dir / image, int(settings.get("render_dpi", 110))
                    )
                    table = {
                        "id": tid,
                        "table_number": None,
                        "caption": None,
                        "header": None,
                        "footnotes": None,
                        "units": None,
                        "page_number": number,
                        "bbox": list(tab.bbox),
                        "row_count": max(0, len(ys) - 1),
                        "column_count": max(0, len(xs) - 1),
                        "rows": rows,
                        "cells": cells,
                        "source": "pdf_extracted",
                        "method": "pdfplumber_lines",
                        "validation_status": "unverified",
                        "missing_reason": "header_caption_units_not_verified",
                        "image_file": image.as_posix(),
                    }
                    widths = {len(row) for row in rows}
                    if len(widths) > 1:
                        table["validation_status"] = "parse_failed"
                        table["missing_reason"] = "irregular_extracted_row_widths"
                        page_result["warnings"].append(message("table_irregular_rows", number, table_id=tid))
                    write_table(table, out_dir)
                    result["tables"].append(table)
                # Compare independently detected tables and literal cell tokens.
                try:
                    secondary = doc[number - 1].find_tables().tables
                    candidates = [{"bbox": list(t.bbox), "rows": t.extract()} for t in secondary]
                    page_result["pymupdf_table_candidates"] = candidates
                    if len(candidates) != len(found):
                        page_result["warnings"].append(
                            message(
                                "table_detection_disagreement",
                                number,
                                pdfplumber_count=len(found),
                                pymupdf_count=len(candidates),
                            )
                        )
                    for table in [t for t in result["tables"] if t["page_number"] == number]:
                        matches = [c for c in candidates if _overlap(table["bbox"], c["bbox"]) > 0.5]
                        if matches:
                            own = _tokens(table["rows"])
                            other = _tokens(matches[0]["rows"])
                            if not same_cell_matrix(table["rows"], matches[0]["rows"]):
                                page_result["warnings"].append(
                                    message(
                                        "table_cell_values_disagree",
                                        number,
                                        table_id=table["id"],
                                        only_pdfplumber=sorted(own - other),
                                        only_pymupdf=sorted(other - own),
                                    )
                                )
                                table["candidate_parsers"] = {"pymupdf": matches[0]}
                            else:
                                table["validation_status"] = "cell_matrix_agreement"
                            write_table(table, out_dir)
                except Exception as exc:
                    page_result["warnings"].append(
                        message("table_secondary_parser_failed", number, error_type=type(exc).__name__)
                    )
            except Exception as exc:
                page_result.update(status="FAILED", error_type=type(exc).__name__)
                result["errors"].append(
                    message("pdfplumber_page_failed", number, error_type=type(exc).__name__)
                )
            result["warnings"].extend(page_result["warnings"])
            page.close()
    return result


def _tokens(rows: list) -> set[str]:
    return set(re.findall(r"\S+", " ".join(str(v) for row in rows for v in row if v is not None)))


def _overlap(a: list, b: list) -> float:
    intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection
    return intersection / union if union else 0


def same_cell_matrix(first: list, second: list) -> bool:
    """Check cell positions and multiplicity, not merely a set of token values."""

    def normalized(rows):
        return [[None if cell is None else " ".join(str(cell).split()) for cell in row] for row in rows]

    return normalized(first) == normalized(second)
