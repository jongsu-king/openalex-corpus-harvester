"""Shared status vocabulary; SQLite rows retain their full JSON payloads."""

from enum import StrEnum


class WorkStatus(StrEnum):
    COMPLETE = "COMPLETE"
    COMPLETE_WITH_WARNINGS = "COMPLETE_WITH_WARNINGS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    PDF_UNAVAILABLE = "PDF_UNAVAILABLE"
    PENDING_RETRY = "PENDING_RETRY"


class FieldStatus(StrEnum):
    PRESENT = "present"
    NOT_PROVIDED = "not_provided_by_source"
    NOT_APPLICABLE = "not_applicable"
    RETRIEVAL_FAILED = "retrieval_failed"
    PARSE_FAILED = "parse_failed"
    UNVERIFIED = "unverified"
