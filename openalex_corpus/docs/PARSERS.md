# PDF parsers, outputs, and validation

The parser keeps the original PDF and the evidence produced by each extraction engine. Markdown is a reading copy: it cannot express every layout detail, and a plausible-looking page is not enough to establish that a table or equation was recovered correctly. Where extraction is uncertain, the output retains the source location, competing candidates, and the reason for the uncertainty.

The checked-in YAML enables local GROBID and selective OCR alongside Docling, PyMuPDF, and pdfplumber. The library defaults used without a configuration file leave GROBID and OCR disabled. GROBID 0.9.1 and OCRmyPDF 17.13.0 / Tesseract 5.5.3 / Ghostscript 10.08.0 were exercised in the integration work recorded on 2026-10-29. See [enhanced validation](ENHANCED_VALIDATION.md), [GROBID setup](GROBID_SETUP.md), and [OCR setup](OCR_SETUP.md) for the results and reproduction commands.

## What each parser contributes

| Component | Role | Retained output | Limits |
|---|---|---|---|
| Docling standard PDF pipeline | Primary candidates for document structure, headings, reading order, tables, and figure regions | `docling_raw.json`, `docling_items.json`, `docling_body.md`, model status, table and figure regions | Layout classification can be wrong. The pipeline compares each page with PyMuPDF and uses the source-text candidate for that page when Docling appears to omit text. |
| PyMuPDF | Text blocks, spans, coordinates, page sizes, image objects, equation candidates, and page renders | `pymupdf_raw.json`, `text_blocks`, `page_images/`, `figures/`, `image_resources/`, `equations/` | It cannot repair a defective text-layer encoding. Two-column order and headings inferred from font size are heuristics. |
| pdfplumber | An independent text extraction and line-based table extraction | `pdfplumber_raw.json`, table CSV/JSON/HTML/Markdown, table images | It can miss borderless or complex tables. Its tables are also compared with PyMuPDF candidates. |
| GROBID or a supplied TEI file | Additional candidates for scholarly structure and references | Original TEI XML, `tei_document.json`, structured references | Live extraction requires a separate service. TEI supplements the PDF evidence; it does not supersede it. |
| OCRmyPDF / Tesseract | OCR for pages with visible content but little or no extractable text | A separate OCR PDF, sidecar text, logs, `ocr_extraction.json` | The result is labeled `ocr_extracted` and remains unverified. OCR runs only when enabled and installed. |

The Docling adapter explicitly disables its OCR, picture descriptions, picture classification, code/formula generation, chart-value extraction, external services, and external plugins. This project does not use a VLM or LLM to generate prose, captions, equations, or measurements. Docling's layout and table models supply structural candidates.

## Tested runtime and model configuration

The environment tested on 2026-10-29 used Python 3.11.16, Docling 2.144.0, PyMuPDF 1.28.2, pdfplumber 0.11.10, Pillow 12.3.0, and lxml 6.1.3. The lock file records the full dependency set.

The tested Docling configuration was:

- Standard PDF pipeline on CPU, with two threads.
- Layout model `docling-project/docling-layout-heron`, using the transformers engine.
- TableFormer in `accurate` mode, with `do_cell_matching=true`.
- Layout, table, and OCR batch sizes of 1; queue size of 2. Docling OCR itself remains disabled.
- Page and picture image generation, with a default render resolution of 110 DPI.
- A 300-second Docling document timeout and a 600-second timeout for the complete worker process.
- One worker. The checked-in YAML assigns 3072 MB per worker; the initial validation and the library default use 2048 MB.

During validation, the layout model's `main` revision resolved to `8f39ad3c0b4c58e9c2d2c84a38465abf757272d8`, and `docling-project/docling-models` at `v2.3.0` resolved to `fc0f2d45e2218ea24bce5045f58a389aed16dc23`. A remote `main` branch can move. For reproducible runs, keep the prepared model directory and the file hashes in `model_manifest.json` together.

## Prepare models and run offline

Run these commands from the application directory (`openalex_corpus/`):

```bash
uv sync --extra docling --extra dev
.venv/bin/python scripts/prepare_models.py --output-dir data/models/docling
```

Model preparation is an explicit network operation when the required files are missing. The script reuses existing files where possible. Otherwise, it calls the installed Docling version's `download_models` function, first disabling every `with_*` option and then enabling only `layout` and `tableformer`. It does not select OCR, VLM, picture-description, code/formula, or ASR models. Docling's layout downloader may also fetch other engine formats within the same layout-model family. After preparation, the script records the actual files and their SHA-256 hashes in `data/models/docling/model_manifest.json`.

