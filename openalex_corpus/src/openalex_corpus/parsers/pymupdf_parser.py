"""PDF evidence extraction. Coordinates are PDF points in top-left page space.

The conservative fallback is deliberately not advertised as a logical-structure
oracle: original spans, ordering decisions, and page evidence remain available.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from statistics import median
from typing import Any

import pymupdf as fitz


def json_safe(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"byte_length": len(value)}
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return value


def message(code: str, page_number: int | None = None, **kwargs: Any) -> dict:
    return {"code": code, "page_number": page_number, **kwargs}


def crop_region(page: Any, bbox: list[float] | tuple, out: Path, dpi: int = 110) -> bool:
    rect = fitz.Rect(bbox) & page.rect
    if rect.is_empty or rect.is_infinite:
        return False
    out.parent.mkdir(parents=True, exist_ok=True)
    page.get_pixmap(clip=rect, dpi=dpi, alpha=False).save(out)
    return True


def reading_order(blocks: list[dict], width: float) -> tuple[list[dict], str]:
    """Two-column band ordering; spanning headings split the column bands.

    This heuristic never modifies source strings and reports its use. Tables
    and all unusual arrangements require validation, not an assertion of truth.
    """
    mid = width / 2
    left = [b for b in blocks if b["bbox"][2] <= mid + 5]
    right = [b for b in blocks if b["bbox"][0] >= mid - 5]
    if len(left) < 2 or len(right) < 2:
        return sorted(blocks, key=lambda b: (round(b["bbox"][1], 1), b["bbox"][0])), "top_to_bottom"
    left_ids, right_ids = {b["id"] for b in left}, {b["id"] for b in right}
    spanning = sorted([b for b in blocks if b["id"] not in left_ids | right_ids], key=lambda b: b["bbox"][1])
    remaining = [b for b in blocks if b["id"] in left_ids | right_ids]
    ordered = []
    for barrier in [*spanning, None]:
        limit = barrier["bbox"][1] if barrier else float("inf")
        band = [b for b in remaining if b["bbox"][1] < limit]
        for group in (left_ids, right_ids):
            ordered.extend(
                sorted([b for b in band if b["id"] in group], key=lambda b: (b["bbox"][1], b["bbox"][0]))
            )
        chosen = {b["id"] for b in band}
        remaining = [b for b in remaining if b["id"] not in chosen]
        if barrier:
            ordered.append(barrier)
    return ordered, "two_column_band_heuristic"


def _block_text(block: dict) -> str:
    return "\n".join(
        "".join(s.get("text", "") for s in line.get("spans", [])) for line in block.get("lines", [])
    )


def extract_pdf(pdf_path: Path, out_dir: Path, settings: dict) -> dict:
    result: dict = {
        "pages": [],
        "text_blocks": [],
        "figures": [],
        "equations": [],
        "warnings": [],
        "errors": [],
        "markdown_pages": {},
        "image_resources": [],
    }
    dpi = int(settings.get("render_dpi", 110))
    minimum = int(settings.get("text_min_chars", 20))
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf_path)
    try:
        if doc.needs_pass:
            raise ValueError("ENCRYPTED_PDF: password required")
        result["page_count"] = len(doc)
        result["pdf_metadata"] = doc.metadata
        resource_files: dict[int, str] = {}
        for number in range(1, len(doc) + 1):
            pg: dict = {
                "page_number": number,
                "width": None,
                "height": None,
                "text_blocks_count": 0,
                "char_count": 0,
                "table_count": 0,
                "image_count": 0,
                "equation_count": 0,
                "ocr_used": False,
                "source": "pdf_text_layer",
                "status": "failed",
                "missing_regions": [],
                "validation_messages": [],
            }
            result["pages"].append(pg)  # A failed page is still represented.
            try:
                page = doc.load_page(number - 1)
                pg.update(width=page.rect.width, height=page.rect.height)
                text_flags = fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES
                raw = page.get_text("dict", flags=text_flags, sort=False)
                blocks = []
                for block in raw.get("blocks", []):
                    if block.get("type") != 0:
                        continue
                    text = _block_text(block)
                    item = {
                        "id": f"p{number}_b{len(blocks) + 1}",
                        "page_number": number,
                        "bbox": list(block["bbox"]),
                        "text": text,
                        "raw": json_safe(block),
                        "source": "pdf_extracted",
                        "method": "pymupdf_text_layer",
                        "validation_status": "unverified",
                    }
                    blocks.append(item)
                ordered, method = reading_order(blocks, page.rect.width)
                pg["reading_order_method"] = method
                for index, block in enumerate(ordered):
                    block["reading_order"] = index
                result["text_blocks"].extend(ordered)
                pg["text_blocks_count"] = len(blocks)
                pg["char_count"] = sum(len(b["text"].strip()) for b in blocks)
                if method == "two_column_band_heuristic":
                    pg["validation_messages"].append(
                        message("reading_order_requires_review", number, method=method)
                    )
                if any("\ufffd" in b["text"] for b in blocks):
                    pg["validation_messages"].append(message("unicode_replacement_character", number))
                # Retain even image resources that are not painted on the page.
                # They are recorded separately from actual displayed figures.
                for resource in page.get_images(full=True):
                    xref = resource[0]
                    resource_record = {
                        "xref": xref,
                        "page_number": number,
                        "bbox": None,
                        "kind": "image_resource",
                        "source": "pdf_extracted",
                        "raw": list(resource),
                    }
                    try:
                        if xref not in resource_files:
                            image_data = doc.extract_image(xref)
                            if not image_data:
                                raise ValueError("No image bytes")
                            resource_path = Path("image_resources") / f"xref_{xref:07d}.{image_data['ext']}"
                            (out_dir / resource_path).parent.mkdir(parents=True, exist_ok=True)
                            (out_dir / resource_path).write_bytes(image_data["image"])
                            resource_files[xref] = resource_path.as_posix()
                        resource_record.update(file=resource_files[xref], validation_status="present")
                    except Exception as exc:
                        resource_record.update(
                            file=None, validation_status="parse_failed", missing_reason=type(exc).__name__
                        )
                        pg["validation_messages"].append(
                            message("image_resource_extraction_failed", number, xref=xref)
                        )
                    result["image_resources"].append(resource_record)
                image_infos = page.get_image_info(xrefs=True)
                # Embedded bytes retain the original PDF image resolution; crops
                # are additionally retained for inline/undetectable xrefs.
                for occurrence, info in enumerate(image_infos, 1):
                    fid = f"image_p{number:04d}_{occurrence:03d}"
                    xref = info.get("xref", 0)
                    record = {
                        "id": fid,
                        "kind": "image_object",
                        "figure_number": None,
                        "caption": None,
                        "page_number": number,
                        "bbox": list(info["bbox"]),
                        "source": "pdf_extracted",
                        "width": info.get("width"),
                        "height": info.get("height"),
                        "original_resolution": {
                            "width": info.get("width"),
                            "height": info.get("height"),
                            "xres": info.get("xres"),
                            "yres": info.get("yres"),
                        },
                        "xref": xref,
                        "validation_status": "present",
                        "raw": json_safe(info),
                    }
                    try:
                        if xref:
                            data = doc.extract_image(xref)
                            if not data:
                                raise ValueError("Image object has no extractable bytes")
                            rel = Path("figures") / f"{fid}.{data['ext']}"
                            (out_dir / rel).parent.mkdir(parents=True, exist_ok=True)
                            (out_dir / rel).write_bytes(data["image"])
                            record.update(file=rel.as_posix(), extraction_method="embedded_image_object")
                            if data.get("smask"):
                                mask = doc.extract_image(data["smask"])
                                if mask:
                                    maskrel = Path("figures") / f"{fid}_mask.{mask['ext']}"
                                    (out_dir / maskrel).write_bytes(mask["image"])
                                    record["mask_file"] = maskrel.as_posix()
                                    record["mask_xref"] = data["smask"]
                                    composed = fitz.Pixmap(
                                        fitz.Pixmap(doc, xref), fitz.Pixmap(doc, data["smask"])
                                    )
                                    displayrel = Path("figures") / f"{fid}_display.png"
                                    composed.save(out_dir / displayrel)
                                    record["display_file"] = displayrel.as_posix()
                        else:
                            rel = Path("figures") / f"{fid}.png"
                            if not crop_region(page, info["bbox"], out_dir / rel, dpi):
                                raise ValueError("Empty image region")
                            record.update(
                                file=rel.as_posix(),
                                extraction_method="rendered_inline_image_region",
                                validation_status="unverified",
                            )
                            pg["validation_messages"].append(
                                message(
                                    "inline_image_original_bytes_unavailable", number, bbox=record["bbox"]
                                )
                            )
                    except Exception as exc:
                        record.update(
                            file=None, validation_status="parse_failed", missing_reason=type(exc).__name__
                        )
                        pg["validation_messages"].append(
                            message("image_extraction_failed", number, bbox=record["bbox"])
                        )
                        pg["missing_regions"].append(
                            {"bbox": record["bbox"], "reason": "image_extraction_failed"}
                        )
                    result["figures"].append(record)
                pg["image_count"] = len(image_infos)
                # Equation detection is a candidate heuristic, never conversion
                # to invented LaTeX. The exact original region is retained.
                for block in blocks:
                    text = block["text"]
                    math_font = any(
                        re.search(r"math|symbol|cmsy|cmmi|cmex", s.get("font", ""), re.I)
                        for line in block["raw"].get("lines", [])
                        for s in line.get("spans", [])
                    )
                    if not (math_font or re.search(r"[=∫∑√∂∞≤≥≈≠∇]", text)):
                        continue
                    eqid = f"equation_p{number:04d}_{pg['equation_count'] + 1:03d}"
                    rel = Path("equations") / f"{eqid}.png"
                    saved = crop_region(page, block["bbox"], out_dir / rel, dpi)
                    result["equations"].append(
                        {
                            "id": eqid,
                            "page_number": number,
                            "bbox": block["bbox"],
                            "text": text,
                            "latex": None,
                            "equation_number": None,
                            "source": "pdf_extracted",
                            "method": "math_symbol_or_font_candidate",
                            "status": "equation_parse_failed",
                            "validation_status": "unverified",
                            "missing_reason": "exact_mathematical_structure_not_verified",
                            "image_file": rel.as_posix() if saved else None,
                        }
                    )
                    pg["equation_count"] += 1
                    pg["validation_messages"].append(
                        message("equation_parse_failed", number, bbox=block["bbox"])
                    )
                drawings = page.get_drawings()
                pg["drawing_count"] = len(drawings)
                # Preserve every page as visual evidence, including vector-only
                # figures that neither a raster object nor layout model detects.
                if settings.get("render_all_pages", True):
                    rel = Path("page_images") / f"page_{number:04d}.png"
                    crop_region(page, page.rect, out_dir / rel, dpi)
                    pg["image_file"] = rel.as_posix()
                visual_content = bool(image_infos or drawings or page.get_text("text").strip())
                if pg["char_count"] == 0 and not visual_content:
                    pg["status"] = "blank"
                    pg["blank_evidence"] = "no_text_images_or_drawing_objects"
                elif (pg["char_count"] == 0 or pg["char_count"] < minimum) and (image_infos or drawings):
                    pg["status"] = "ocr_required"
                    pg["missing_regions"].append(
                        {
                            "bbox": [0, 0, page.rect.width, page.rect.height],
                            "reason": "text_layer_missing_or_sparse_visual_page",
                        }
                    )
                    pg["validation_messages"].append(message("text_layer_missing_or_sparse", number))
                else:
                    pg["status"] = "extracted"
                if drawings:
                    pg["validation_messages"].append(
                        message("vector_graphics_preserved_in_page_image", number, count=len(drawings))
                    )
                sizes = [
                    float(span.get("size", 0))
                    for block in blocks
                    for line in block["raw"].get("lines", [])
                    for span in line.get("spans", [])
                    if span.get("text", "").strip()
                ]
                body_size = median(sizes) if sizes else 0
                md = []
                for block in ordered:
                    # Only restore whitespace line wraps. Hyphens remain intact
                    # because removing them without lexical evidence is unsafe.
                    text = re.sub(r"(?<!-)\n(?=\S)", " ", block["text"].strip())
                    largest = max(
                        (
                            float(s.get("size", 0))
                            for ln in block["raw"].get("lines", [])
                            for s in ln.get("spans", [])
                        ),
                        default=0,
                    )
                    if body_size and largest >= body_size * 1.35 and len(text) < 180:
                        text = "## " + text
                        block["heading_level_candidate"] = 2
                    md.append(text)
                result["markdown_pages"][number] = "\n\n".join(md)
            except Exception as exc:
                pg["status"] = "failed"
                pg["validation_messages"].append(
                    message("page_extraction_failed", number, error_type=type(exc).__name__)
                )
                pg["missing_regions"].append({"bbox": None, "reason": "page_extraction_failed"})
                result["errors"].append(
                    message("page_extraction_failed", number, error_type=type(exc).__name__)
                )
        # Repeated marginal text is retained, not discarded as a guessed header.
        marginal = Counter(
            b["text"].strip()
            for b in result["text_blocks"]
            if b["bbox"][1] < 55 or b["bbox"][3] > (result["pages"][b["page_number"] - 1]["height"] or 0) - 55
        )
        for block in result["text_blocks"]:
            if marginal[block["text"].strip()] >= 2:
                block["role_candidate"] = "repeated_header_or_footer"
        return result
    finally:
        doc.close()
