"""Check bytes and PDF structure before any file receives a .pdf suffix."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any


def validate_pdf(path: Path | str) -> dict[str, Any]:
    path = Path(path)
    result: dict[str, Any] = {
        "status": "INVALID_PDF",
        "sha256": None,
        "size_bytes": 0,
        "page_count": None,
        "encrypted": None,
        "validation_status": "failed",
    }
    if not path.is_file():
        result["missing_reason"] = "file_not_found"
        return result
    result["size_bytes"] = path.stat().st_size
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        head = stream.read(1024)
        digest.update(head)
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    result["sha256"] = digest.hexdigest()
    if b"%PDF-" not in head:
        result["missing_reason"] = "pdf_signature_missing"
        return result
    try:
        import pymupdf

        with pymupdf.open(path) as document:
            result["encrypted"] = bool(document.is_encrypted or document.needs_pass)
            if result["encrypted"]:
                result.update(status="ENCRYPTED_PDF", missing_reason="password_or_encryption_present")
                return result
            if not document.is_pdf or document.page_count < 1:
                result["missing_reason"] = "no_pdf_pages"
                return result
            result["page_count"] = document.page_count
            # Loading every page catches invalid page trees before queueing a parser.
            for index in range(document.page_count):
                _ = document.load_page(index).rect
            if document.is_repaired:
                result["missing_reason"] = "pdf_required_structural_repair"
                return result
    except Exception as exc:
        result["missing_reason"] = "pdf_open_failed:" + type(exc).__name__
        return result
    result.update(status="VALID", validation_status="passed")
    return result
