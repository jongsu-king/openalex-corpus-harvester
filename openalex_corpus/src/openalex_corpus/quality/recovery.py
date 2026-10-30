"""Bounded recovery policy, separate from extraction and evidence selection."""

from datetime import datetime, timedelta, timezone


def retry_at(attempt: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=min(3600, 30 * 2**attempt))).isoformat()


def recovery_options(settings: dict, previous_quality: dict, attempt: int) -> dict | None:
    if attempt >= settings.get("max_recovery_attempts", 2):
        return None
    options = dict(settings)
    codes = {issue.get("code") for issue in previous_quality.get("issues", [])}
    if codes & {"PAGE_PARSE_FAILED", "MISSING_REGIONS", "MARKDOWN_EMPTY"} and settings.get("enable_ocr"):
        options["enable_ocr"] = True
        options["enable_docling"] = False
        options["primary_engine"] = "pymupdf"
        return options
    if settings.get("enable_docling"):
        options["enable_docling"] = False
        options["primary_engine"] = "pymupdf"
        return options
    return None
