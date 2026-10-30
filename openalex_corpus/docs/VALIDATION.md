# 실제 실행 검증 기록

이 문서는 최초 구현 시점의 검증 기록입니다. 이후 GROBID/OCR 설치·실제 테스트와 PARTIAL 재처리를 완료했습니다. 현재 상태는 [후속 검증 기록](ENHANCED_VALIDATION.md)을 우선 확인하세요. 아래의 당시 미검증 항목은 이력으로 보존합니다.

검증일: **2026-10-29, Asia/Seoul**. 환경: macOS arm64, Python 3.11.16. 설계만 제공한 상태가 아니라 로컬 설치·실제 수집·다운로드·파싱·저장·내보내기까지 실행한 결과입니다.

## 테스트와 설치

| 검사 | 실제 결과 |
|---|---|
| 기본 pytest 전체 | **114 passed, 2 skipped** |
| 기본 실행에서 제외한 2개 | 실제 네트워크 singleton, 실제 Docling opt-in 테스트 |
| OpenAlex singleton opt-in | 별도로 실행하여 1 passed, 무료 단건 응답의 원본 52개 키 보존 |
| Docling 실제 실행 | 혼합 콘텐츠 fixture와 실제 논문에서 실행; 로컬 모델을 지정한 오프라인 실행도 통과 |
| Ruff | `src`, `tests`, `scripts` 검사 통과 |
| 잠금 환경 설치 | `uv sync --locked --python 3.11 --extra dev --extra docling` 성공 |
| 배포 패키지 | source distribution 및 wheel 빌드 성공, SQL 마이그레이션 포함 확인 |

기본 테스트 로그에는 PyMuPDF SWIG 객체의 Python `DeprecationWarning` 5개가 있습니다. 테스트 실패나 무시한 assertion은 없습니다. 모의 응답 테스트와 실제 외부 실행의 범위는 [TEST_MATRIX.md](TEST_MATRIX.md)에 분리했습니다.

주요 버전: HTTPX 0.28.1, Pydantic 2.14.1, Docling 2.144.0, PyMuPDF 1.28.2, pdfplumber 0.11.10, Pillow 12.3.0, lxml 6.1.3, pandas 3.1.0, PyArrow 23.0.1. 검증 조합 전체는 `requirements-tested.txt`, 설치 잠금은 `uv.lock`입니다. 새 환경에서 최신 버전으로 임의 업그레이드한 결과까지 보증하지 않습니다.

## 실제 OA 논문

OpenAlex API 키 없이 단건 조회와 DOI 필터 검색을 수행했습니다. PDF는 API가 제공한 arXiv OA URL에서 내려받았습니다. **OpenAlex 보관 콘텐츠 다운로드는 0회이며 해당 다운로드 예산은 $0**입니다.

| Work ID / DOI | 페이지 | 현재 상태 | 검증 결과 |
|---|---:|---|---|
| `W4229010617` / `10.48550/arxiv.2205.01833` | 5/5 | `COMPLETE_WITH_WARNINGS` | 기본 엔진·텍스트/표 교차 검증 실행. 참고문헌 구조 미검증과 벡터 영역 보존 경고가 남음 |
| `W4402950921` / `10.48550/arxiv.2408.09869` | 9/9 | `PARTIAL` | 모든 페이지 처리. 수식 구조·표 후보 불일치·읽기 순서·일부 원본 이미지 바이트 추출 등의 불확실성 기록 |

두 논문 모두 `docling+pymupdf+pdfplumber` 엔진으로 처리했습니다. **14페이지 중 건너뛴 페이지는 0**입니다. 두 번째 논문의 불확실성을 성공으로 숨기지 않았으며, 검토할 원본 페이지·좌표·그림·표·수식 영역·파서별 후보를 보존했습니다.

현재 추출에는 표 후보 13개, 그림 오브젝트/영역 후보 38개, 수식 후보 3개, 참고문헌 후보 17개가 있습니다. 이 숫자는 **논문 안의 실제 표·Figure·참고문헌 개수를 의미하지 않습니다.** 하나의 표/그림에 여러 파서 후보가 있고, 번호 없는 참고문헌 구역이 하나의 원문 후보로 남을 수 있습니다. `stats`는 후보 묶음과 image object/figure region을 별도로 보여 줍니다.

