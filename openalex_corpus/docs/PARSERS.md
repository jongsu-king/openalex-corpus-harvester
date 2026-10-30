# PDF 파서의 동작과 검증 범위

**후속 업데이트:** GROBID 0.9.1 및 OCRmyPDF 17.13.0/Tesseract 5.5.3/Ghostscript 10.08.0의 실제 연동 검증을 완료했습니다. 현재 기본 YAML은 로컬 GROBID와 선택적 OCR을 사용합니다. 최신 실행 근거는 [후속 검증](ENHANCED_VALIDATION.md), [GROBID](GROBID_SETUP.md), [OCR](OCR_SETUP.md)에 있습니다.

이 프로그램은 원본 PDF를 보존하고 여러 추출 결과를 함께 저장합니다. Markdown은 사람이 읽기 위한 표현이며, PDF의 모든 의미·레이아웃을 정확하게 복원했다는 보증이 아닙니다. 구조나 수식이 불확실하면 원본 위치, 후보 결과, 실패 사유를 남깁니다.

## 파서별 역할

| 구성 요소 | 실제 역할 | 저장되는 근거 | 한계와 상태 |
|---|---|---|---|
| Docling standard PDF pipeline | 문서 구조·제목·읽기 순서·표·그림 영역의 기본 후보 | `docling_raw.json`, `docling_items.json`, `docling_body.md`, 모델 상태, 표와 그림 영역 | 학습된 레이아웃 분류도 틀릴 수 있습니다. 페이지별 PyMuPDF 결과와 비교하며 원문 누락이 의심되면 해당 페이지의 원문 텍스트 후보를 사용합니다. |
| PyMuPDF | 모든 페이지의 텍스트 블록·span·좌표·페이지 크기·이미지 오브젝트·수식 후보·페이지 이미지 | `pymupdf_raw.json`, `text_blocks`, `page_images/`, `figures/`, `image_resources/`, `equations/` | 텍스트 레이어의 인코딩 오류를 자체 해결할 수 없습니다. 두 단 읽기 순서와 큰 글꼴 제목은 검증이 필요한 휴리스틱입니다. |
| pdfplumber | 독립적인 페이지 텍스트와 선 기반 표 추출 | `pdfplumber_raw.json`, 표 CSV/JSON/HTML/Markdown, 표 이미지 | 선이 없거나 복잡한 표를 놓칠 수 있습니다. PyMuPDF의 표 탐지와도 비교합니다. |
| GROBID / 제공된 TEI | 학술 구조와 참고문헌의 추가 후보 | 원본 TEI XML, `tei_document.json`, 구조화 참고문헌 | 별도 GROBID 서비스가 필요합니다. PDF 대신 TEI만 신뢰하지 않습니다. 현재 기본 YAML에서 로컬 서비스를 사용하며, 설정 없는 라이브러리 기본값은 비활성입니다. |
| OCRmyPDF / Tesseract | 텍스트 레이어가 없거나 희박한 시각 페이지의 OCR | 원본과 별도의 OCR PDF, sidecar, 로그, `ocr_extraction.json` | 현재 기본 YAML에서 필요한 페이지에 사용하며, 설정 없는 라이브러리 기본값은 비활성입니다. OCR 결과는 `ocr_extracted`로 구분하고 정확성을 보증하지 않습니다. |

Docling에는 OCR·그림 설명·그림 분류·코드/수식 생성·차트 값 추출·외부 서비스·외부 플러그인을 모두 명시적으로 끕니다. 이 프로젝트는 VLM/LLM으로 문장, 캡션, 수식 또는 측정값을 생성하지 않습니다. Docling의 기본 표/레이아웃 모델은 구조 후보 탐지에 사용합니다.

## 설치한 버전과 기본 모델

2026-10-29 실제 설치·검증 조합은 Python 3.11.16, Docling 2.144.0, PyMuPDF 1.28.2, pdfplumber 0.11.10, Pillow 12.3.0, lxml 6.1.3입니다. 최종 의존성 목록은 프로젝트의 lock 파일도 확인하십시오.

검증한 Docling 기본 설정:

- standard PDF pipeline, CPU, 스레드 2개
- layout: `docling-project/docling-layout-heron`, transformers 엔진
- table: TableFormer `accurate`, `do_cell_matching=true`
- 레이아웃/표/OCR 배치 크기 1, 대기열 크기 2
- 페이지·그림 이미지 생성, 기본 렌더링 해상도 110 DPI
- Docling 문서 제한 300초, 전체 작업 프로세스 제한 600초
- 기본 프로세스 1개, 현재 YAML의 프로세스별 메모리 예산 3072 MB (초기 검증 당시 2048 MB)

