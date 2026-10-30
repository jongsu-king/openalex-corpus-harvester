# GROBID·OCR 실제 연동 및 PARTIAL 재처리

검증일: **2026-10-29, Asia/Seoul**. 기존 원본·추출 이력을 유지하면서 선택적 구성요소를 실제 설치하고 검증했습니다.

## 설치 및 서비스

| 구성요소 | 설치한 버전 | 확인 |
|---|---|---|
| OpenJDK | 21.0.13 | Homebrew JDK를 명시적 경로로 사용 |
| GROBID | 0.9.1 | 공식 소스·모델로 macOS ARM 네이티브 빌드, 실제 fulltext REST 응답 확인 |
| OCRmyPDF | 17.13.0 | 프로젝트 가상환경에 설치 |
| Tesseract | 5.5.3 | English/OSD 모델 사용 |
| Ghostscript | 10.08.0 | 실제 OCR 파이프라인에서 사용 |

GROBID는 `127.0.0.1:8070`에서 실행 중입니다. 관리 포트 8071도 loopback에만 바인딩합니다. 동시 처리 1개, Java heap 1,536 MiB, pdfalto 메모리 제한 1,024 MiB이며 모델 로드 후 RSS는 약 1.6–1.8 GiB였습니다. 로그인 자동 시작이나 전역 셸 설정은 추가하지 않았습니다. 시작·상태·종료는 `scripts/grobid_service.py`로 관리합니다.

배포 패키징 중 upstream Gradle의 중복 JAR 처리가 실패하여, 배포 ZIP의 정책과 같은 duplicate EXCLUDE를 로컬 init 설정에 적용했습니다. 공식 소스는 덮어쓰지 않았으며 운영 스크립트와 설치 증거에 재현 방법을 남겼습니다. 상세: [GROBID 설정](GROBID_SETUP.md).

## GROBID 실제 검증

실제 논문 `W4402950921`의 원본 PDF에 `/api/processFulltextDocument`를 호출했습니다.

- TEI XML 103,023 bytes, 구조 항목 108개.
- 구조화된 참고문헌 16개, 원문 인용 문자열 16개, 페이지·좌표 근거 16개.
- 원본 PDF SHA-256 변경 없음.
- 실제 서비스 opt-in 테스트 1개 통과, 로컬 TEI 테스트 통과.
- Crossref 등 외부 consolidation은 비활성화했습니다.

GROBID는 이 PDF의 부록에 포함된 다른 논문의 예시 제목을 문서 제목으로 선택했습니다. 실제 OpenAlex 제목 `Docling Technical Report`와 다른 `DocLayNet: A Large Human-Annotated Dataset for Document-Layout Analysis`를 반환한 것입니다. 따라서 **두 제목을 출처별로 보존하고 `METADATA_TITLE_DISAGREEMENT`를 기록**했습니다. 기존 메타데이터를 교체하거나 잘못된 값을 정답으로 승격하지 않았습니다.

실행 증거: `data/validation/grobid/integration_evidence.json`, 원본 TEI 및 서비스 로그. 새 결과의 `academic_parser`에 서비스 성공 상태와 TEI가 보고한 GROBID 0.9.1 버전을 보존합니다.

## OCR 실제 검증

실제 OCR opt-in 테스트 **2개 통과**. 원본 파일을 수정하지 않고 별도 시험 입력과 OCR 파생 PDF를 생성했습니다.

| 입력 | OCR 전 문자 수 | OCR 후 문자 수 | 검증 |
|---|---|---|---|
| 디지털 1페이지 + 스캔 1페이지의 혼합 fixture | `[47, 0]` | `[47, 100]` | 스캔 페이지 2만 OCR. 디지털 페이지는 원래 출처 유지 |
| 실제 논문 1페이지를 별도 raster PDF로 만든 시험 입력 | `[0]` | `[2241]` | `Docling` 원문 문자열 검출, 페이지 수 유지 |

두 입력과 실제 원본 PDF의 해시가 그대로 유지됐습니다. 출력 PDF 열기·페이지 수·해시를 검사하고 시각적으로 원본 모습이 보존되는 것을 확인했습니다. 실제 논문이 원래 스캔 문서였다고 주장하지 않습니다. OCR 검증을 위해 명시적으로 래스터화한 별도 입력입니다.

