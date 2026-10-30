#!/usr/bin/env python3
"""Render the module/storage map without a graphing dependency."""

from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "diagrams" / "pipeline-overview.svg"


def render() -> str:
    ink = "#d1d9e0"
    muted = "#91a4b7"
    blue = "#79b8ff"
    green = "#7ee7b4"
    border = "#344657"
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="1680" height="1120" '
        'viewBox="0 0 1680 1120" role="img" aria-labelledby="title desc">',
        '<title id="title">OpenAlex Corpus Harvester: module and storage map</title>',
        '<desc id="desc">Pipeline coordinates HTTPX clients and a spawned parser worker. '
        'Request receipts, budget reservations, immutable paper artifacts, and SQLite '
        'records support checkpoint resume and extraction recovery. Solid arrows show '
        'data exchange; dashed arrows show persistence.</desc>',
        '<defs><pattern id="grid" width="24" height="24" patternUnits="userSpaceOnUse">'
        '<path d="M 24 0 L 0 0 0 24" fill="none" stroke="#1b2936" stroke-width="0.5"/>'
        '</pattern><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
        f'<path d="M 0 0 L 10 5 L 0 10 z" fill="{blue}"/></marker></defs>',
        '<rect width="1680" height="1120" fill="#0d141c"/>',
        '<rect width="1680" height="1120" fill="url(#grid)"/>',
        '<g font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace">',
    ]

    def text(x, y, value, size=20, color=ink, weight=400):
        parts.append(
            f'<text xml:space="preserve" x="{x}" y="{y}" font-size="{size}" fill="{color}" '
            f'font-weight="{weight}">{escape(value)}</text>'
        )

    def box(x, y, width, height, title):
        parts.append(
            f'<rect x="{x}" y="{y}" width="{width}" height="{height}" '
            f'rx="3" fill="#101b26" stroke="{border}"/>'
        )
        parts.append(
            f'<path d="M{x} {y + 48} H{x + width}" stroke="{border}" stroke-width="1"/>'
        )
        text(x + 20, y + 31, title, 19, blue, 600)

    def line(x1, y1, x2, y2, dashed=False, arrow=True):
        dash = ' stroke-dasharray="6 5"' if dashed else ""
        marker = ' marker-end="url(#arrow)"' if arrow else ""
        parts.append(
            f'<path d="M{x1} {y1} L{x2} {y2}" fill="none" '
            f'stroke="{blue}" stroke-width="2"{dash}{marker}/>'
        )

    text(40, 58, "openalex_corpus / execution map", 32, ink, 600)
    text(40, 96, "HTTP clients  |  asyncio coordinator  |  spawn worker  |  durable state", 20, muted)

    box(40, 135, 1600, 105, "cli.py")
    text(62, 218, "$ openalex-corpus harvest ...  ->  SearchSpec.params()  ->  Pipeline.harvest(spec)", 23, green)
    line(820, 240, 820, 292)
    text(838, 276, "bounded batches", 16, muted)

    box(40, 300, 460, 400, "api/client.py + download/")
    text(62, 384, "OpenAlexClient.get_page()", 21, green)
    for y, value in [
        (420, "  reserve_metadata(cost)"),
        (453, "  _throttle()"),
        (486, "  GET /works?cursor=..."),
        (519, "  observe_headers(...)"),
    ]:
        text(62, y, value, 19)
    line(62, 546, 478, 546, arrow=False)
    text(62, 585, "PDFDownloader.download()", 21, green)
    text(62, 624, "  stream -> validate -> hash", 19)
    text(62, 660, "  .part -> pdf_versions/<sha>", 19)

    box(540, 300, 560, 400, "pipeline.py :: Pipeline")
    text(565, 384, "ingest(raw)", 22, green)
    text(565, 413, "  raw payload + normalized metadata", 18, muted)
    text(565, 461, "process_work(raw)", 22, green)
    text(565, 490, "  download -> parse(work_id, pdf)", 18, muted)
    text(565, 538, "check_extraction(result)", 22, green)
    text(565, 567, "  page coverage / files / warnings", 18, muted)
    text(565, 615, "commit extraction run", 22, green)
    text(565, 644, "  rename -> record_extraction()", 18, muted)
    text(565, 675, "  _publish() -> current.json", 18, muted)

    box(1140, 300, 500, 400, "spawn :: _parser_worker()")
    text(1162, 384, "parse_pdf(pdf, out, options)", 21, green)
    parser_rows = [
        "1  PyMuPDF     raw pages / geometry",
        "2  OCRmyPDF*   selected-page text",
        "3  pdfplumber  table / text checks",
        "4  Docling     layout / structure",
        "5  GROBID*     references / TEI",
    ]
    for i, row in enumerate(parser_rows):
        text(1162, 425 + i * 35, row, 18)
    line(1162, 591, 1618, 591, arrow=False)
    text(1162, 628, "workers=1   timeout=600s", 19, muted)
    text(1162, 665, "worker RSS budget: 3072 MiB", 19, muted)

    line(502, 385, 537, 385)
    text(506, 369, "JSON", 12, muted)
    line(538, 593, 502, 593)
    text(506, 577, "call", 12, muted)
    line(1102, 473, 1137, 473)
    text(1107, 456, "PDF", 12, muted)
    line(1138, 550, 1102, 550)
    text(1104, 534, "dict", 12, muted)

    for x, label in [(270, "receipts / reservations"), (820, "transactions / selection"), (1390, "parser output to staging")]:
        line(x, 702, x, 798, dashed=True)
        text(x + 15, 753, label, 15, muted)

    box(40, 800, 460, 220, "disk :: request state")
    text(62, 886, "crawls/<run>/pages/*.json", 19)
    text(62, 921, "*.receipt.json", 19)
    text(62, 956, "state/network_budget.json", 19)
    text(62, 994, "checkpoint cursor + offset: SQLite", 16, muted)

    box(540, 800, 560, 220, "SQLite + publication pointer")
    text(565, 886, "works / metadata_revisions", 19)
    text(565, 921, "crawl_checkpoints / retry_queue", 19)
    text(565, 956, "extraction_runs / validation_results", 19)
    text(565, 994, "current.json -> extractions/<run>", 18, green)

    box(1140, 800, 500, 220, "disk :: paper artifacts")
    text(1162, 886, "metadata_history/<sha>.json", 19)
    text(1162, 921, "original.pdf / pdf_versions/", 19)
    text(1162, 956, "extractions/<run>/manifest.json", 19)
    text(1162, 994, "paper.md / tables / figures / ...", 18, muted)

    text(40, 1061, "solid: calls / returned data     dashed: persistence     * configured only", 18, muted)
    text(40, 1094, "Workers write staging output; pipeline.py commits runs. GROBID runs as a separate local service.", 17, muted)
    parts.extend(["</g>", "</svg>"])
    return "\n".join(parts) + "\n"


if __name__ == "__main__":
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(render(), encoding="utf-8")
    print(OUTPUT.relative_to(ROOT))
