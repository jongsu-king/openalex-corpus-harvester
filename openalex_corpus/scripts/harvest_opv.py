"""Collect a reproducible OPV candidate inventory, with explicit key and budget controls."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from openalex_corpus.config import load_settings
from openalex_corpus.opv import load_api_key_file, run_inventory
from openalex_corpus.utils import configure_logging, redact

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config/opv.yaml")
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Explicit .env file; reads OPENALEX_API_KEY without executing shell code",
    )
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument(
        "--max-works", type=int, default=10000, help="Record cap for this invocation; resume next invocation"
    )
    parser.add_argument(
        "--all",
        dest="all_matches",
        action="store_true",
        help="Follow targeted query to terminal cursor, subject to unchanged daily budgets",
    )
    parser.add_argument("--chunk-size", type=int, default=1000)
    parser.add_argument(
        "--daily-budget-usd", type=float, help="Explicit local daily metadata cap; does not purchase credits"
    )
    parser.add_argument("--daily-request-budget", type=int, help="Explicit local daily HTTP attempt cap")
    parser.add_argument("--fresh", action="store_true", help="Start this query again; prior records remain")
    parser.add_argument("--download-pdf", action="store_true")
    parser.add_argument("--parse-markdown", action="store_true")
    parser.add_argument(
        "--seed-page", type=Path, default=ROOT / "data/opv_discovery/pages/expanded_union.json"
    )
    args = parser.parse_args()
    try:
        if args.env_file:
            load_api_key_file(args.env_file)
        settings = load_settings(args.config)
        if args.data_dir:
            settings.data_dir = args.data_dir.resolve()
        updates = {}
        if args.daily_budget_usd is not None:
            updates["daily_budget_usd"] = args.daily_budget_usd
        if args.daily_request_budget is not None:
            updates["daily_request_budget"] = args.daily_request_budget
        settings.api = type(settings.api).model_validate({**settings.api.model_dump(), **updates})
        configure_logging(settings.log_dir)
        report = asyncio.run(
            run_inventory(
                settings,
                max_works=args.max_works,
                all_matches=args.all_matches,
                chunk_size=args.chunk_size,
                fresh=args.fresh,
                download_pdf=args.download_pdf,
                parse_markdown=args.parse_markdown,
                seed_page=args.seed_page,
            )
        )
        print(json.dumps(redact(report), ensure_ascii=False, indent=2))
        return 2 if report.get("requires_attention") or not report["database_integrity"]["ok"] else 0
    except KeyboardInterrupt:
        print(json.dumps({"status": "PAUSED", "reason": "interrupted; rerun the same command to resume"}))
        return 130
    except Exception as error:
        print(
            json.dumps(
                redact(
                    {
                        "status": "FAILED",
                        "code": getattr(error, "code", type(error).__name__),
                        "error": str(error),
                    }
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