검증 당시 Hugging Face의 layout `main`은 `8f39ad3c0b4c58e9c2d2c84a38465abf757272d8`, `docling-project/docling-models`의 `v2.3.0`은 `fc0f2d45e2218ea24bce5045f58a389aed16dc23`으로 해석되었습니다. 온라인의 `main`은 이후 변경될 수 있습니다. 장기 재현에는 준비된 모델 디렉터리와 `model_manifest.json`의 파일 SHA-256을 함께 보존하십시오.

## 모델 준비와 오프라인 실행

프로젝트 루트에서 실행합니다.

```bash
uv sync --extra docling --extra dev
.venv/bin/python scripts/prepare_models.py --output-dir data/models/docling
```

모델 준비 명령은 명시적인 네트워크 작업입니다. 필요한 모델이 이미 있으면 재사용합니다. 없으면 설치된 Docling의 `download_models` 함수를 사용하되 모든 `with_*` 옵션을 먼저 끄고 필요한 `layout`과 `tableformer`만 켭니다. OCR, VLM, 그림 설명, 코드/수식, ASR 모델은 선택하지 않습니다. 현재 Docling의 layout 다운로드 함수는 같은 layout 계열의 다른 엔진 형식도 미리 받을 수 있습니다. 다운로드 후 실제 파일 목록과 SHA-256을 `data/models/docling/model_manifest.json`에 저장합니다.

설정은 다음과 같습니다.

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

`docling_artifacts_path`는 두 모델 디렉터리의 **공통 부모**를 가리킵니다.

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

검증 환경의 `data/models/docling` 아래 모델 디렉터리는 기존 Hugging Face 스냅샷을 가리키는 심볼릭 링크입니다. 이 디렉터리를 다른 장치로 옮길 때는 링크 대상도 포함하거나 새 경로에서 준비 스크립트를 실행해야 합니다. Hugging Face 기본 캐시는 `~/.cache/huggingface/hub`, Docling 도구의 기본 다운로드 경로는 `~/.cache/docling/models`입니다. 현재 프로젝트가 사용하는 경로는 위 YAML의 경로입니다. 환경변수나 라이브러리 설정으로 기본 캐시는 달라질 수 있습니다.

`allow_model_downloads=false`일 때 어댑터는 `HF_HUB_OFFLINE=1`과 `TRANSFORMERS_OFFLINE=1`도 설정합니다. 모델 경로가 없거나 설치가 불완전하면 `docling_models_not_configured` 또는 `docling_failed`를 기록하고 PyMuPDF/pdfplumber 추출로 진행합니다. 이 경우 결과는 구조 검증 경고를 포함합니다. 모델이 없어도 기본 수집과 추출 모듈은 독립적으로 동작합니다.

실제 오프라인 기본 엔진 테스트:

```bash
OPENALEX_RUN_DOCLING=1 \
DOCLING_ARTIFACTS_PATH="$PWD/data/models/docling" \
OPENALEX_ALLOW_MODEL_DOWNLOADS=0 \
.venv/bin/python -m pytest tests/test_parsers.py::test_actual_docling_standard_pipeline -q
```

## 정보 보존 정책

### 페이지, 읽기 순서, 문단

페이지는 1부터 번호를 매기며, 페이지 로드가 실패해도 해당 번호의 실패 레코드를 남깁니다. 원문 텍스트·이미지·그리기 오브젝트가 모두 없는 페이지는 `blank`로 기록합니다. 시각 내용은 있는데 텍스트가 없거나 희박하면 `ocr_required`입니다. 빈 페이지로 간주하지 않습니다.

PyMuPDF fallback의 두 단 정렬은 전체 너비를 가로지르는 블록을 경계로 좌·우 열을 정렬하는 휴리스틱입니다. 복잡한 세 단 구성, 떠 있는 캡션, 세로쓰기, 여러 단을 가로지르는 표의 완벽한 읽기 순서를 보증하지 않습니다. `reading_order_requires_review`를 기록합니다. 헤더·푸터 후보는 삭제하지 않고 표시하며, 단어 끝 하이픈도 의미를 추정하여 제거하지 않습니다. 원문에 없는 절 제목은 추가하지 않습니다. Markdown의 HTML 주석은 페이지와 추출 출처를 표시하는 처리 기록입니다.

