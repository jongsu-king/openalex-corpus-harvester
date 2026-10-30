"""Bounded first-page query inventory, never an exhaustive corpus claim."""

from __future__ import annotations

import asyncio
import json
import math
from collections import Counter
from decimal import Decimal
from pathlib import Path

import yaml

from openalex_corpus.api.client import OpenAlexClient
from openalex_corpus.config import APISettings
from openalex_corpus.utils import atomic_json, atomic_text, sha256_file, utcnow

ROOT = Path(__file__).resolve().parents[1]


async def discover():
    definitions_path = ROOT / "config/opv_searches.yaml"
    definitions = yaml.safe_load(definitions_path.read_text())
    output = ROOT / "data/opv_discovery"
    output.mkdir(parents=True, exist_ok=True)
    budget_scope = output / "discovery_budget_scope.json"
    journal_path = ROOT / "data/state/network_budget.json"
    current_journal = json.loads(journal_path.read_text()) if journal_path.exists() else {}
    if budget_scope.exists():
        budget = json.loads(budget_scope.read_text())
    else:
        budget = {
            "day": utcnow()[:10],
            "baseline_requests": current_journal.get("metadata_requests", 0),
            "baseline_reserved_usd": current_journal.get("metadata_reserved_usd", "0"),
            "max_additional_requests": 10,
            "max_additional_usd": "0.01",
        }
        atomic_json(budget_scope, budget)
    if budget["day"] != utcnow()[:10]:
        raise ValueError(
            "Discovery's original daily budget scope expired; use the resumable OPV harvester for further collection"
        )
    settings = APISettings(
        daily_request_budget=budget["baseline_requests"] + budget["max_additional_requests"],
        daily_budget_usd=float(
            Decimal(budget["baseline_reserved_usd"]) + Decimal(budget["max_additional_usd"])
        ),
        max_attempts=1,
        requests_per_second=0.7,
    )
    summary = {
        "recorded_at": utcnow(),
        "status": "FIRST_PAGE_INVENTORY_ONLY",
        "definitions_sha256": sha256_file(definitions_path),
        "queries": [],
        "source": "https://api.openalex.org/works",
        "metadata_only": True,
        "api_key_present": False,
        "pages_followed": 0,
    }
    members = {}
    raw_works = {}
    async with OpenAlexClient(settings, ROOT / "data/state") as client:
        summary["api_key_present"] = bool(client.api_key)
        for definition in definitions["queries"]:
            qid = definition["id"]
            params = {definitions["search_parameter"]: definition["query"], "per_page": 100}
            raw_path = output / "pages" / f"{qid}.json"
            receipt_path = raw_path.with_suffix(".receipt.json")
            if raw_path.exists() and receipt_path.exists():
                receipt = json.loads(receipt_path.read_text())
                if receipt["params"] != params or receipt["sha256"] != sha256_file(raw_path):
                    raise ValueError(f"Retained discovery receipt mismatch: {qid}")
                page = json.loads(raw_path.read_text())
            else:
                page = await client.get_page(params, cursor="*")
                atomic_json(raw_path, page)
                receipt = {
                    "retrieved_at": utcnow(),
                    "params": params,
                    "cursor": "*",
                    "sha256": sha256_file(raw_path),
                    "rate_headers": client.last_rate_headers,
                }
                atomic_json(receipt_path, receipt)
            count = page["meta"]["count"]
            rows = page["results"]
            ids = {row["id"] for row in rows}
            members[qid] = ids
            for row in rows:
                raw_works.setdefault(row["id"], row)
            summary["queries"].append(
                {
                    "id": qid,
                    "params": params,
                    "reported_total": count,
                    "first_page_records": len(rows),
                    "first_page_unique": len(ids),
                    "estimated_pages_at_100": math.ceil(count / 100),
                    "next_cursor": page["meta"]["next_cursor"],
                    "raw_path": str(raw_path.relative_to(ROOT)),
                    "receipt_path": str(receipt_path.relative_to(ROOT)),
                }
            )
            atomic_json(output / "summary.json", summary)
        summary["network_requests_this_execution"] = client.request_count
    summary["unique_work_ids_in_first_pages"] = len(raw_works)
    summary["record_occurrences_in_first_pages"] = sum(len(v) for v in members.values())
    summary["duplicate_occurrences_in_first_pages"] = summary["record_occurrences_in_first_pages"] - len(
        raw_works
    )
    summary["first_page_overlap"] = {a: {b: len(members[a] & members[b]) for b in members} for a in members}
    summary["source_type_counts_in_first_pages"] = dict(
        Counter(row.get("type") for row in raw_works.values())
    )
    summary["selected_inventory"] = definitions["selected_inventory"]
    summary["coverage_status"] = (
        "INCOMPLETE_API_KEY_REQUIRED" if not summary["api_key_present"] else "FIRST_PAGES_ONLY"
    )
    summary["all_world_literature_complete"] = False
    final_journal = json.loads(journal_path.read_text())
    summary["discovery_requests_including_rejected_queries"] = (
        final_journal["metadata_requests"] - budget["baseline_requests"]
    )
    summary["discovery_reserved_usd_including_rejected_queries"] = str(
        Decimal(final_journal["metadata_reserved_usd"]) - Decimal(budget["baseline_reserved_usd"])
    )
    atomic_json(output / "summary.json", summary)
    atomic_text(
        output / "first_pages_unique_raw.jsonl",
        "".join(json.dumps(raw, ensure_ascii=False) + "\n" for _, raw in sorted(raw_works.items())),
    )
    atomic_json(
        output / "first_pages_index.json",
        [
            {
                "id": wid,
                "title": row.get("title"),
                "doi": row.get("doi"),
                "year": row.get("publication_year"),
                "query_memberships": [qid for qid, ids in members.items() if wid in ids],
                "source_title_review_flags": [
                    term
                    for term in ("perovskite", "polio", "vaccine", "organic-inorganic")
                    if term in (row.get("title") or "").lower()
                ],
            }
            for wid, row in sorted(raw_works.items())
        ],
    )
    print(
        json.dumps(
            {k: v for k, v in summary.items() if k != "first_page_overlap"}, ensure_ascii=False, indent=2
        )
    )


if __name__ == "__main__":
    asyncio.run(discover())