This example configures offline Docling extraction with OCR disabled. The checked-in default YAML enables selective OCR; that setting is independent of Docling's own OCR, which the adapter always disables.

```yaml
parsing:
  primary_engine: docling
  enable_docling: true
  docling_artifacts_path: data/models/docling
  allow_model_downloads: false
  enable_ocr: false
  docling_threads: 2
  docling_timeout: 300
  render_all_pages: true
```

Set `docling_artifacts_path` to the **common parent** of the two model directories:

```text
data/models/docling/
├── docling-project--docling-layout-heron/
│   ├── config.json
│   ├── model.safetensors
│   └── preprocessor_config.json
├── docling-project--docling-models/
│   └── model_artifacts/tableformer/accurate/
│       ├── tm_config.json
│       └── tableformer_accurate.safetensors
└── model_manifest.json
```

In the validation environment, these model directories are symlinks to existing Hugging Face snapshots. When moving the project to another machine, include the link targets or run the preparation script at the new location. Hugging Face normally caches files under `~/.cache/huggingface/hub`; Docling's downloader normally uses `~/.cache/docling/models`. Environment variables and library settings can change those defaults. This project reads from the path configured in YAML.

With `allow_model_downloads=false`, the adapter also sets `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1`. An absent model path or incomplete installation produces `docling_models_not_configured` or `docling_failed`. Extraction then continues with PyMuPDF and pdfplumber, with a structural-validation warning. Collection and basic extraction therefore remain usable without Docling models.

To exercise the real standard pipeline against the prepared models without allowing downloads:

```bash
OPENALEX_RUN_DOCLING=1 \
DOCLING_ARTIFACTS_PATH="$PWD/data/models/docling" \
OPENALEX_ALLOW_MODEL_DOWNLOADS=0 \
.venv/bin/python -m pytest tests/test_parsers.py::test_actual_docling_standard_pipeline -q
```

## How source information is preserved

### Pages, reading order, and paragraphs

Page numbers start at 1. If a page fails to load, its number still has a failure record. A page is `blank` only when it contains no source text, images, or drawing objects. A page with visible content and little or no text is `ocr_required`.

The PyMuPDF fallback orders two-column text by using full-width blocks as boundaries, then sorting the left and right columns within those boundaries. This can misorder three-column layouts, floating captions, vertical text, or tables spanning several columns, so it records `reading_order_requires_review`. Possible headers and footers are marked and retained. End-of-line hyphens are preserved rather than removed by guessing the intended word, and the output does not invent section headings. HTML comments in Markdown identify the page and extraction source.

The main element `bbox` values use PDF points with the origin at the top left of the page. Raw Docling structures and raw table-cell bounding boxes retain the library's `coord_origin`; read that field before interpreting those coordinates.

### Tables

Tables are saved as CSV, JSON, and HTML/Markdown. If the first row has not been identified as a header, the fallback Markdown file may contain an HTML `<table>` so that the renderer does not implicitly promote it to one. JSON retains merged-cell row/column spans, empty values, and `null`. An empty string and `null` are not interchangeable.

The cross-check status `cell_matrix_agreement` means that cell values, positions, and duplicate counts agree after whitespace normalization. Matching word sets or character counts alone do not qualify. The status does not validate captions, units, or the meaning of headers.

When parsers disagree, their candidates are retained and a warning is recorded. Overlapping candidates on the same page share a `candidate_group_id`; their cell values are not merged. The group count is a coordinate-based estimate of distinct tables. `table_candidate_count` gives the raw number of candidates.

### Figures and images

`kind=image_object` identifies a raster image displayed in the PDF. `kind=figure_region` identifies a region that Docling classified as a paper figure. The same content can appear in both, so adding these counts can overstate the number of numbered figures in the paper. Images found among PDF resources without a confirmed displayed occurrence are retained separately in `image_resources`.

Where possible, extraction preserves original image bytes and resolution. If the bytes of an inline image cannot be extracted directly, the pipeline renders its region and records that method. Full-page renders also retain vector artwork, although the pipeline may not segment every vector figure into a separate region. It does not generate missing captions or infer measurements from charts.

### Equations

Regions containing mathematical symbols or equation fonts are saved as equation candidates. These heuristics may miss equations or include surrounding prose. If an exact LaTeX structure has not been verified, the result retains `latex=null`, `status=equation_parse_failed`, and an image of the source region. The document remains `PARTIAL`.

