"""Optional standard Docling adapter, with generative enrichments disabled."""

from __future__ import annotations

import os
import re
from pathlib import Path

from ..utils import redact
from .table_parser import write_table


def _provenance(item, document) -> list[dict]:
    result = []
    for prov in getattr(item, "prov", []):
        bb = prov.bbox
        height = document.pages[prov.page_no].size.height
        if str(getattr(bb.coord_origin, "value", bb.coord_origin)).upper() == "BOTTOMLEFT":
            bbox = [bb.l, height - bb.t, bb.r, height - bb.b]
        else:
            bbox = [bb.l, bb.t, bb.r, bb.b]
        result.append({"page_number": prov.page_no, "bbox": bbox, "raw": prov.model_dump(mode="json")})
    return result


def parse_docling(pdf_path: Path, out_dir: Path, settings: dict) -> dict:
    if not settings.get("enable_docling", True) or settings.get("primary_engine", "docling") != "docling":
        return {"available": False, "reason": "docling_disabled"}
    artifact_path = settings.get("docling_artifacts_path")
    allow_downloads = settings.get("allow_model_downloads", False)
    if not allow_downloads and not artifact_path:
        return {
            "available": False,
            "reason": "docling_models_not_configured",
            "hint": "Configure docling_artifacts_path, or explicitly allow model downloads",
        }
    previous_env = {key: os.environ.get(key) for key in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")}
    if not allow_downloads:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
    try:
        from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling_core.types.doc import ImageRefMode, PictureItem, TableItem

        opts = PdfPipelineOptions()
        # No OCR in the layout pipeline: selected scanned pages use the explicit
        # OCRmyPDF derivative below and retain an independent provenance trail.
        opts.do_ocr = False
        opts.do_table_structure = True
        opts.generate_page_images = True
        opts.generate_picture_images = True
        opts.images_scale = int(settings.get("render_dpi", 110)) / 72
        opts.artifacts_path = Path(artifact_path) if artifact_path else None
        opts.accelerator_options = AcceleratorOptions(
            num_threads=int(settings.get("docling_threads", 2)), device=AcceleratorDevice.CPU
        )
        opts.document_timeout = float(settings.get("docling_timeout", 300))
        for field in ("layout_batch_size", "table_batch_size", "ocr_batch_size"):
            if field in type(opts).model_fields:
                setattr(opts, field, 1)
        if "queue_max_size" in type(opts).model_fields:
            opts.queue_max_size = 2
        for field in (
            "do_picture_description",
            "do_picture_classification",
            "do_code_enrichment",
            "do_formula_enrichment",
            "do_chart_extraction",
            "enable_remote_services",
            "allow_external_plugins",
        ):
            if field in type(opts).model_fields:
                setattr(opts, field, False)
        converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
        )
        converted = converter.convert(pdf_path, raises_on_error=False)
        status = str(getattr(converted.status, "value", converted.status))
        document = converted.document
        out_dir.mkdir(parents=True, exist_ok=True)
        md_path = out_dir / "docling_body.md"
        document.save_as_markdown(
            md_path, artifacts_dir=Path("docling_body_artifacts"), image_mode=ImageRefMode.REFERENCED
        )
        document.save_as_json(
            out_dir / "docling_raw.json",
            artifacts_dir=Path("docling_raw_artifacts"),
            image_mode=ImageRefMode.REFERENCED,
        )
        items, tables, figures, formulas = [], [], [], []
        for item, level in document.iterate_items():
            evidence = _provenance(item, document)
            base = {
                "page_number": evidence[0]["page_number"] if evidence else None,
                "bbox": evidence[0]["bbox"] if evidence else None,
                "evidence": evidence,
                "source": "pdf_extracted",
                "method": "docling_standard",
                "validation_status": "unverified",
            }
            label = str(getattr(getattr(item, "label", None), "value", getattr(item, "label", "")))
            items.append(
                {
                    **base,
                    "label": label,
                    "level": level,
                    "text": getattr(item, "text", None),
                    "raw": item.model_dump(mode="json"),
                }
            )
            if isinstance(item, TableItem):
                tid = f"docling_table_{len(tables) + 1:04d}"
                data = item.data
                rows = [[None for _ in range(data.num_cols)] for _ in range(data.num_rows)]
                cells = []
                for cell in data.table_cells:
                    r0, c0 = cell.start_row_offset_idx, cell.start_col_offset_idx
                    r1, c1 = cell.end_row_offset_idx, cell.end_col_offset_idx
                    if r0 < len(rows) and c0 < len(rows[r0]):
                        rows[r0][c0] = cell.text
                    cells.append(
                        {
                            "row": r0,
                            "column": c0,
                            "row_start": r0,
                            "row_end": r1,
                            "col_start": c0,
                            "col_end": c1,
                            "row_span": r1 - r0,
                            "col_span": c1 - c0,
                            "value": cell.text,
                            "bbox": cell.bbox.model_dump(mode="json") if cell.bbox else None,
                            "is_header": cell.column_header,
                            "source": "pdf_extracted",
                            "raw": cell.model_dump(mode="json"),
                        }
                    )
                caption = item.caption_text(document) or None
                number = re.search(r"\bTable\s+(\S+)", caption or "", re.I)
                table = {
                    **base,
                    "id": tid,
                    "table_number": number.group(1) if number else None,
                    "caption": caption,
                    "header": None,
                    "footnotes": [x.model_dump(mode="json") for x in item.footnotes],
                    "units": None,
                    "row_count": data.num_rows,
                    "column_count": data.num_cols,
                    "rows": rows,
                    "cells": cells,
                    "raw": item.model_dump(mode="json"),
                }
                image = item.get_image(document)
                if image is not None:
                    rel = Path("tables") / f"{tid}.png"
                    (out_dir / rel).parent.mkdir(parents=True, exist_ok=True)
                    image.save(out_dir / rel, "PNG")
                    table["image_file"] = rel.as_posix()
                write_table(table, out_dir)
                tables.append(table)
            if isinstance(item, PictureItem):
                fid = f"figure_region_{len(figures) + 1:04d}"
                image = item.get_image(document)
                rel = Path("figures") / f"{fid}.png"
                if image is not None:
                    (out_dir / rel).parent.mkdir(parents=True, exist_ok=True)
                    image.save(out_dir / rel, "PNG")
                caption = item.caption_text(document) or None
                number = re.search(r"\b(?:Figure|Fig\.)\s+(\S+)", caption or "", re.I)
                figures.append(
                    {
                        **base,
                        "id": fid,
                        "kind": "figure_region",
                        "figure_number": number.group(1) if number else None,
                        "caption": caption,
                        "file": rel.as_posix() if image else None,
                        "validation_status": "unverified" if image else "parse_failed",
                        "missing_reason": None if image else "figure_region_image_unavailable",
                        "original_resolution": None,
                        "extraction_method": "docling_layout_region_render",
                        "panels": None,
                        "raw": item.model_dump(mode="json"),
                    }
                )
            if label == "formula":
                formulas.append(
                    {
                        **base,
                        "id": f"docling_equation_{len(formulas) + 1:04d}",
                        "text": getattr(item, "text", None),
                        "latex": None,
                        "status": "equation_parse_failed",
                        "missing_reason": "non_generative_formula_structure_not_verified",
                        "raw": item.model_dump(mode="json"),
                    }
                )
        page_markdown = {int(n): document.export_to_markdown(page_no=int(n)) for n in document.pages}
        return {
            "available": True,
            "status": status,
            "markdown": md_path.read_text(encoding="utf-8"),
            "page_markdown": page_markdown,
            "page_numbers": sorted(int(n) for n in document.pages),
            "items": items,
            "tables": tables,
            "figures": figures,
            "equations": formulas,
            "errors": [
                e.model_dump(mode="json") if hasattr(e, "model_dump") else str(e) for e in converted.errors
            ],
        }
    except ImportError as exc:
        return {
            "available": False,
            "reason": "docling_not_installed_or_import_failed",
            "error_type": type(exc).__name__,
            "detail": redact(str(exc))[:500],
        }
    except Exception as exc:
        return {
            "available": False,
            "reason": "docling_failed",
            "error_type": type(exc).__name__,
            "detail": redact(str(exc))[:500],
        }
    finally:
        for key, val in previous_env.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
