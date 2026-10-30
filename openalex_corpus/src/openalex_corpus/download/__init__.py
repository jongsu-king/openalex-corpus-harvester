"""Validated and version-preserving PDF downloads."""

from .pdf_downloader import PDFDownloader, pdf_candidates
from .validator import validate_pdf

__all__ = ["PDFDownloader", "pdf_candidates", "validate_pdf"]
