#!/usr/bin/env python3
"""Prepare only Docling layout/TableFormer model families for offline parsing.

This opt-in command may access Hugging Face and download model weights. It does
not call OpenAlex or download OCR/VLM/formula/caption/embedding model families.
"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
from importlib.metadata import version
from pathlib import Path


def restricted_flags(download_function, *, layout: bool, tableformer: bool) -> dict:
    signature = inspect.signature(download_function)
    # Docling's defaults enable some unwanted families. Explicitly disable ALL
    # named with_* flags, including new ones introduced by a supported release.
    flags = {name: False for name in signature.parameters if name.startswith("with_")}
    if not {"with_layout", "with_tableformer"} <= set(flags):
        raise RuntimeError("Installed Docling model downloader has an unsupported interface")
    flags.update(with_layout=layout, with_tableformer=tableformer)
    for field in ("whisper_models", "whisper_distil_models", "whisper_s2t_models"):
        if field in signature.parameters:
            flags[field] = None
    return flags


def checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prepare(output_dir: Path) -> dict:
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.models.stages.table_structure.table_structure_model import TableStructureModel
    from docling.utils.model_downloader import download_models

    from openalex_corpus.utils import atomic_json

    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    options = PdfPipelineOptions()
    layout_spec = options.layout_options.model_spec
    layout_dir = output_dir / layout_spec.repo_id.replace("/", "--")
    table_dir = output_dir / TableStructureModel._model_repo_folder
    has_layout = (layout_dir / "config.json").is_file() and any(layout_dir.glob("*.safetensors"))
    accurate_dir = table_dir / "model_artifacts" / "tableformer" / "accurate"
    has_table = (accurate_dir / "tm_config.json").is_file() and any(accurate_dir.glob("*.safetensors"))
    if not has_layout or not has_table:
        download_models(
            output_dir=output_dir,
            force=False,
            progress=True,
            **restricted_flags(download_models, layout=not has_layout, tableformer=not has_table),
        )
    if not ((layout_dir / "config.json").is_file() and (accurate_dir / "tm_config.json").is_file()):
        raise RuntimeError("Required model files are missing after preparation")
    files = []
    # Directory roots may be symlinks to a shared Hugging Face cache. Traverse
    # each explicitly and hash the actual weights to record the reproducible input.
    for root in (layout_dir, table_dir):
        for path in sorted(root.rglob("*")):
            if (
                path.is_file()
                and ".cache" not in path.parts
                and path.suffix in {".json", ".safetensors", ".bin", ".pt", ".pth"}
            ):
                files.append(
                    {
                        "path": str(path.relative_to(output_dir)),
                        "sha256": checksum(path),
                        "size_bytes": path.stat().st_size,
                    }
                )
    manifest = {
        "docling_version": version("docling"),
        "output_dir": str(output_dir),
        "layout": layout_spec.model_dump(mode="json"),
        "tableformer": options.table_structure_options.model_dump(mode="json"),
        "allowed_model_families": ["layout", "tableformer"],
        "resolved_roots": {"layout": str(layout_dir.resolve()), "tableformer": str(table_dir.resolve())},
        "files": files,
        "note": "The installed Docling helper may prefetch layout-engine variants within the layout family; no other model family is enabled.",
    }
    atomic_json(output_dir / "model_manifest.json", manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("data/models/docling"))
    args = parser.parse_args()
    try:
        manifest = prepare(args.output_dir)
    except ImportError as error:
        parser.exit(2, f"Docling is not installed: {error}. Install the docling extra first.\n")
    print(
        json.dumps(
            {
                "output_dir": manifest["output_dir"],
                "docling_version": manifest["docling_version"],
                "model_files": len(manifest["files"]),
                "model_families": manifest["allowed_model_families"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
