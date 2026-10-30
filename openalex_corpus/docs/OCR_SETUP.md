# OCRmyPDF / Tesseract 설치와 실제 검증

2026-10-29 macOS arm64에서 OCRmyPDF **17.13.0**, Tesseract **5.5.3**, Ghostscript **10.08.0**을 설치하고 실제 OCR을 실행했습니다. Python은 프로젝트의 3.11.16 가상환경을 사용합니다. Tesseract 언어는 `eng`, `osd`가 설치되어 있고, 이 어댑터는 영어 `eng`를 명시적으로 선택합니다.

## 재현 가능한 설치

프로젝트 루트에서 다음과 같이 설치합니다.

```bash
brew install tesseract ghostscript
uv sync --locked --python 3.11 --extra dev --extra docling --extra ocr
.venv/bin/ocrmypdf --version
tesseract --version
tesseract --list-langs
gs --version
```

Homebrew의 OCRmyPDF 전체 패키지를 사용하는 대신 프로젝트 잠금에 포함된 Python 패키지와 Homebrew 시스템 실행 파일을 조합했습니다. `qpdf` CLI, `jbig2enc`, `pngquant`, `unpaper`는 현재 어댑터의 `--optimize 0`/전처리 비활성 경로에 필수로 사용하지 않습니다. 모든 언어 팩을 설치하지 않았습니다. 한국어 등 다른 언어의 인식 품질은 이번 검증 범위가 아닙니다.

원래 CLI를 직접 `.venv/bin/openalex-corpus`로 실행하면 가상환경의 `bin`이 `PATH`에 포함되지 않을 수 있습니다. 어댑터는 `sys.executable`과 같은 디렉터리의 `ocrmypdf`를 먼저 찾아 이 경우에도 동작합니다. Tesseract와 Ghostscript는 설치한 시스템 `PATH`에서 찾습니다.

## 설정과 동작

```yaml
parsing:
  enable_ocr: true
  ocr_timeout: 300
```

텍스트가 없거나 희박하고 시각 내용이 있는 페이지에만 OCR을 수행합니다. OCRmyPDF의 `--redo-ocr --pages ... --jobs 1 --language eng --output-type pdf --optimize 0`을 사용합니다. OCRmyPDF 17의 `--redo-ocr`는 공식적으로 유지되는 `--mode redo`의 별칭입니다. 원본은 수정하지 않으며 별도의 `ocr/ocr_derivative.pdf`에 텍스트 레이어를 추가합니다. 디지털 텍스트가 충분한 페이지는 OCR 대상이 아닙니다.

OCR 실행 전에 입력 PDF와 페이지 범위를 검사합니다. 종료 코드 0만으로 성공으로 간주하지 않고 출력 PDF 열기·페이지 수·입출력 SHA-256·원본 불변 여부를 검사합니다. 페이지별로 실제 추출 문자 수가 충분해야 `ocr_used=true`/`source=ocr_extracted`를 부여하며, 부족하면 `PARTIAL`을 유지합니다. 전체 OCR 제한 시간이 지나면 자식 프로세스 그룹까지 종료합니다.

OCR 문자는 디지털 원문과 분리합니다. OCR은 기호·이름·악센트 등을 잘못 인식할 수 있으므로 `ocr_text_unverified`를 남깁니다. `COMPLETE_WITH_WARNINGS`는 OCR 텍스트의 완전한 정확성 인증이 아닙니다.

## 실제 실행 결과

```bash
OPENALEX_RUN_OCR=1 .venv/bin/pytest -q tests/test_live_ocr.py
.venv/bin/python scripts/validate_ocr.py \
  --source-pdf data/papers/W4402950921/original.pdf \
  --output-dir data/validation/ocr
```

실제 opt-in 테스트 **2개 통과**, 실행 시간 약 4.94초였습니다. 로컬 실행이므로 네트워크 요청이나 API 비용이 없습니다. 기본 테스트 실행에서는 실제 OCR 테스트를 건너뛰며 위 환경변수로 명시적으로 활성화합니다.

| 실제 검증 입력 | OCR 전 페이지별 문자 수 | OCR 후 문자 수 | 확인한 사항 |
|---|---|---|---|
| 합성 2페이지: 1번 디지털, 2번 raster-only | 47, 0 | 47, 100 | 2번만 OCR, 1번은 `ocr_used=false`, 알려진 문구 복원, 2페이지 유지 |
| 실제 논문 `W4402950921`의 1페이지를 별도로 rasterize한 시험 입력 | 0 | 2,241 | 실제 페이지의 `Docling` 문구 인식, 1페이지 유지, 원본 논문 SHA 불변 |

두 번째 입력은 **기존 디지털 OA 논문을 검증 목적으로 rasterize한 파생 입력**입니다. 새로 입수한 실제 스캔 논문이라고 주장하지 않습니다. 출처의 원본 PDF와 raster 시험 입력 모두 OCR 실행 전후 해시가 같습니다. 두 시험 결과는 OCR 불확실성과 보조 파서 경고를 남긴 `COMPLETE_WITH_WARNINGS`입니다. 디지털 본문이 이미 있는 실제 `PARTIAL` 논문을 재처리하면 OCR이 `not_required`가 될 수 있으며, 수식/표 불확실성을 OCR만으로 해결했다고 처리하지 않습니다.

원본 논문 해시: `82dd470712ce8389f19f20eb9330475e2166a281f8c7990a9f1d0763d73b4d22`.

검증 증거는 `data/validation/ocr/20261029T070634.265676Z/evidence.json`과 `data/validation/ocr/latest_evidence.json`에 있습니다. 시험 입력·OCR PDF·원시 텍스트·이미지·로그는 각 실행 디렉터리에 함께 보존했습니다. 실제 페이지의 OCR 파생 PDF를 렌더링해 원래 시각 내용이 보존되는지도 확인했습니다.

참고: [OCRmyPDF 설치 문서](https://ocrmypdf.readthedocs.io/en/stable/installation.html), [OCR 모드와 텍스트 레이어 처리](https://ocrmypdf.readthedocs.io/en/stable/advanced.html), [Tesseract 데이터 파일](https://tesseract-ocr.github.io/tessdoc/Data-Files-in-different-versions.html).