가상환경의 `ocrmypdf`가 셸 PATH에 없더라도 현재 Python의 sibling 실행 파일을 찾도록 개선했습니다. OCR 성공 종료 코드만 믿지 않고 원본 불변성·출력 PDF·페이지 수를 검사합니다. 근거: `data/validation/ocr/latest_evidence.json`, [OCR 설정 문서](OCR_SETUP.md).

## 기존 PARTIAL 논문 재처리

```bash
.venv/bin/openalex-corpus --config config/enhanced.yaml reparse --status PARTIAL --max-works 10
```

대상: `W4402950921` / `10.48550/arxiv.2408.09869`.

- **9/9페이지 처리**, 원본과 이전 추출 이력 유지.
- GROBID 연결 성공: 구조화 참고문헌 16개 추가. 원래 텍스트 후보 16개와 구분하여 총 32개 후보 보존.
- 디지털 텍스트가 있어 OCR은 `not_required`. 불필요하게 원문을 이미지로 다시 읽지 않았습니다.
- 기본 엔진 Docling, PyMuPDF, pdfplumber 및 학술 구조 보조 엔진 GROBID를 함께 사용.
- **결과는 계속 `PARTIAL`**. 수식 3개 후보의 구조, 표·읽기 순서의 파서 불일치, 제목 충돌 등 검토할 근거가 남습니다. GROBID/OCR 설치만으로 이 문제를 해결했다고 표시하지 않습니다.

실행 결과: `data/validation/enhanced_reparse.json`. 선택된 실행: `data/papers/W4402950921/current.json`. 무결성 재검사: `data/validation/enhanced_verify.json`.

## OPV 검색 현황

Organic photovoltaics 관련 표현을 OpenAlex 제목·초록·키워드에서 합친 최종 검색은 **82,363건의 후보**를 보고했습니다. 연도·문헌 유형·OA 여부를 제한하지 않았으며 실제 OPV 관련성 검증 완료 목록과는 다릅니다. 검색 범위와 오탐·누락 한계는 [OPV 검색 문서](OPV_SCOPE.md)에 명시했습니다.

첫페이지 조사 8개에서 고유 원본 **608건**을 보존했고, 최종 합집합의 **첫 100건**은 별도 `data/opv` corpus에 정상 저장했습니다. 현재 수집은 `PAUSED_API_KEY_REQUIRED`이며, `.env`의 키 값이 비어 있어 다음 cursor를 요청하지 않았습니다. **82,363건 전체를 이미 수집했다는 뜻이 아닙니다.** 실제 현재 상태는 `data/opv/collection_report.json`이 기준입니다.

- 발견된 608건의 보기용 목록: `data/opv_discovery/opv_discovered_candidates_608.csv`.
- 원본 608건: `data/opv_discovery/first_pages_unique_raw.jsonl`.
- 최종 검색의 저장된 100건: `data/opv/exports/corpus.jsonl`, `corpus.parquet`.
- 키 입력 후 재개: `.venv/bin/python scripts/harvest_opv.py --env-file .env --all`.

기존 메타데이터 예산 $0.05/일을 임의로 올리지 않았습니다. `--all`에서도 이 예산과 서버 한도에 도달하면 안전하게 멈춥니다. 보관 콘텐츠 다운로드 예산은 0회/$0이며, OPV 단계는 메타데이터 목록을 먼저 수집합니다. 전체 검색이 끝나기 전에는 완전 수집으로 표시하지 않습니다.

## 검증 범위

추가 회귀 테스트는 TEI 제목 충돌 보존, OCR 출력 무결성, scoped exact 검색, 안전한 `.env` 읽기, OPV 청크·체크포인트·seed 검증, PDF 부재와 다운로드 오류의 구분을 포함합니다. 최종 기본 pytest는 **130 passed, 5 skipped**이며, 결과를 `data/validation/enhanced_pytest_offline.txt`에 보존합니다. Ruff와 소스 배포·wheel 빌드도 통과했습니다. 선택적 외부 API/Docling/GROBID/OCR 테스트의 기본 skip과 실제 별도 실행 결과를 구분합니다.

여전히 검증하지 않은 범위: 전체 OPV cursor 수집(API 키 대기), 장기간 수만 편 PDF의 부하 시험, Linux 실기기 실행, 유료 보관 콘텐츠 다운로드, PostgreSQL backend. 원문 수식·표의 의미적 완전 복원을 보장하지 않습니다.