A deliberately narrow subset of linear expressions can also receive a `latex_candidate`. This uses a fixed mapping of observed Unicode Greek characters and linear operators, and records both the source string and each substitution. For example, the character `ν` can map to `\nu{}`; the English letters `nu` are not interpreted as Greek. The conversion does not reconstruct multiline notation, superscripts, subscripts, fraction layouts, integrals, or matrices. A candidate remains `unverified` and is not promoted to a verified equation in the body text.

### References and TEI

For a text-based References or Bibliography section, the parser first preserves each reference number and source string. A DOI found by a regular expression is a `doi_candidate`; it is not treated as an established structured DOI or linked automatically. Unnumbered references can be difficult to segment reliably.

GROBID requests send `consolidateHeader`, `consolidateCitations`, and `consolidateFunders` as `0`, preventing automatic enrichment from external metadata. Both the structured TEI fields and the original XML are retained. The XML reader rejects DTDs and entity declarations.

`link_exact_references` can link a reference only when a structurally supplied DOI matches exactly one Work ID in the local identifier index. It does not accept title similarity as a match and does not perform an external OpenAlex reference search.

```yaml
parsing:
  grobid_url: http://127.0.0.1:8070
  grobid_timeout: 120
  # Alternatively, supply a local TEI file for this PDF:
  # tei_path: /absolute/path/to/paper.tei.xml
```

The example uses the library's 120-second GROBID timeout; `config/config.yaml` and `config/opv.yaml` allow 180 seconds. A `tei_path` must point to TEI for the PDF being processed. Because a global setting applies the same file to every document, use it for a single-document reparse rather than a bulk collection. The adapter does not automatically download paid TEI content from OpenAlex.

### OCR

OCR requires `enable_ocr=true`, an available `ocrmypdf` executable, and the separately installed Tesseract/Ghostscript system dependencies. Only pages with visible content and little or no text are passed through `--pages`. With `--redo-ocr`, OCRmyPDF masks visible digital text while recognizing additional image text. The original PDF is unchanged, and text or table evidence derived from the OCR copy is labeled `ocr_extracted`.

OCR is limited to one job. On timeout, the adapter terminates the child process group as well as the main process. A scanned page remains `PARTIAL` if OCR is unavailable, fails, or does not recover enough text. [OCR setup](OCR_SETUP.md) describes the integrity checks and the tested inputs.

## Interpreting validation status

| Status | Meaning |
|---|---|
| `COMPLETE` | The defined automated checks passed. This is not a claim of exact semantic or mathematical equivalence to the PDF. |
| `COMPLETE_WITH_WARNINGS` | All pages were processed, but items such as reading order, parser disagreements, or undetected references need review. |
| `PARTIAL` | Known gaps remain: a missing page or region, unfinished OCR, an unverified equation, or a failed table/figure extraction. |
| `FAILED` | The document could not be opened or the processing run failed. |

Differences in word or character counts are diagnostic signals, not proof of missing content. The retained blocks, coordinates, page images, TEI, and parser candidates make those differences inspectable. Automated checks cannot catch every defect already present in the PDF's text layer or every element missed by all detectors. Use `quality_report.json` to decide which papers need a reparse or manual review.

## Validation record

Offline fixtures cover page coverage, blank pages, two-column ordering, merged and empty table cells, equations, scanned PDFs, absent references, image-file references, page failures, Docling page/text omissions, damaged and encrypted PDFs, TEI structure, external-entity rejection, strict cell-matrix comparisons, and the rule against guessing equation structure.

A synthetic PDF containing text, a table, a figure, and an equation was also processed with the real Docling 2.144.0 models. The initial run, including model acquisition, took about 64 seconds and reached roughly 1.56 GB peak RSS. It was correctly left `PARTIAL` because the source equation was not verified. Both the cached-model test and the offline test with an explicit model path passed.

Later integration runs exercised GROBID on a real paper and OCRmyPDF on mixed digital/raster inputs. Adding GROBID references did not make the existing `PARTIAL` paper complete: equation and table review remained outstanding. These runs establish that the integrations execute and retain the expected evidence; they do not establish accuracy for untested document types.

Upstream references: [Docling pipeline options](https://docling-project.github.io/docling/reference/pipeline_options/), [PyMuPDF Page API](https://pymupdf.readthedocs.io/en/latest/page.html), [pdfplumber](https://github.com/jsvine/pdfplumber), [GROBID services](https://grobid.readthedocs.io/en/latest/Grobid-service/), and [OCRmyPDF redo OCR](https://ocrmypdf.readthedocs.io/en/stable/advanced.html).
