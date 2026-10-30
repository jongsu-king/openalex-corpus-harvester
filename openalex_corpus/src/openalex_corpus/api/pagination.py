"""Cursor guards shared by CLI/pipeline consumers.

Persist the raw page and in-page offset before acknowledging a page. An empty
page with next_cursor=None is normal; a nonempty repeated cursor is an error.
The API is a live dataset, not a transactional snapshot. Store count changes.
"""

from __future__ import annotations

from typing import Any

from .client import APIError


def next_cursor(page: dict[str, Any], current_cursor: str) -> str | None:
    value = page["meta"].get("next_cursor")
    if value is not None and not isinstance(value, str):
        raise APIError("Malformed next_cursor", "CURSOR_INVALID")
    if value == current_cursor:
        raise APIError("OpenAlex repeated the same cursor; checkpoint retained", "CURSOR_LOOP")
    if not page.get("results") and value:
        raise APIError(
            "Empty results with continuation cursor; refusing to skip a possible page", "CURSOR_EMPTY_PAGE"
        )
    return value