주요 요소의 `bbox`는 페이지 좌상단을 원점으로 하는 PDF point 좌표입니다. Docling의 원시 구조와 표 셀의 원시 bbox에는 라이브러리의 `coord_origin`이 남아 있으므로 해당 좌표계를 함께 읽어야 합니다.

### 표

CSV와 HTML/Markdown 및 JSON을 함께 저장합니다. 헤더임을 확인하지 못한 첫 행을 임의로 헤더로 승격하지 않기 위해, fallback Markdown 파일은 HTML `<table>`을 포함할 수 있습니다. 병합 셀의 row/column span과 빈 값·`null`을 JSON에 보존합니다. `null`과 빈 문자열을 같은 의미로 바꾸지 않습니다.

교차 검증의 `cell_matrix_agreement`는 공백만 정규화한 셀 행렬의 값·위치·중복 개수가 일치했다는 뜻입니다. 단어 집합이나 문자 개수가 같다는 이유만으로 이 상태를 부여하지 않습니다. 캡션, 단위, 헤더 의미까지 검증되었다는 뜻은 아닙니다. 다른 파서의 결과가 불일치하면 모두 남기고 경고를 기록합니다. 같은 페이지에서 겹치는 표 후보는 `candidate_group_id`로 묶지만 원본 후보 값을 합치지 않습니다. 묶음 수는 좌표 기반 추정치이며, 원시 후보 수는 `table_candidate_count`입니다.

### 그림과 이미지

`kind=image_object`는 PDF에 표시된 래스터 이미지 오브젝트이고, `kind=figure_region`은 Docling이 탐지한 논문 그림 영역입니다. 같은 내용이 양쪽에 나타날 수 있습니다. 이 구분을 무시하고 그림 수를 합치면 실제 논문 Figure 개수보다 커질 수 있습니다. 표시 여부가 확인되지 않은 PDF 리소스 이미지도 `image_resources`에 별도로 보존합니다.

원본 이미지 바이트와 해상도를 가능한 경우 저장합니다. 인라인 이미지의 바이트를 직접 추출하지 못하면 영역 렌더링으로 보존하고 그 방법을 기록합니다. 벡터 그림은 전체 페이지 이미지에도 남습니다. 모든 벡터 그림을 논문 Figure 단위로 분할했다는 보증은 없습니다. 캡션을 모델로 만들어 넣거나 그래프의 측정값을 생성하지 않습니다.

### 수식

수학 기호나 수식용 글꼴을 탐지한 영역을 후보로 저장합니다. 탐지 휴리스틱이므로 모든 수식을 찾거나 본문과 수식을 완벽히 분리하지는 못합니다. 정확한 LaTeX 구조가 검증되지 않은 경우 `latex=null`, `status=equation_parse_failed`와 원문 영역 이미지를 보존하고 전체 결과를 `PARTIAL`로 둡니다.

아주 좁은 선형 표현에 대해서만 `latex_candidate`를 추가합니다. 관찰된 Unicode 그리스 문자·선형 연산자의 고정 매핑이며, 원문 문자열과 치환 목록이 함께 저장됩니다. 예를 들어 실제 문자 `ν`는 `\nu{}`로 표기할 수 있지만 영문 문자열 `nu`를 그리스 문자라고 추정하지 않습니다. 여러 줄, 지수·첨자, 분수의 배치, 적분, 행렬 구조를 복구하거나 추측하지 않습니다. 후보가 있어도 검증 상태는 `unverified`이고 본문의 검증된 수식으로 채택하지 않습니다.

### 참고문헌과 TEI

텍스트의 References/Bibliography 구역에서는 번호와 원문 문자열을 우선 보존합니다. 정규식으로 발견한 DOI는 `doi_candidate`이며, 구조화된 DOI로 확정하거나 자동 연결하지 않습니다. 번호가 없는 참고문헌 구획을 정확하게 나누지 못할 수 있습니다.

GROBID 요청은 `consolidateHeader`, `consolidateCitations`, `consolidateFunders`를 모두 `0`으로 보내 외부 메타데이터의 자동 보완을 막습니다. TEI의 구조화 필드와 원문 XML을 모두 남깁니다. XML DTD와 entity 선언은 거절합니다. 실제 DOI가 구조적으로 제공되고 로컬 식별자 목록에서 하나의 Work ID와 정확히 일치할 때만 `link_exact_references`가 연결할 수 있습니다. 제목 유사도로 확정하지 않습니다. 이 helper는 외부 OpenAlex 참조 검색을 자동 수행하지 않습니다.

