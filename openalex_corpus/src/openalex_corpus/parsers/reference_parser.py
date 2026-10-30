"""Conservative bibliographic candidates; matching is exact-ID-only."""

from __future__ import annotations

import re

DOI = re.compile(r"\b10\.\d{4,9}/[^\s<>\"]+", re.I)
NUMBER = re.compile(r"^\s*(?:\[(\d+)\]|(\d+)[.)])\s+")
HEADING = re.compile(r"^\s*(?:\d+[. ]+)?(?:references|bibliography|literature cited)\s*$", re.I)


def normalize_doi(value: str) -> str:
    return re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value.strip(), flags=re.I).lower()


def extract_references(blocks: list[dict]) -> tuple[list[dict], list[dict]]:
    active = False
    candidates: list[dict] = []
    for block in blocks:
        lines = block.get("text", "").splitlines()
        for line in lines:
            if HEADING.match(line):
                active = True
                continue
            if not active:
                continue
            if re.match(r"^\s*(?:appendix|supplementary information|acknowledgements)\b", line, re.I):
                active = False
                continue
            if not line.strip():
                continue
            number = NUMBER.match(line)
            if number or not candidates:
                candidates.append(
                    {
                        "id": f"ref_{len(candidates) + 1:04d}",
                        "number": int(number.group(1) or number.group(2)) if number else None,
                        "original_reference_string": line,
                        "page_number": block["page_number"],
                        "bbox": block["bbox"],
                        "evidence": [
                            {"page_number": block["page_number"], "bbox": block["bbox"], "text": line}
                        ],
                        "authors": None,
                        "title": None,
                        "journal": None,
                        "year": None,
                        "volume": None,
                        "issue": None,
                        "pages": None,
                        "doi": None,
                        "url": None,
                        "openalex_id": None,
                        "source": "pdf_extracted",
                        "method": "reference_section_candidate",
                        "validation_status": "unverified",
                        "missing_reason": "bibliographic_fields_not_structurally_verified",
                    }
                )
            else:
                candidates[-1]["original_reference_string"] += "\n" + line
                candidates[-1]["evidence"].append(
                    {"page_number": block["page_number"], "bbox": block["bbox"], "text": line}
                )
    for ref in candidates:
        raw = ref["original_reference_string"]
        match = DOI.search(raw)
        if match:
            # Keep literal candidate punctuation; do not silently alter DOI.
            ref["doi_candidate"] = match.group(0)
        url = re.search(r"https?://\S+", raw)
        if url:
            ref["url_candidate"] = url.group(0)
    warnings = (
        [{"code": "references_structural_parser_unavailable", "page_number": None}]
        if candidates
        else [
            {
                "code": "references_not_detected",
                "page_number": None,
                "missing_reason": "not_detected_in_text_layer_not_proof_of_absence",
            }
        ]
    )
    return candidates, warnings


def link_exact_references(references: list[dict], known_identifiers: dict[str, list[str]]) -> list[dict]:
    """Only unique, explicit, structurally extracted DOI matches can be linked.

    A regexp DOI candidate or title similarity never qualifies.
    """
    for ref in references:
        doi = ref.get("doi")
        if not doi:
            continue
        ids = known_identifiers.get(normalize_doi(doi), [])
        if len(set(ids)) == 1:
            ref["openalex_id"] = ids[0]
            ref["link_method"] = "exact_unique_doi"
        elif ids:
            ref["openalex_id"] = None
            ref["link_missing_reason"] = "ambiguous_doi_multiple_work_ids"
    return references
