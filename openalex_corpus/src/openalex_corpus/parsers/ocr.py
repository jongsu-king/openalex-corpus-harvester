"""OCR is an explicit, bounded derivative; source PDF is never rewritten."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

from ..download.validator import validate_pdf
from ..utils import sha256_file


def ocr_binary() -> str | None:
    """Prefer the active Python environment even when its bin is not on PATH."""
    local = Path(sys.executable).parent / "ocrmypdf"
    if local.is_file() and os.access(local, os.X_OK):
        return str(local)
    return shutil.which("ocrmypdf")


def run_ocr(pdf_path: Path, out_dir: Path, pages: list[int], settings: dict) -> dict:
    if not pages:
        return {"status": "not_required"}
    if not settings.get("enable_ocr", False):
        return {"status": "unavailable", "missing_reason": "ocr_disabled", "pages": pages}
    binary = ocr_binary()
    if not binary:
        return {"status": "unavailable", "missing_reason": "ocrmypdf_not_installed", "pages": pages}
    out_dir.mkdir(parents=True, exist_ok=True)
    derivative = out_dir / "ocr_derivative.pdf"
    if derivative.exists():
        raise ValueError("Refusing to overwrite an existing OCR derivative")
    original = validate_pdf(pdf_path)
    if original["status"] != "VALID":
        return {
            "status": "failed",
            "missing_reason": "ocr_input_pdf_invalid",
            "pages": pages,
            "input_validation": original,
        }
    if any(type(page) is not int or page < 1 or page > original["page_count"] for page in pages):
        return {"status": "failed", "missing_reason": "ocr_page_selection_invalid", "pages": pages}
    pages = sorted(set(pages))
    # --redo-ocr masks visible digital text and OCRs additional image text.
    # Only selected sparse/scanned pages are processed; original PDF is retained.
    args = [
        binary,
        "--redo-ocr",
        "--pages",
        ",".join(map(str, pages)),
        "--jobs",
        "1",
        "--language",
        "eng",
        "--output-type",
        "pdf",
        "--optimize",
        "0",
        "--sidecar",
        str(out_dir / "ocr_sidecar.txt"),
        str(pdf_path),
        str(derivative),
    ]
    # Kill the whole subprocess group on timeout so Tesseract descendants do
    # not survive an interrupted OCR worker.
    env = dict(os.environ)
    env["PATH"] = str(Path(binary).parent) + os.pathsep + env.get("PATH", "")
    proc = subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True, env=env
    )
    try:
        _, stderr = proc.communicate(timeout=float(settings.get("ocr_timeout", 300)))
        (out_dir / "ocr.log").write_text(stderr, encoding="utf-8")
        if proc.returncode != 0 or not derivative.exists():
            return {
                "status": "failed",
                "missing_reason": "ocr_process_failed",
                "exit_code": proc.returncode,
                "pages": pages,
            }
        output = validate_pdf(derivative)
        source_unchanged = sha256_file(pdf_path) == original["sha256"]
        if (
            not source_unchanged
            or output["status"] != "VALID"
            or output["page_count"] != original["page_count"]
        ):
            return {
                "status": "failed",
                "missing_reason": "ocr_integrity_validation_failed",
                "pages": pages,
                "source_unchanged": source_unchanged,
                "input_validation": original,
                "output_validation": output,
            }
        return {
            "status": "success",
            "path": str(derivative),
            "pages": pages,
            "source": "ocr_extracted",
            "method": "ocrmypdf_tesseract",
            "language": "eng",
            "input_sha256": original["sha256"],
            "output_sha256": output["sha256"],
            "input_page_count": original["page_count"],
            "output_page_count": output["page_count"],
            "source_unchanged": source_unchanged,
        }
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            _, stderr = proc.communicate(timeout=5)
        except (subprocess.TimeoutExpired, ProcessLookupError):
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            _, stderr = proc.communicate()
        (out_dir / "ocr.log").write_text(stderr or "", encoding="utf-8")
        return {"status": "failed", "missing_reason": "ocr_timeout", "pages": pages}
