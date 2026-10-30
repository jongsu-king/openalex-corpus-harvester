# Installing and validating OCRmyPDF / Tesseract

The OCR integration was exercised on macOS ARM64 on 2026-10-29 with OCRmyPDF **17.13.0**, Tesseract **5.5.3**, and Ghostscript **10.08.0**, using the project's Python 3.11.16 virtual environment. The installed Tesseract language data were `eng` and `osd`; the adapter explicitly selects English (`eng`).

## Install the runtime

Run from the application directory (`openalex_corpus/`):

```bash
brew install tesseract ghostscript
uv sync --locked --python 3.11 --extra dev --extra docling --extra ocr
.venv/bin/ocrmypdf --version
tesseract --version
tesseract --list-langs
gs --version
```

This setup uses the locked Python OCRmyPDF package with Homebrew's system executables. It does not use Homebrew's complete OCRmyPDF package. The adapter disables preprocessing and uses `--optimize 0`, so its execution path does not require the `qpdf` CLI, `jbig2enc`, `pngquant`, or `unpaper`. Additional language packs were not installed, and recognition quality for Korean or other languages was outside this validation.

Invoking `.venv/bin/openalex-corpus` directly does not necessarily put the virtual environment's `bin` directory on `PATH`. The adapter first looks for `ocrmypdf` beside `sys.executable`, so it can find the project installation in that case. Tesseract and Ghostscript must be available through the system `PATH`.

## Enable OCR and understand its output

```yaml
parsing:
  enable_ocr: true
  ocr_timeout: 300
```

The pipeline selects pages that have visible content but little or no extractable text. It invokes OCRmyPDF with `--redo-ocr --pages ... --jobs 1 --language eng --output-type pdf --optimize 0`. In OCRmyPDF 17, `--redo-ocr` remains a supported alias for `--mode redo`. Pages with enough digital text are excluded.

The original PDF is left unchanged. OCR adds a text layer to a separate `ocr/ocr_derivative.pdf`. Before starting, the adapter validates the input PDF and requested page numbers. After execution, it checks that the derivative opens, that its page count matches the input, and that the source hash is unchanged; input and output SHA-256 hashes are recorded. Exit code 0 alone is insufficient.

Each selected page must yield enough extractable characters before it receives `ocr_used=true` and `source=ocr_extracted`. Otherwise, it remains `PARTIAL`. If the overall OCR timeout expires, the adapter terminates the child process group as well as the main process.

OCR text is kept distinct from the PDF's original digital text. The output carries `ocr_text_unverified` because symbols, names, and accents can be misrecognized. A `COMPLETE_WITH_WARNINGS` result means the processing checks passed with review items; it does not certify every recognized character.

## Reproduce the validation

```bash
OPENALEX_RUN_OCR=1 .venv/bin/pytest -q tests/test_live_ocr.py
.venv/bin/python scripts/validate_ocr.py \
  --source-pdf data/papers/W4402950921/original.pdf \
  --output-dir data/validation/ocr
```

The recorded opt-in test run reported **2 passed** in about 4.94 seconds: one checked selected-page recognition and one checked rejection of an invalid page range. The validation script separately processed the two inputs below. These local checks make no network requests or API charges. Ordinary test runs skip the opt-in tests unless `OPENALEX_RUN_OCR=1` is set.

The validation script exercised these inputs:

| Input | Characters per page before OCR | Characters per page after OCR | Checks |
|---|---|---|---|
| Synthetic two-page PDF: digital text on page 1, raster-only content on page 2 | 47, 0 | 47, 100 | Only page 2 selected; page 1 retained `ocr_used=false`; known text recovered; two pages preserved |
| Separate rasterized test input made from page 1 of real paper `W4402950921` | 0 | 2,241 | `Docling` recognized on the page; one page preserved; original paper SHA-256 unchanged |

The second input was made by rasterizing a page of an existing digital open-access paper for this test. It was not a newly acquired scanned paper. Both the original paper and the rasterized test input retained their pre-OCR hashes. Both validation cases finished as `COMPLETE_WITH_WARNINGS`, retaining OCR uncertainty and auxiliary-parser warnings.

Reprocessing a real `PARTIAL` paper that already has digital body text may result in OCR `not_required`. OCR does not resolve an unverified equation or disputed table merely by running successfully.

The original paper's SHA-256 was:

```text
82dd470712ce8389f19f20eb9330475e2166a281f8c7990a9f1d0763d73b4d22
```

The recorded evidence is in `data/validation/ocr/20261029T070634.265676Z/evidence.json`, with a convenience copy at `data/validation/ocr/latest_evidence.json`. Each run directory retains its test inputs, OCR PDFs, raw text, images, and logs. The derivative from the real-paper page was also rendered and inspected to check that its visible content had been preserved.

Upstream references: [OCRmyPDF installation](https://ocrmypdf.readthedocs.io/en/stable/installation.html), [OCR modes and text-layer handling](https://ocrmypdf.readthedocs.io/en/stable/advanced.html), and [Tesseract language data](https://tesseract-ocr.github.io/tessdoc/Data-Files-in-different-versions.html).
