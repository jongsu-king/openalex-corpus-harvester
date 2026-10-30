"""Conservative quality gates: presence is verifiable; semantic completeness is not."""

import re
from collections import Counter
from pathlib import Path

from ..utils import sha256_file, utcnow

STATUSES = {"COMPLETE", "COMPLETE_WITH_WARNINGS", "PARTIAL", "FAILED", "PDF_UNAVAILABLE", "PENDING_RETRY"}


def check_extraction(result: dict, expected_pages: int, output_dir: Path) -> dict:
    issues = []

    def issue(code, message, severity="warning", page_number=None):
        issues.append(
            {
                "code": code,
                "message": message,
                "severity": severity,
                "page_number": page_number,
                "source": "quality_checker",
            }
        )

    pages = result.get("pages", [])
    numbers = [p.get("page_number") for p in pages]
    expected = set(range(1, expected_pages + 1))
    missing = sorted(expected - set(numbers))
    if missing or len(numbers) != len(set(numbers)) or set(numbers) != expected:
        issue(
            "PAGE_COVERAGE_FAILED",
            f"Expected pages 1..{expected_pages}; missing {missing}; got {numbers}",
            "error",
        )
    for page in pages:
        state = str(page.get("status", "unverified")).lower()
        if state in {"failed", "parse_failed", "partial", "ocr_required", "pending_ocr", "unprocessed"}:
            issue("PAGE_PARSE_FAILED", f"Page extraction status: {state}", "error", page.get("page_number"))
        if page.get("missing_regions"):
            issue("MISSING_REGIONS", "Page contains unresolved regions", "error", page.get("page_number"))
    md = result.get("markdown", "")
    total_chars = sum(p.get("char_count", 0) for p in pages)
    if not md.strip() and (total_chars or expected_pages):
        issue("MARKDOWN_EMPTY", "No extracted Markdown content", "error")
    elif total_chars > 300 and len(md) < total_chars * 0.25:
        issue(
            "SHORT_MARKDOWN", "Markdown is much shorter than text-layer evidence; inspect retained candidates"
        )
    if "\ufffd" in md:
        issue("UNICODE_REPLACEMENT", "Unicode replacement characters require source inspection")
    sentences = [line.strip() for line in md.splitlines() if len(line.strip()) > 70]
    for text, count in Counter(sentences).items():
        if count > 5:
            issue(
                "REPEATED_TEXT",
                f"A line repeats {count} times; may be header, OCR duplication, or source text",
            )
            break
    for reference in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", md):
        if reference.startswith(("https://", "http://", "data:")):
            issue("NONLOCAL_IMAGE", "Markdown contains a nonlocal image reference")
            continue
        target = (output_dir / reference.strip("<> ")).resolve()
        if not target.is_relative_to(output_dir.resolve()) or not target.is_file():
            issue("MISSING_MARKDOWN_IMAGE", f"Image reference unavailable: {reference}", "error")
    for equation in result.get("equations", []):
        if equation.get("status") in {"equation_parse_failed", "unverified", "parse_failed"}:
            issue(
                "EQUATION_UNVERIFIED",
                "Equation candidate retained with original region; LaTeX is unverified",
                page_number=equation.get("page_number"),
            )
    for table in result.get("tables", []):
        if table.get("validation_status") in {"failed", "parse_failed", "partial", "unverified"}:
            issue(
                "TABLE_UNVERIFIED",
                "Table candidate requires inspection of source region",
                page_number=table.get("page_number"),
            )
    for warning in result.get("warnings", []):
        if isinstance(warning, dict):
            issue(
                warning.get("code", "PARSER_WARNING"),
                warning.get("message", str(warning)),
                page_number=warning.get("page_number"),
            )
        else:
            issue("PARSER_WARNING", str(warning))
    for error in result.get("errors", []):
        issue(
            error.get("code", "PARSER_ERROR") if isinstance(error, dict) else "PARSER_ERROR",
            error.get("message", str(error)) if isinstance(error, dict) else str(error),
            "error",
        )
    if not str(result.get("engine", "")).startswith("docling"):
        issue(
            "FALLBACK_STRUCTURE_UNVERIFIED",
            "Primary structural parser was unavailable or disabled; retained native text order is a candidate",
        )
    if result.get("status") == "FAILED":
        status = "FAILED"
    elif any(i["severity"] == "error" for i in issues) or result.get("status") == "PARTIAL":
        status = "PARTIAL"
    elif issues or result.get("status") == "COMPLETE_WITH_WARNINGS":
        status = "COMPLETE_WITH_WARNINGS"
    else:
        status = "COMPLETE"
    checks = {
        "page_coverage": not missing and set(numbers) == expected and len(numbers) == len(expected),
        "markdown_created": bool(md.strip()),
        "image_references_resolve": not any(i["code"] == "MISSING_MARKDOWN_IMAGE" for i in issues),
        "equations": "unverified"
        if any(i["code"] == "EQUATION_UNVERIFIED" for i in issues)
        else "no_detected_failures",
        "references": "extracted_candidates" if result.get("references") else "not_detected",
    }
    return {
        "status": status,
        "checked_at": utcnow(),
        "expected_pages": expected_pages,
        "processed_pages": len(set(numbers) & expected),
        "missing_pages": missing,
        "checks": checks,
        "issues": issues,
        "scope": "Automated structural and integrity checks; COMPLETE is not proof of exact source reproduction.",
    }


def verify_manifest(manifest: dict, base_dir: Path) -> dict:
    failures = []
    for artifact in manifest.get("files", []):
        path = (base_dir / artifact["path"]).resolve()
        if not path.is_relative_to(base_dir.resolve()):
            failures.append({"path": artifact["path"], "code": "UNSAFE_ARTIFACT_PATH"})
        elif not path.is_file():
            failures.append({"path": artifact["path"], "code": "FILE_MISSING"})
        elif path.stat().st_size != artifact["size_bytes"] or sha256_file(path) != artifact["sha256"]:
            failures.append({"path": artifact["path"], "code": "CHECKSUM_MISMATCH"})
    return {"valid": not failures, "checked_files": len(manifest.get("files", [])), "failures": failures}
