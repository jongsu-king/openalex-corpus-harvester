"""Optional TEI evidence, with no metadata consolidation or entity guessing."""

from __future__ import annotations

import re
from pathlib import Path

import httpx
from lxml import etree

NS = {"tei": "http://www.tei-c.org/ns/1.0"}


def _text(node) -> str | None:
    return " ".join(part.strip() for part in node.itertext() if part.strip()) if node is not None else None


def _first(node, path: str) -> str | None:
    found = node.xpath(path, namespaces=NS)
    return _text(found[0]) if found else None


def _coords(value: str | None) -> list[dict]:
    result = []
    for segment in (value or "").split(";"):
        try:
            p, x, y, w, h = [float(v) for v in segment.split(",")]
            result.append({"page_number": int(p), "bbox": [x, y, x + w, y + h]})
        except (ValueError, TypeError):
            continue
    return result


def parse_tei(path: Path, source: str = "grobid") -> dict:
    raw = path.read_bytes()
    # Disallow a DTD entirely, even though network/entity resolution is disabled.
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError("TEI DTD/entity declarations are not accepted")
    root = etree.fromstring(
        raw, etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False)
    )
    references = []
    for index, ref in enumerate(root.xpath(".//tei:listBibl/tei:biblStruct", namespaces=NS), 1):
        coords = _coords(ref.get("coords"))
        scopes = {s.get("unit"): s for s in ref.xpath(".//tei:imprint/tei:biblScope", namespaces=NS)}
        pages_node = scopes.get("page")
        if pages_node is not None:
            pages = _text(pages_node) or (
                f"{pages_node.get('from')}-{pages_node.get('to')}"
                if pages_node.get("from") and pages_node.get("to")
                else pages_node.get("from")
            )
        else:
            pages = None
        year_node = ref.xpath(".//tei:imprint/tei:date", namespaces=NS)
        date = year_node[0].get("when") if year_node else None
        raw_ref = _first(ref, ".//tei:note[@type='raw_reference']")
        doi = _first(ref, ".//tei:idno[@type='DOI']")
        references.append(
            {
                "id": f"tei_ref_{index:04d}",
                "tei_id": ref.get("{http://www.w3.org/XML/1998/namespace}id"),
                "number": None,
                "authors": [_text(n) for n in ref.xpath(".//tei:author/tei:persName", namespaces=NS)],
                "title": _first(ref, ".//tei:analytic/tei:title"),
                "journal": _first(ref, ".//tei:monogr/tei:title"),
                "year": date[:4] if date and re.match(r"\d{4}", date) else None,
                "volume": _text(scopes.get("volume")),
                "issue": _text(scopes.get("issue")),
                "pages": pages,
                "doi": doi,
                "url": next(iter(ref.xpath(".//tei:ptr/@target", namespaces=NS)), None),
                "original_reference_string": raw_ref,
                "raw_xml": etree.tostring(ref, encoding="unicode"),
                "page_number": coords[0]["page_number"] if coords else None,
                "bbox": coords[0]["bbox"] if coords else None,
                "evidence": coords,
                "source": source,
                "method": "tei_bibliography",
                "validation_status": "unverified",
                "missing_reason": None if raw_ref else "raw_reference_not_provided_in_tei",
                "openalex_id": None,
            }
        )
    sections = []
    for el in root.xpath(
        ".//tei:text/tei:body//tei:head | .//tei:text/tei:body//tei:p | .//tei:formula | .//tei:figure",
        namespaces=NS,
    ):
        sections.append(
            {
                "tag": etree.QName(el).localname,
                "text": _text(el),
                "coordinates": _coords(el.get("coords")),
                "raw_xml": etree.tostring(el, encoding="unicode"),
                "source": source,
                "validation_status": "unverified",
            }
        )
    return {
        "source": source,
        "software": [
            {
                "ident": app.get("ident"),
                "version": app.get("version"),
                "when": app.get("when"),
            }
            for app in root.xpath(
                ".//tei:teiHeader/tei:encodingDesc/tei:appInfo/tei:application", namespaces=NS
            )
        ],
        "references": references,
        "sections": sections,
        "metadata": {
            "title": _first(root, ".//tei:teiHeader//tei:titleStmt/tei:title"),
            "authors": [
                _text(n)
                for n in root.xpath(
                    ".//tei:teiHeader//tei:sourceDesc//tei:author/tei:persName", namespaces=NS
                )
            ],
        },
    }


def request_tei(pdf_path: Path, out: Path, base_url: str, timeout: float = 120) -> dict:
    endpoint = base_url.rstrip("/") + "/api/processFulltextDocument"
    # No Crossref/biblio-glutton enrichment: every extracted field must trace
    # back to PDF/TEI. Explicitly override GROBID's header-consolidation default.
    data = [
        ("consolidateHeader", "0"),
        ("consolidateCitations", "0"),
        ("consolidateFunders", "0"),
        ("includeRawCitations", "1"),
        ("includeRawAffiliations", "1"),
        ("teiCoordinates", "ref"),
        ("teiCoordinates", "biblStruct"),
        ("teiCoordinates", "figure"),
        ("teiCoordinates", "formula"),
        ("teiCoordinates", "p"),
    ]
    with httpx.Client(timeout=timeout) as client, pdf_path.open("rb") as pdf:
        response = client.post(
            endpoint,
            files={"input": (pdf_path.name, pdf, "application/pdf")},
            data={
                key: [v for k, v in data if k == key] if key == "teiCoordinates" else value
                for key, value in data
            },
            headers={"Accept": "application/xml"},
        )
        response.raise_for_status()
        if response.status_code == 204 or not response.content:
            raise ValueError("GROBID returned no TEI content")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(response.content)
    return parse_tei(out)
