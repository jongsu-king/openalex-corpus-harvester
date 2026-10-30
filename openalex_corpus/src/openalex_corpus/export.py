"""Streaming exports; nested data stays JSON, never silently flattened away."""

import csv
import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path

from .utils import atomic_json


def export_corpus(repo, destination: Path, format: str) -> dict:
    destination = destination.resolve()
    protected_roots = [
        Path(row[0]).resolve() for row in repo.conn.execute("SELECT DISTINCT paper_dir FROM works")
    ]
    protected_paths = [
        repo.db_path.resolve(),
        *(Path(row[0]).resolve() for row in repo.conn.execute("SELECT path FROM artifacts")),
    ]
    if destination in protected_paths or any(
        destination.is_relative_to(root) or root.is_relative_to(destination) for root in protected_roots
    ):
        raise ValueError("Export destination overlaps authoritative corpus data")
    corpus_root = repo.db_path.parent.parent.resolve()
    if destination.is_relative_to(corpus_root) and not destination.is_relative_to(corpus_root / "exports"):
        raise ValueError("Exports inside the corpus must be placed under data/exports")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if format == "sqlite":
        repo.backup(destination)
        return {"path": str(destination), "format": format}
    if format == "markdown":
        if destination.exists():
            raise FileExistsError("Use a new Markdown export directory to preserve previous exports")
        staging = destination.with_name("." + destination.name + "." + uuid.uuid4().hex + ".tmp")
        staging.mkdir()
        count = 0
        try:
            for row in repo.iter_works():
                directory = Path(row["paper_dir"])
                pointer = directory / "current.json"
                if pointer.exists():
                    current = json.loads(pointer.read_text())
                    source = (directory / current["run"]).resolve()
                    if not source.is_relative_to(directory.resolve()):
                        raise ValueError("Unsafe extraction directory in current.json")
                    from .quality.checker import verify_manifest

                    manifest = json.loads((source / "manifest.json").read_text())
                    if not verify_manifest(manifest, source)["valid"]:
                        raise ValueError(f"Cannot export damaged extraction {row['id']}")
                    # Export every supporting artifact so relative Markdown links resolve.
                    shutil.copytree(source, staging / row["id"])
                    count += 1
            os.replace(staging, destination)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        return {"path": str(destination), "format": format, "count": count}
    fd, temp = tempfile.mkstemp(dir=destination.parent, prefix=".export-", suffix=".tmp")
    os.close(fd)
    count = 0
    writer = None
    try:
        if format == "parquet":
            import pyarrow as pa
            import pyarrow.parquet as pq

            schema = pa.schema(
                [
                    ("id", pa.string()),
                    ("doi", pa.string()),
                    ("title", pa.string()),
                    ("publication_year", pa.int64()),
                    ("status", pa.string()),
                    ("missing_reason", pa.string()),
                    ("metadata_json", pa.string()),
                    ("raw_json", pa.string()),
                ]
            )
            writer = pq.ParquetWriter(temp, schema=schema, compression="zstd")
            batch = []
            for row in repo.iter_works():
                batch.append(_flat_row(row))
                count += 1
                if len(batch) == 1000:
                    writer.write_table(pa.Table.from_pylist(batch, schema=schema))
                    batch = []
            if batch:
                writer.write_table(pa.Table.from_pylist(batch, schema=schema))
            writer.close()
            writer = None
        else:
            with open(temp, "w", encoding="utf-8", newline="") as stream:
                csv_writer = None
                if format == "csv":
                    fields = [
                        "id",
                        "doi",
                        "title",
                        "publication_year",
                        "status",
                        "missing_reason",
                        "metadata_json",
                        "raw_json",
                    ]
                    csv_writer = csv.DictWriter(stream, fieldnames=fields)
                    csv_writer.writeheader()
                elif format == "json":
                    stream.write("[\n")
                elif format != "jsonl":
                    raise ValueError(f"Unsupported export format: {format}")
                for row in repo.iter_works():
                    if csv_writer:
                        csv_writer.writerow(_flat_row(row))
                    else:
                        record = {
                            "id": row["id"],
                            "status": row["status"],
                            "missing_reason": row["missing_reason"],
                            "metadata": row["metadata"],
                            "raw": row["raw"],
                        }
                        if format == "json" and count:
                            stream.write(",\n")
                        stream.write(json.dumps(record, ensure_ascii=False))
                        if format == "jsonl":
                            stream.write("\n")
                    count += 1
                if format == "json":
                    stream.write("\n]\n")
                stream.flush()
                os.fsync(stream.fileno())
        os.replace(temp, destination)
        atomic_json(
            destination.with_suffix(destination.suffix + ".schema.json"),
            {
                "format": format,
                "records": count,
                "nested_fields": ["metadata_json", "raw_json"],
                "note": "CSV/Parquet nested fields are JSON strings preserving original null and list values; raw files remain authoritative.",
            },
        )
    finally:
        if writer:
            writer.close()
        Path(temp).unlink(missing_ok=True)
    return {"path": str(destination), "format": format, "count": count}


def _flat_row(row):
    return {
        "id": row["id"],
        "doi": row["doi"],
        "title": row["title"],
        "publication_year": row["publication_year"],
        "status": row["status"],
        "missing_reason": row["missing_reason"],
        "metadata_json": json.dumps(row["metadata"], ensure_ascii=False),
        "raw_json": json.dumps(row["raw"], ensure_ascii=False),
    }
