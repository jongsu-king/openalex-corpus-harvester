"""Lossless, source-labelled projections of OpenAlex Work records.

The original record is always stored separately. This module never invents missing
names, identifiers, abstract tokens, or publication facts.
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any
from urllib.parse import unquote


def work_id(value: str) -> str:
    """Return the canonical local OpenAlex Work ID; reject non-work identifiers."""
    value = str(value).strip().rstrip("/").rsplit("/", 1)[-1].upper()
    if not re.fullmatch(r"W[0-9]+", value):
        raise ValueError("Expected an OpenAlex Work ID (W followed by digits)")
    return value


def normalize_doi(value: str | None) -> str | None:
    if value is None:
        return None
    value = unquote(str(value)).strip()
    value = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi\s*:\s*)", "", value, flags=re.I)
    return value.lower() or None


def restore_abstract(index: dict[str, list[int]] | None) -> dict[str, Any]:
    """Reconstruct only an unambiguous, gap-free abstract inverted index.

    Ambiguous indexes retain every candidate and are explicitly failed; a partial
    sentence without placeholders must not masquerade as a complete abstract.
    """
    if index is None:
        return {"text": None, "status": "not_provided_by_source", "issues": [], "positions": []}
    if not isinstance(index, dict):
        return {
            "text": None,
            "status": "parse_failed",
            "issues": ["invalid_inverted_index_type"],
            "positions": [],
        }
    slots: dict[int, list[str]] = {}
    issues: list[str] = []
    for token, positions in index.items():
        if not isinstance(token, str) or not isinstance(positions, list):
            issues.append("invalid_token_or_positions")
            continue
        for position in positions:
            if isinstance(position, bool) or not isinstance(position, int) or position < 0:
                issues.append("invalid_position")
                continue
            values = slots.setdefault(position, [])
            if token not in values:
                values.append(token)
    ordered = [{"position": key, "tokens": slots[key]} for key in sorted(slots)]
    if any(len(tokens) > 1 for tokens in slots.values()):
        issues.append("conflicting_tokens_at_position")
    if slots and (min(slots) != 0 or len(slots) != max(slots) + 1):
        issues.append("missing_abstract_positions")
    if issues:
        return {"text": None, "status": "parse_failed", "issues": sorted(set(issues)), "positions": ordered}
    return {
        "text": " ".join(slots[key][0] for key in sorted(slots)),
        "status": "present",
        "issues": [],
        "positions": ordered,
    }


def _field_state(value: Any, source_key: str, exists: bool = True) -> dict[str, Any]:
    missing = not exists or value is None
    return {
        "status": "not_provided_by_source" if missing else "present",
        "missing_reason": "source_field_absent" if not exists else "source_null" if value is None else None,
        "source": "openalex",
        "source_field": source_key,
        "retrieval_status": "retrieved",
        "validation_status": "unverified" if not missing else "not_applicable",
    }


def _nested_states(raw: dict[str, Any]) -> dict[str, dict]:
    """Label source paths without expanding scalar arrays (e.g. token positions)."""
    states: dict[str, dict] = {}

    def walk(value: Any, path: str) -> None:
        if path:
            states[path] = _field_state(value, path)
        if isinstance(value, dict):
            for key, child in value.items():
                walk(child, f"{path}.{key}" if path else key)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                if isinstance(child, dict):
                    walk(child, f"{path}[{index}]")

    def expect(obj: Any, prefix: str, keys: tuple[str, ...]) -> None:
        obj = obj if isinstance(obj, dict) else {}
        for key in keys:
            path = f"{prefix}.{key}"
            states.setdefault(path, _field_state(obj.get(key), path, key in obj))

    walk(raw, "")
    expect(raw.get("ids"), "ids", ("openalex", "doi", "pmid", "pmcid", "arxiv"))
    expect(raw.get("biblio"), "biblio", ("volume", "issue", "first_page", "last_page"))
    expect(raw.get("open_access"), "open_access", ("is_oa", "oa_status", "oa_url"))
    for index, authorship in enumerate(raw.get("authorships") or []):
        if not isinstance(authorship, dict):
            continue
        prefix = f"authorships[{index}]"
        expect(
            authorship,
            prefix,
            (
                "author",
                "author_position",
                "is_corresponding",
                "institutions",
                "raw_author_name",
                "raw_affiliation_strings",
            ),
        )
        expect(authorship.get("author"), f"{prefix}.author", ("id", "display_name", "orcid"))
        for index2, institution in enumerate(authorship.get("institutions") or []):
            expect(
                institution,
                f"{prefix}.institutions[{index2}]",
                ("id", "display_name", "ror", "country_code", "type"),
            )
    locations = [
        (f"locations[{index}]", location) for index, location in enumerate(raw.get("locations") or [])
    ]
    locations += [(key, raw.get(key)) for key in ("primary_location", "best_oa_location")]
    for prefix, location in locations:
        expect(location, prefix, ("is_oa", "landing_page_url", "pdf_url", "license", "version", "source"))
        source = location.get("source") if isinstance(location, dict) else None
        expect(
            source,
            f"{prefix}.source",
            ("id", "display_name", "issn_l", "issn", "type", "host_organization", "host_organization_name"),
        )
    return states


def normalize_work(raw: dict[str, Any]) -> dict[str, Any]:
    """Preserve source values (including null/empty), add explicit projections."""
    if not isinstance(raw, dict):
        raise ValueError("OpenAlex Work response must be an object")
    identifier = work_id(raw.get("id", ""))
    result = deepcopy(raw)
    result["openalex_id"] = identifier
    result["doi"] = normalize_doi(raw.get("doi"))
    result["doi_raw"] = raw.get("doi")
    # A title may be null while display_name is present. Do not silently substitute
    # one for the other; both source fields remain independently represented.
    result["title"] = raw.get("title")
    result["display_name"] = raw.get("display_name")
    abstract = restore_abstract(raw.get("abstract_inverted_index"))
    result["abstract_text"] = abstract["text"]
    result["abstract_reconstruction"] = abstract
    result["source"] = "openalex"
    result["retrieval_status"] = "retrieved"
    result["validation_status"] = "unverified"
    states = _nested_states(raw)
    expected = (
        "id",
        "doi",
        "ids",
        "title",
        "display_name",
        "publication_year",
        "publication_date",
        "type",
        "language",
        "created_date",
        "updated_date",
        "abstract_inverted_index",
        "open_access",
        "best_oa_location",
        "primary_location",
        "locations",
        "content_urls",
        "has_content",
        "authorships",
        "biblio",
        "referenced_works",
        "related_works",
        "cited_by_count",
        "topics",
        "keywords",
        "concepts",
        "grants",
        "funding",
        "sustainable_development_goals",
    )
    for key in expected:
        states.setdefault(key, _field_state(raw.get(key), key, key in raw))
    states["abstract_text"] = {
        "status": abstract["status"],
        "missing_reason": ";".join(abstract["issues"])
        or ("source_null" if abstract["text"] is None else None),
        "source": "openalex.abstract_inverted_index",
        "source_field": "abstract_inverted_index",
        "retrieval_status": "retrieved",
        "validation_status": "passed" if abstract["status"] == "present" else abstract["status"],
    }
    result["field_status"] = states
    return result