```yaml
parsing:
  grobid_url: http://127.0.0.1:8070
  grobid_timeout: 120
  # 또는 이미 확보한 로컬 TEI 파일 하나:
  # tei_path: /absolute/path/to/paper.tei.xml
```

`tei_path`는 해당 PDF와 일치하는 파일을 지정해야 합니다. 전역 설정으로 지정하면 모든 문서에 적용되므로 대량 수집 설정에는 넣지 말고 해당 문서를 재파싱하는 실행에서 사용하십시오. OpenAlex 유료 TEI 다운로드는 이 어댑터가 자동 수행하지 않습니다.

### OCR

`enable_ocr=true`이고 `ocrmypdf` 실행 파일이 설치된 경우에만 동작합니다. OCRmyPDF/Tesseract의 시스템 의존성은 별도로 설치해야 합니다. 텍스트가 없거나 희박한 시각 페이지만 `--pages`로 선택하고, `--redo-ocr`로 기존의 보이는 디지털 텍스트를 가린 상태에서 추가 이미지 텍스트를 읽습니다. 원본은 수정하지 않습니다. OCR 결과의 표·문자 근거는 `ocr_extracted`로 구분합니다.

OCR 프로세스는 작업 1개로 제한하고, 시간 제한이 지나면 하위 프로세스 그룹도 종료합니다. OCR이 없거나 실패한 스캔 페이지는 `PARTIAL` 상태를 유지합니다.

## 검증 상태를 읽는 방법

- `COMPLETE`: 정의된 자동 검사가 통과했다는 뜻이며, 내용이 수학적으로 100% 동일하다는 뜻은 아닙니다.
- `COMPLETE_WITH_WARNINGS`: 페이지 처리는 끝났지만 구조 순서, 파서 불일치, 탐지하지 못한 참고문헌 등 검토할 항목이 있습니다.
- `PARTIAL`: 페이지/확인된 영역의 누락, OCR 미완료, 미검증 수식 또는 실패한 표·그림 추출 등이 남았습니다.
- `FAILED`: 문서를 열 수 없거나 처리 실행이 실패했습니다.

단어·문자 수 차이는 이상 징후일 뿐 누락의 증거로 단정하지 않습니다. 원본 블록·좌표·페이지 이미지·TEI·각 파서의 후보를 비교할 수 있도록 남깁니다. 디지털 원문에 추출 오류가 이미 포함되어 있거나 탐지기가 요소 자체를 놓친 경우까지 자동 판별하지는 못합니다. `quality_report.json`을 확인하고 필요한 논문을 다시 처리하거나 직접 검토해야 합니다.

## 실제 검증 기록

오프라인 fixture는 전체 페이지 범위, 빈 페이지, 두 단 읽기 순서, 병합·빈 셀 표, 수식, 스캔 PDF, 참고문헌 부재, 이미지 파일 참조, 페이지 실패, Docling 누락 페이지/문자, 손상·암호화 PDF, TEI 구조와 외부 entity 거부, 엄격한 셀 행렬 비교, 수식의 추정 금지를 검사합니다.

Docling 2.144.0의 실제 모델로 텍스트·표·그림·수식이 있는 합성 PDF를 처리했습니다. 최초 모델 확보를 포함해 약 64초, 최대 RSS 약 1.56 GB였으며, 원문 수식을 확정하지 않아 `PARTIAL`로 기록했습니다. 캐시를 이용한 기본 엔진 테스트와 명시적 모델 경로를 이용한 오프라인 테스트도 통과했습니다. 이후 실제 논문에 GROBID를 호출하고 OCRmyPDF로 혼합·래스터 입력을 처리하는 통합 검증도 완료했습니다. 기존 PARTIAL 논문은 GROBID 참고문헌을 추가한 뒤에도 수식·표 검토가 남아 PARTIAL을 유지합니다. 테스트로 확인하지 않은 기능의 정확성을 성공으로 간주하지 않습니다.

참고 문서: [Docling 파이프라인 옵션](https://docling-project.github.io/docling/reference/pipeline_options/), [PyMuPDF Page API](https://pymupdf.readthedocs.io/en/latest/page.html), [pdfplumber](https://github.com/jsvine/pdfplumber), [GROBID 서비스](https://grobid.readthedocs.io/en/latest/Grobid-service/), [OCRmyPDF redo OCR](https://ocrmypdf.readthedocs.io/en/stable/advanced.html).