실제 실행한 주요 명령:

```bash
.venv/bin/openalex-corpus --config config/validation.yaml fetch \
  --doi 10.48550/arxiv.2205.01833 --download-pdf --parse-markdown
.venv/bin/openalex-corpus --config config/validation.yaml fetch \
  --doi 10.48550/arxiv.2408.09869 --download-pdf --parse-markdown
.venv/bin/openalex-corpus --config config/validation.yaml harvest \
  --doi 10.48550/arxiv.2205.01833 --max-works 1
.venv/bin/openalex-corpus --config config/validation.yaml reparse --max-works 2
.venv/bin/openalex-corpus reconcile
.venv/bin/openalex-corpus verify --all
```

처음에는 모델 다운로드를 명시적으로 허용해 설치한 뒤, 최종 재파싱은 `allow_model_downloads=false`와 준비된 모델 경로를 사용했습니다. 혼합 fixture의 첫 Docling 실행은 모델 준비를 포함하여 약 64초, 최대 RSS 약 1.56 GiB였습니다. 이는 해당 작은 fixture의 측정치이며 모든 PDF의 성능 한계는 아닙니다.

## 저장·출력 검증

- SQLite `integrity_check=ok`, foreign key 위반 없음.
- 두 논문의 모든 등록 파일 해시, 선택된 실행 manifest, PDF 출처 해시, 전체 페이지 처리 검사 통과.
- 상태 합계 2편 = 전체 논문 2편. `COMPLETE` 비율 0/2와 PDF 다운로드 성공률 2/2를 분리 집계.
- JSON, JSONL, CSV, Parquet, Markdown, SQLite의 실제 export 실행 성공.
- Markdown export의 이미지 참조 **41개 모두 존재**, 누락 0개.
- SQLite 온라인 백업 생성 및 백업 파일의 `integrity_check=ok` 확인.
- 실제 프로세스 실행에서 서드파티 stdout/stderr를 실행별 로그로 분리하여 CLI 결과 JSON을 그대로 파싱할 수 있음.

`verify.valid=true`는 저장된 증거의 무결성과 검사 범위에 대한 결과입니다. 두 번째 논문의 `PARTIAL`을 해제하거나 논문의 의미·수식·표가 완전히 복원되었다는 뜻이 아닙니다.

## 실패·복구 검증

HTTP 429·timeout·인증 오류, HTML/손상/암호화 PDF, Range 재개, 예산 소진, 동일 URL의 새 PDF 버전, 중간 페이지 체크포인트, 반복 실행을 테스트했습니다. metadata 저장·파일 rename·DB commit 사이 중단을 주입해 복구했습니다. 원본 JSON이나 성공한 추출 파일을 변조해 체크섬을 새 값으로 재승인하지 않는지, 손상된 이전 결과가 정상으로 재사용되지 않는지, 나쁜 재파싱이 정상 결과를 덮어쓰지 않는지 확인했습니다. 원본 데이터 경로를 export로 덮어쓰는 동작도 거부됩니다.

## 검증하지 않은 범위

- 실제 GROBID 서버: HTTP 연결 구현과 로컬 TEI/XML 보안 테스트는 있지만 서버 end-to-end는 미실행.
- 실제 OCRmyPDF/Tesseract 인식: 선택적 실행 구현과 스캔 실패 상태 검증은 있지만 OCR 실행 파일 미설치로 end-to-end는 미실행.
- OpenAlex 유료 보관 PDF/TEI 다운로드: 모의 예산·인증·우선순위 테스트만 수행.
- Linux 실기기·장기간 대규모 운영·수천 페이지 PDF의 성능/부하 검증.
- PostgreSQL: 선택적 확장 대상으로 문서화했으며 현재 구현하지 않음.

## 로컬 실행 증거

`data/validation/validation_summary.json`에 실제 논문별 상태·페이지·검사 결과·export 경로를 보존했습니다. 함께 저장한 증거는 `pytest_offline.txt`, `live_api_evidence.json`, `live_work.json`, `reparse_result.json`, `reconcile_result.json`, `verify_result.json`, `stats_result.json`, `doctor_result.json`입니다. 실제 데이터는 Git 제외 대상이며 로컬 작업 폴더에 남아 있습니다. 모델 파일 해시는 `data/models/docling/model_manifest.json`에 있습니다.
