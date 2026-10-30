# OpenAlex Corpus Harvester 개발·검증 이력

기록 범위: **2026-10-29 최초 구현·후속 기능 보강, 2026-10-30 개발 기록 정리·GitHub 게시 준비**. 사용자에게 표시하는 날짜와 시각은 Asia/Seoul(KST)을 기준으로 한다. 원본 JSON의 ISO 시각은 UTC로 보존되어 있으므로 둘을 구분한다.

이 문서는 구현 과정의 결정, 실행한 검사, 남은 제약을 재현할 수 있도록 정리한 기록이다. 2026-10-30에는 이미 저장된 코드·문서·실행 증거를 읽어 작성했으며, 이 기록 작성을 위해 OpenAlex를 새로 조회하거나 OPV 수집을 재개하지 않았다. 자격증명 파일의 내용도 읽지 않았다. **GitHub push 완료를 증명하는 문서는 아니다.** 실제 원격 게시 결과는 성공 응답과 게시된 commit을 확인한 뒤 별도로 기록한다.

빠른 실행은 [README](../README.md), 전체 흐름은 [구성도](diagrams/pipeline-overview.svg), 최초 실행 결과는 [최초 검증 기록](VALIDATION.md), 후속 GROBID/OCR 실행 결과는 [후속 검증 기록](ENHANCED_VALIDATION.md), 요구사항별 테스트 대응은 [테스트 매트릭스](TEST_MATRIX.md)를 참고한다.

## 1. 개발 목적과 범위

입력 요청은 설계 제안이 아니라 Python 3.11 이상에서 설치·실행할 수 있는 학술 원문 수집 프로그램이었다. 구현 범위는 다음 흐름으로 고정했다.

```text
OpenAlex 검색·원본 응답 보존
  → 논문 식별·메타데이터 정규화
  → 접근 가능한 PDF 후보 탐색·다운로드·검증
  → 여러 파서로 구조·본문·표·그림·수식·참고문헌 추출
  → 페이지·영역·파일 무결성 검사
  → Markdown·JSON·관계형 DB 저장
  → 중단 복구·재파싱·누적 수집·내보내기
```

논문 요약, LLM 기반 보완, 의미 재작성, 연구 평가, 추천, 임베딩, RAG, 웹 UI는 추가하지 않았다. 원본에서 확인하지 못한 내용은 생성하지 않고 누락 사유·출처·후보·원본 영역으로 남기는 방향을 택했다. 특히 PDF 다운로드 성공, 파서 실행 성공, 자동 품질 검사 통과, 원문 내용의 정확성을 같은 의미로 취급하지 않았다.

## 2. 주요 일정과 확인 가능한 근거

| 날짜·시각(KST) | 단계 | 수행·확인한 내용 | 증거 |
| --- | --- | --- | --- |
| 2026-10-29, 초기 개발 | 실행 환경·모듈 구현 | Python 3.11 가상환경, CLI, API·다운로드·파서·SQLite·검증·복구·테스트 구현 | 소스 트리, `pyproject.toml`, `uv.lock`, 최초 검증 문서 |
| 2026-10-29 11:58경 | 실제 API 단건 검증 | 키 없는 singleton 요청 1회, Work 응답 원본 52개 키 보존, 보관 콘텐츠 다운로드 0회 | `data/validation/live_api_evidence.json` |
| 2026-10-29, 최초 통합 검증 | 실제 OA 논문 2편 | 5페이지·9페이지 논문을 내려받고 총 14페이지 처리, export·백업·파일 무결성 검사 | `data/validation/validation_summary.json` |
| 2026-10-29, 최초 회귀 검사 | 기본 테스트 | 114 passed, 2 skipped; 별도 실제 singleton·Docling 검사 실행 | `data/validation/pytest_offline.txt`, 최초 검증 문서 |
| 2026-10-29 16:06경 | 실제 OCR 실행 | 혼합 2페이지 fixture와 실제 논문을 래스터화한 별도 입력에 OCR 실행 | `data/validation/ocr/latest_evidence.json` |
| 2026-10-29 16:09경 | 실제 GROBID 연동 | REST 전문 요청, TEI 103,023 bytes, 구조화 참고문헌 16개, 제목 충돌 검출 | `data/validation/grobid/integration_evidence.json` |
| 2026-10-29 16:12경 | 기존 PARTIAL 재처리 | 9/9페이지 재처리, GROBID 후보 추가, 수식·표·제목 불확실성 때문에 PARTIAL 유지 | `data/validation/enhanced_reparse.json`, 선택된 실행의 품질 보고서 |
| 2026-10-29 16:13경 | 서비스 설정 재검증 | GROBID 재시작 후 실제 서비스 테스트 1 passed, 엔진 풀 1/1·loopback 바인딩 확인 | GROBID 연동 증거의 `post_config_restart_validation` |
| 2026-10-29 16:09–16:22경 | OPV 검색 정의·목록 준비 | 8개 첫페이지에서 고유 608건 보존, 최종 검색 첫 100건 DB 저장, 다음 cursor는 키 필요 상태로 중단 | `data/opv_discovery/summary.json`, `data/opv/collection_report.json` |
| 2026-10-29, 후속 회귀 검사 | 기능 보강 검증 | 보존된 후속 기본 로그: 130 passed, 5 skipped, 5 warnings | `data/validation/enhanced_pytest_offline.txt` |
| 2026-10-30 | 개발 기록·게시 준비 | 기존 실행 근거와 한계를 정리하고 게시 가능한 코드·설정·문서와 로컬 데이터를 구분; 구성도·문서 탐색·이식 가능한 설치 안내 보강 | 본 문서, `.gitignore`, 저장소 문서·SVG 구성도 |
| 2026-10-30 | 게시 전 재검사 | 기본 pytest 130 passed, 5 skipped; Ruff 및 sdist·wheel 빌드 통과. SVG 개요도를 1600×1000으로 렌더링해 확인 | `data/validation/docs/pytest_pre_push.txt`, `data/validation/docs/pipeline-overview.png` |
| 2026-10-30 | 상세 도표 검증 | Mermaid 11.17.2로 파이프라인·예산 시퀀스·추출 복구·품질 판정·DB 관계도 5개를 파싱·렌더링하고 육안 확인. Markdown 내부 파일 링크 13개 문서 검사 통과 | `data/validation/docs/mermaid_validation.json`, `architecture-1.png`–`architecture-5.png` |

이 표의 분 단위 시각은 각 증거의 `tested_at`, `recorded_at`, `checked_at` 또는 추출 실행 이름을 KST로 바꾼 값이다. 날짜만 표시한 검사에 임의의 세부 시작·종료 시각을 부여하지 않았다. 과거 API가 반환한 후보 수를 2026-10-30의 새 조회 결과로 표시하지 않는다.

## 3. 구현 구조와 기술 선택

| 영역 | 주요 파일 | 선택 이유와 동작 |
| --- | --- | --- |
| 설정·실행 | `config.py`, `cli.py`, `config/*.yaml` | Pydantic으로 설정을 검증하고 YAML·환경변수를 분리한다. CLI는 JSON 결과와 명시적 종료 코드를 제공한다. |
| API | `api/client.py`, `api/search.py`, `api/pagination.py` | HTTPX로 실제 공식 검색 인수를 전송한다. per_page=100, cursor, 재시도, 서버 사용량 헤더, 영속 예산을 지원한다. |
| 메타데이터 | `metadata.py` | 원본을 복사해 필요한 값을 정규화하되 null·빈 문자열·빈 배열을 구분한다. 초록의 위치 정보가 모호하면 복원 실패와 후보를 남긴다. |
| 다운로드 | `download/pdf_downloader.py`, `download/validator.py` | 여러 PDF 후보를 순서대로 시도하고 HTTP·시그니처·크기·열기·암호화·페이지·SHA-256을 검사한다. 원본과 버전을 구분한다. |
| 기본 구조 파싱 | `parsers/docling_parser.py` | 논리 구조·Markdown·표·그림 영역의 기본 엔진으로 Docling을 사용한다. 선택적 의존성이 없어도 다른 단계는 작동한다. |
| 페이지 원본 근거 | `parsers/pymupdf_parser.py` | 모든 페이지의 텍스트 블록·span·좌표·이미지·렌더링 근거를 별도로 확보한다. 구조 파서의 출력만으로 페이지 완료를 판정하지 않는다. |
| 표·수식·참고문헌 | `table_parser.py`, `equation_parser.py`, `reference_parser.py` | pdfplumber 비교, 병합 셀·빈 셀·원문 문자열 보존, 불확실한 수식 후보와 원본 영역 보존을 담당한다. |
| 학술 구조·OCR | `parsers/grobid_parser.py`, `parsers/ocr.py` | TEI와 실제 서비스 응답은 원문 보조 근거로 사용한다. OCR은 필요한 페이지만 선택하고 별도 파생 파일로 남긴다. |
| 저장 | `database/repository.py`, `database/migrations/001_initial.sql` | SQLite WAL·FK·FULL 동기화·트랜잭션을 사용한다. 정규화 관계와 불변 원본·추출 이력을 함께 보존한다. |
| 품질·복구 | `quality/checker.py`, `quality/recovery.py`, `pipeline.py` | 페이지 범위·파일·후보 불일치·미해결 요소를 검사한다. 더 나쁜 재파싱이 정상 결과를 대체하지 않도록 한다. |
| 출력 | `export.py` | JSON·JSONL·CSV·Parquet·Markdown·SQLite를 지원한다. 중첩 메타데이터를 유지하고 corpus 원본 경로 덮어쓰기를 거부한다. |
| OPV 전용 수집 | `opv.py`, `scripts/discover_opv.py`, `scripts/harvest_opv.py` | 검색 정의·첫페이지 조사·전체 후보 목록 cursor 재개를 분리하고 메타데이터 완료와 PDF 처리 완료를 따로 보고한다. |
| 운영·재현 | `scripts/prepare_models.py`, `scripts/grobid_service.py`, `scripts/validate_ocr.py` | 모델 준비, 로컬 학술 서비스 관리, 실제 OCR 검증을 명시적 명령으로 제공한다. |
| 개발 문서 | `docs/DEVELOPMENT_LOG.md`, `docs/diagrams/pipeline-overview.svg`, `scripts/render_architecture.py` | 구현·검증 이력과 재생성 가능한 구성도를 제공하고 README에서 세부 문서로 연결한다. |

### 환경과 버전

실제 설치·실행 검증 환경은 **macOS arm64, Python 3.11.16**이다. 프로젝트는 Python 3.11 이상을 요구하지만 Python의 모든 새 버전·OS 조합을 검증했다는 뜻은 아니다. 기본 시스템 Python과 분리된 가상환경을 사용해 Docling 및 네이티브 PDF 라이브러리의 의존성을 관리했다.

초기 검증 조합은 HTTPX 0.28.1, Pydantic 2.14.1, Docling 2.144.0, PyMuPDF 1.28.2, pdfplumber 0.11.10, Pillow 12.3.0, lxml 6.1.3, pandas 3.1.0, PyArrow 23.0.1이다. 후속으로 OpenJDK 21.0.13, GROBID 0.9.1, OCRmyPDF 17.13.0, Tesseract 5.5.3, Ghostscript 10.08.0을 실제 설치·사용했다.

설치 재현의 기준은 [`uv.lock`](../uv.lock)과 [`requirements-tested.txt`](../requirements-tested.txt)이다. `pyproject.toml`의 허용 범위가 곧 모든 버전 조합의 성공 증거는 아니다. Python 패키지는 잠금으로 관리하고 Tesseract·Ghostscript·JDK 같은 시스템 의존성은 [OCR 설치](OCR_SETUP.md), [GROBID 설치](GROBID_SETUP.md)에 별도로 기록했다.

### 원본 보존과 중복 처리

OpenAlex Work ID를 corpus의 기준 식별자로 삼았다. DOI는 정규화하고 충돌을 조회할 수 있게 했지만 unique 제약으로 서로 다른 Work ID를 합치지 않았다. 저자 순서와 복수 기관 연결은 관계 테이블로 저장하며 원본 JSON에도 그대로 남긴다.

메타데이터는 내용 기준 해시를 가진 `metadata_history`와 최신 편의 사본을 함께 유지한다. PDF는 첫 정상 원본과 SHA-256별 버전을 보존한다. 파싱은 실행별 디렉터리에 결과를 저장하고 `current.json`이 선택된 실행을 가리킨다. 확인하기 쉬운 `paper.md` 등은 이 실행으로 연결되는 편의 링크다. 상세 규칙은 [데이터베이스 문서](DATABASE.md)에 있다.

파일시스템과 SQLite를 하나의 트랜잭션으로 묶을 수 없으므로, 임시 다운로드·추출 staging·메타데이터 pending·manifest·DB 이력으로 중간 상태를 재구성하도록 했다. 크롤은 cursor뿐 아니라 보존한 응답의 해시와 페이지 내부 offset을 기록한다. 같은 배치를 다시 실행해도 기존 원본을 삭제하거나 같은 Work ID를 중복 생성하지 않는다.

## 4. 초기 검증에서 확인한 문제와 보강

다음 보강은 정상 경로만 검사하면 놓치기 쉬운 손실·거짓 성공을 막기 위한 것이다. 정상 응답을 모방한 테스트와 의도적으로 오류를 주입한 회귀 테스트를 함께 작성했다.

| 확인한 위험 | 보강한 동작 |
| --- | --- |
| 초록 인덱스 위치가 빠지거나 서로 다른 단어가 같은 위치를 가짐 | 빈 위치를 추정하지 않고 `parse_failed`, 원본 구조와 위치 후보 보존 |
| 원본 JSON 정규화 중 알 수 없는 필드·중첩 정보 손실 | 원본 전체 JSON과 변경 이력 보존, 중첩 필드별 source/missing 상태 추가 |
| HTML 오류 페이지를 PDF로 오인 | Content-Type만 신뢰하지 않고 시그니처·PDF 열기·페이지 수까지 확인 |
| 중단된 다운로드를 다른 파일 버전에 이어 붙임 | validator와 Content-Range가 일치하는 경우에만 재개, 서버가 Range를 무시하면 안전하게 재시작 |
| 같은 URL의 PDF가 바뀌어 기존 원고를 덮어씀 | 해시별 원고 버전 보존, 명시적 `--refresh-pdf` 지원 |
| 모델·네이티브 파서 오류 또는 과도한 메모리 사용 | 별도 프로세스에서 실행, 자식 프로세스 포함 RSS·시간 제한 감시, 실패 증거와 재처리 상태 기록 |
| 전체 페이지 중 일부만 추출했는데 성공으로 보고 | 기대 페이지 집합·실제 처리 페이지·빈 페이지·실패 페이지를 분리하고 미처리 페이지는 성공을 차단 |
| 이전 성공 결과가 존재한다는 이유만으로 손상된 Markdown을 재사용 | 선택된 manifest와 파일·원본 PDF 체크섬을 검사한 뒤에만 재사용 |
| 변조된 원본 메타데이터의 새 체크섬을 정상 값으로 다시 등록 | 기존 신뢰 해시와 내용 주소를 먼저 검증하고 불일치 시 오류 처리 |
| 파일 rename 이후 DB 등록 전에 종료 | 완성된 실행 디렉터리를 검증해 안정된 run ID로 멱등 등록·복구 |
| 실패한 새 추출이 정상 이전 결과를 덮어씀 | 유효한 이전 실행의 품질 등급과 비교해 더 나쁜 결과의 승격을 막음 |
| `verify`가 실패했는데 프로세스는 0으로 종료 | 무결성 실패·실패 작업·일시정지 상태의 종료 코드와 JSON을 명확히 구분 |
| export 경로 실수로 원본 DB·PDF·메타데이터 파괴 | 원본 corpus 경로를 보호하고 Markdown export는 새 목적지로 원자적 완성 |
| 재실행·청크 반복으로 요청/다운로드 예산 초기화 | 요청 전 영속 예산 예약, 전체 OPV 실행에서 downloader 공유, 재시도 한도 보존 |
| 목록 cursor는 끝났지만 요청한 PDF·파싱이 미완료 | `metadata_status`와 전체 처리 상태를 분리해 `QUERY_COMPLETE_PROCESSING_INCOMPLETE` 보고 |

모든 수식·표·문단 경계를 자동으로 정확하게 복구하는 해결책을 구현했다고 주장하지 않는다. 파서 간 불일치에는 원시 텍스트, 후보 표, 원본 페이지·좌표·영역 이미지를 남겼으며 사람이 검토하거나 이후 파서를 개선해 재처리할 수 있게 했다.

## 5. 실제 실행과 모의 테스트의 구분

### 기본 회귀 검사

보존된 초기 로그는 **114 passed, 2 skipped, 5 warnings**, 후속 로그는 **130 passed, 5 skipped, 5 warnings**이다. 후속 skipped 5개는 기본 실행에서 꺼 둔 실제 API 1개, Docling 1개, GROBID 1개, OCR 2개에 해당한다. 이 선택적 검사는 별도의 활성화 명령으로 실제 실행했고, 기본 테스트의 skip을 기능 실패나 실제 실행 증거로 혼동하지 않는다.

2026-10-29의 130은 `data/validation/enhanced_pytest_offline.txt`에 저장된 후속 결과다. 2026-10-30 게시 전에도 기본 전체 테스트를 다시 실행해 **130 passed, 5 skipped, 5 warnings**를 확인했고 새 로그를 `data/validation/docs/pytest_pre_push.txt`에 남겼다. 실행 시간은 약 3.99초였다. Ruff(`src tests scripts`), `uv build`의 sdist·wheel 생성, 잠금 파일 검사도 통과했다. 기존 114의 기록은 초기 이력으로 보존한다.

네트워크 응답의 429·timeout·인증 오류, 비용 헤더, Range 재개·거부, 손상 파일, 체크포인트/DB commit 중단 등은 주로 HTTPX 모의 응답이나 실패 주입으로 재현했다. 합성 PDF는 실제 PyMuPDF·pdfplumber 등을 실행하는 입력으로 사용했다. 파서의 반환값을 주입해 비교 오류를 확인하는 테스트와 실제 Docling을 실행하는 테스트도 구분했다. 구체적인 테스트명은 [TEST_MATRIX.md](TEST_MATRIX.md)에 있다.

보존된 초기·후속 검증 기록에는 Ruff 검사와 source distribution/wheel 빌드 성공이 기록되어 있다. wheel에 SQLite SQL 마이그레이션이 포함되는 것도 확인했다. PyMuPDF SWIG 객체의 `DeprecationWarning` 5개는 경고로 남았으며, 이를 실패한 assertion을 무시한 것으로 계산하지 않았다.

### 실제 OpenAlex·OA 논문

| 논문 | 실제 처리 | 보존된 결과 |
| --- | --- | --- |
| `W4229010617`, DOI `10.48550/arxiv.2205.01833` | OpenAlex 메타데이터, arXiv OA PDF, 5/5페이지, Docling+PyMuPDF+pdfplumber | `COMPLETE_WITH_WARNINGS`; 참고문헌 구조 및 벡터 영역 보존 경고 |
| `W4402950921`, DOI `10.48550/arxiv.2408.09869` | OpenAlex 메타데이터, arXiv OA PDF, 9/9페이지, 이후 GROBID 보강 재처리 | `PARTIAL`; 수식·표·읽기 순서·제목 충돌 등 미해결 근거 유지 |

두 논문의 원본 다운로드와 14페이지 처리를 실제 실행했다. 초기 검증에서 JSON·JSONL·CSV·Parquet·Markdown·SQLite export와 SQLite 온라인 백업도 실행했다. Markdown export의 이미지 참조 41개가 모두 존재했고, DB integrity 및 FK 검사, 등록 파일의 해시 검사도 통과했다.

이 값은 실제 corpus 2편에 대한 검사다. 수천 편 규모의 성공률 추정이나 모든 학술 분야·레이아웃에서의 정답률로 일반화하지 않는다. 원본 PDF는 외부 OA 주소로 수집했으며 OpenAlex 보관 PDF/TEI 다운로드를 실제로 유료 실행한 기록은 없다.

### GROBID 보강: 연결 성공과 내용 정확성을 분리

GROBID 0.9.1을 공식 소스·모델로 macOS ARM 네이티브 빌드하고 `127.0.0.1:8070`에서 실제 REST 전문 요청을 수행했다. 관리 포트 8071도 loopback으로 제한했다. 운영 스크립트는 이 서비스의 시작·상태·종료를 제공하며 전역 자동 시작 설정은 추가하지 않았다.

초기 빌드의 중복 JAR 처리 문제는 원본 소스를 변경하지 않고 로컬 Gradle init의 `DuplicatesStrategy.EXCLUDE`로 조정했다. Java heap 1,536 MiB, pdfalto 1,024 MiB/120초, 처리 엔진 1개를 적용했다. 재시작 후 실제 요청의 RSS 약 1,658.5 MiB는 그 환경의 측정값이며 전체 파이프라인 메모리 상한이 아니다. 2026-10-30에 이 문서를 쓰며 서비스가 여전히 실행 중인지 새로 확인하지는 않았다.

`W4402950921`에서 TEI 103,023 bytes, 구조 항목 108개, 구조화 참고문헌 16개를 얻었다. 16개 모두 원문 인용 문자열과 좌표가 있었고, source PDF 해시는 유지됐다. 외부 Crossref 등 consolidation은 header/citations/funders 모두 비활성화했다.

같은 응답은 문서 안의 예시 제목인 `DocLayNet: A Large Human-Annotated Dataset for Document-Layout Analysis`를 선택해 OpenAlex의 `Docling Technical Report`와 충돌했다. 이를 실제 실패 사례로 기록하고 `METADATA_TITLE_DISAGREEMENT`를 남겼다. 서비스 연결 성공만으로 해당 제목을 정답으로 승인하거나 기존 메타데이터를 덮어쓰지 않았다.

### OCR 보강: 원본을 유지하는 선택적 실행

OCRmyPDF 17.13.0, Tesseract 5.5.3, Ghostscript 10.08.0을 실제로 설치하고 **OCR opt-in 테스트 2개**를 실행했다. 영어 `eng`와 OSD 모델을 사용했으며 한국어 등 다른 언어의 인식 품질은 검증하지 않았다.

| 시험 입력 | OCR 전 문자 수 | OCR 후 문자 수 | 확인한 사항 |
| --- | --- | --- | --- |
| 디지털 1페이지 + 스캔 1페이지의 합성 fixture | `[47, 0]` | `[47, 100]` | 페이지 2만 OCR, 원래 디지털 페이지의 출처 유지 |
| 실제 논문 첫 페이지를 별도로 래스터화한 입력 | `[0]` | `[2241]` | `Docling` 문자열 확인, 1페이지 유지, 원본 논문 불변 |

두 번째 입력은 검증 목적으로 만든 파생 입력이다. 원래 스캔 문헌을 새로 수집했다고 표시하지 않는다. 실제 source PDF와 각각의 시험 입력 해시는 OCR 전후 같았고 OCR 출력은 별도 파일에 저장됐다. 이 검증의 네트워크 요청은 0회였다. 성공 종료 코드만 확인하지 않고 출력 PDF 열기·페이지 수·해시·추출 문자와 시각적 보존을 확인했다.

### PARTIAL을 해제하지 않은 이유

후속 재처리는 `W4402950921`의 9페이지 전체에 대해 수행했다. 원래 참고문헌 원문 후보 16개와 GROBID 구조화 후보 16개를 출처별로 보존해 총 32개 후보가 됐다. 이는 서로 다른 실제 참고문헌이 32개라는 뜻이 아니다.

선택된 실행에는 `EQUATION_UNVERIFIED`, `equation_parse_failed`, `TABLE_UNVERIFIED`, `docling_table_values_disagree`, `table_cell_values_disagree`, `table_detection_disagreement`, `reading_order_requires_review`, `primary_text_disagreement`, `text_parser_disagreement`, `inline_image_original_bytes_unavailable`, `vector_graphics_preserved_in_page_image`, `METADATA_TITLE_DISAGREEMENT` 등의 근거가 남는다. 수식 후보는 3개다.

이미 디지털 텍스트가 있는 원본이어서 이 재처리의 OCR 상태는 `not_required`였다. OCR을 설치했다는 이유로 원문을 다시 이미지로 읽거나 수식 문제를 해결한 것으로 바꾸지 않았다. 후속 `enhanced_verify.json`의 `valid=true`는 저장 무결성과 정해진 검사 범위를 통과했다는 뜻이며, 논문의 `PARTIAL` 상태를 자동으로 해제하지 않는다.

## 6. OPV 검색과 저장된 수집 상태

OPV는 organic photovoltaics / organic solar cells, 즉 유기 태양광으로 해석했다. 제목·초록·색인 키워드의 영어 표기와 OPV 약어의 태양광 문맥을 조합했으며, 연도·언어·문헌 유형·OA 여부로 제한하지 않았다. 연구 논문만이 아니라 OpenAlex Work의 다른 유형도 포함될 수 있고, 이는 관련성 심사를 마친 정답 목록이 아니다. 검색 정의와 한계는 [OPV_SCOPE.md](OPV_SCOPE.md)에 기록했다.

| 구분 | 저장된 값 | 의미 |
| --- | ---: | --- |
| 조사한 첫페이지 | 8개 | 서로 다른 검색 정의별 첫 100개 |
| 첫페이지 출현 합계 | 800건 | 검색 간 중복을 포함한 수 |
| 고유 원본 Work ID | 608건 | 조사한 첫페이지들의 합집합 |
| 중복 출현 | 192건 | 800 출현에서 같은 Work ID가 반복된 수 |
| 최종 expanded_union의 API 보고 수 | 82,363건 | 2026-10-29 당시 단일 최종 쿼리에 대한 전체 일치 수 |
| 최종 검색 corpus에 저장한 고유 Work ID | 100건 | 최종 검색 첫페이지를 `data/opv` DB에 적재한 수 |
| 최종 검색의 당시 저장 비율 | 약 0.1214% | 100 / 82,363; PDF 확보율이나 관련성 정확도가 아님 |
| 보고서 상태 | `PAUSED_API_KEY_REQUIRED` | 다음 cursor에 필요한 API 키가 없다고 기록된 실행 상태 |

608개 조사 원본과 최종 검색 DB 100개는 서로 다른 저장 범위다. 608개가 모두 최종 검색 corpus에 추가된 것으로 계산하지 않는다. 또한 82,363건을 이미 수집했거나 전 세계 OPV 문헌 전체를 확보했다는 뜻도 아니다.

문법 조사 중 단독 wildcard 구문은 허용됐으나 Boolean 안의 wildcard 문구가 실제 API에서 HTTP 400을 반환했다. 거부 응답을 보존하고 최종 Boolean 검색은 단수·복수를 명시적으로 나열하도록 바꿨다. 첫페이지 조사에는 거부된 요청을 포함해 10회와 보수적 예약액 $0.0100을 기록했다. 검색별 전체 수는 서로 겹치므로 합산하지 않았다.

**이 현황은 2026-10-29 16:22:56경 KST에 저장된 `collection_report.json`의 스냅샷을 2026-10-30에 확인한 것이다.** 이번 문서 작성에서 새 API 호출, 새 cursor 수집, 자격증명 상태 확인을 하지 않았다. 당시 보고서는 `api_key_present=false`, `query_exhausted=false`, `all_world_literature_complete=false`, 메타데이터 전용 수집을 기록한다. 이후 키나 corpus 상태가 바뀌면 새 실행 보고서가 이 기록보다 우선한다.

기본 예산은 메타데이터 $0.05/UTC일·1,000요청이며 보관 콘텐츠는 0회/$0이다. `--all`은 문헌 수 제한을 해제하는 옵션이며 금액·요청·서버·인증·재시도 제한을 해제하지 않는다. 당시에는 키 없는 cursor 제한을 날짜 분할이나 다른 우회 수집으로 넘지 않았다.

## 7. 재현·재개 명령

아래 명령은 프로젝트 루트 `openalex_corpus`에서 실행한다. 운영 방법을 기록한 것으로, 이 문서를 작성하면서 다시 실행했다는 뜻은 아니다. 실행 환경과 사용자가 지정한 예산을 유지해야 한다.

```bash
# 잠금된 환경 설치: GROBID의 JDK 및 OCR 시스템 의존성은 별도 문서 참조
uv sync --locked --python 3.11 --extra dev --extra docling --extra ocr
.venv/bin/openalex-corpus doctor

# 기본 회귀 검사: 선택적 실제 통합 테스트는 기본적으로 skip
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests scripts

# 실제 로컬 서비스 시작·상태 확인
.venv/bin/python scripts/grobid_service.py start
.venv/bin/python scripts/grobid_service.py status

# 선택적 실제 실행 검사
OPENALEX_RUN_NETWORK=1 .venv/bin/python -m pytest -q tests/test_integration.py
OPENALEX_RUN_DOCLING=1 DOCLING_ARTIFACTS_PATH="$PWD/data/models/docling" \
  OPENALEX_ALLOW_MODEL_DOWNLOADS=0 \
  .venv/bin/python -m pytest -q tests/test_parsers.py -k actual_docling
OPENALEX_RUN_GROBID=1 .venv/bin/python -m pytest -q tests/test_grobid_integration.py
OPENALEX_RUN_OCR=1 .venv/bin/python -m pytest -q tests/test_live_ocr.py

# 원본을 보존한 채 선택적 엔진으로 기존 PARTIAL 재처리
.venv/bin/openalex-corpus --config config/enhanced.yaml \
  reparse --status PARTIAL --max-works 10
.venv/bin/openalex-corpus --config config/enhanced.yaml verify --all

# 사용자가 프로젝트 .env에 키를 준비한 후 명시적으로 읽어 OPV 재개
.venv/bin/python scripts/harvest_opv.py --env-file .env --all
.venv/bin/openalex-corpus --config config/opv.yaml stats
.venv/bin/openalex-corpus --config config/opv.yaml export --format jsonl
.venv/bin/openalex-corpus --config config/opv.yaml export --format parquet

# 중단된 저장·추출 복구와 무결성 확인
.venv/bin/openalex-corpus --config config/opv.yaml reconcile
.venv/bin/openalex-corpus --config config/opv.yaml verify --all
```

일반 CLI는 환경변수 `OPENALEX_API_KEY`를 읽는다. OPV 전용 스크립트만 `--env-file`을 명시했을 때 지정된 파일에서 해당 키를 가져온다. 이 파일을 셸 코드로 실행하지 않고 보간도 비활성화한다. 새로 clone한 저장소에는 모델·서비스·원본 corpus가 없으므로 기존 로컬 데이터와 같은 결과를 바로 재개할 수 없다. 모델 준비와 서비스 설치는 각 문서의 명령으로 별도 수행해야 한다.

## 8. 로컬 실행 증거와 GitHub 포함 범위

코드·설정·잠금·테스트·운영 스크립트·문서는 저장소에 포함할 대상이다. 원본 corpus, API 응답, PDF, DB, 실행 로그, OCR 입력/파생물, 내려받은 모델, 빌드한 서비스, 가상환경과 자격증명은 로컬에 남긴다. `.gitignore`는 `data/`, `logs/`, `.venv/`, `.env`, 캐시·빌드 출력을 제외한다.

2026-10-30 게시 준비 대상은 비공개 GitHub 저장소 `jongsu-king/openalex-corpus-harvester`로 계획했다. 이 단계에서는 계정·권한·원격 저장소·commit·push 결과를 각각 확인해야 하며, 계획된 이름만으로 게시 완료를 표시하지 않는다. README의 설치 안내와 예제 설정에는 특정 사용자의 자격증명이나 로컬 모델·corpus가 함께 배포된다는 가정을 넣지 않는다.

아래 경로는 **로컬 작업 폴더의 증거 위치**이며 GitHub에 함께 올리는 파일 링크가 아니다. 공개 저장소에서 없는 파일로 이동하는 링크를 만들지 않기 위해 코드 형식으로만 표기한다.

| 로컬 전용 경로 | 보존 목적 |
| --- | --- |
| `data/validation/pytest_offline.txt` | 초기 기본 테스트 114 passed / 2 skipped |
| `data/validation/enhanced_pytest_offline.txt` | 후속 기본 테스트 130 passed / 5 skipped |
| `data/validation/live_api_evidence.json` | 실제 singleton 요청 수·원본 키 수·당시 요금 헤더 |
| `data/validation/validation_summary.json` | 최초 실제 논문 2편·내보내기·백업·무결성 결과 |
| `data/validation/grobid/integration_evidence.json` | 실제 GROBID 버전·TEI·좌표·참고문헌·제목 충돌·재시작 후 검증 |
| `data/validation/ocr/latest_evidence.json` | 실제 OCR 2개 입력·출력·문자 수·해시·불확실성 |
| `data/validation/enhanced_reparse.json` | 9페이지 PARTIAL 논문의 후속 실행 식별자·상태 |
| `data/validation/enhanced_verify.json` | 후속 DB·파일 무결성 검사 결과 |
| `data/papers/W4229010617/current.json`, `data/papers/W4402950921/current.json` | 실제 corpus의 선택된 불변 추출 실행 |
| `data/opv_discovery/summary.json` | 검색별 첫페이지 조사·겹침·고유 ID·예약 예산 |
| `data/opv_discovery/first_pages_unique_raw.jsonl` | 조사한 고유 608건의 원본 Work 객체 |
| `data/opv/collection_report.json`, `data/opv/collection_runs/` | 최종 검색 목록의 최신·실행별 저장 진행 상태 |
| `data/opv/database/corpus.sqlite`, `data/opv/exports/` | 최종 검색 첫 100건과 현재 내보내기 |

저장소 게시와 corpus 백업은 다른 작업이다. SQLite 온라인 백업은 PDF·원본 JSON 파일을 포함하지 않으므로 `data/papers`, `data/crawls`, `data/state`와 해당 실행 증거도 함께 백업해야 한다. 기록된 절대 파일 경로가 있어 다른 위치로 옮길 때에는 DB artifact/source 경로 이관을 고려해야 한다.

## 9. 남아 있는 한계와 다음 확인 항목

1. **OPV 전체 cursor 수집은 미완료다.** 저장된 보고서의 키 대기 상태를 해소하고 같은 검색·체크포인트·예산으로 재개해야 한다. 완료 여부는 조회 시점의 보고 수와 고유 ID, terminal cursor, 요청한 후속 처리 상태를 함께 확인한다.
2. **검색 후보의 관련성은 자동 인증되지 않는다.** 영어 표현·색인 누락으로 관련 문헌이 빠질 수 있고 organic-inorganic/perovskite 등 오탐 후보가 포함될 수 있다. 82,363이라는 수는 보존 당시 검색 정의의 보고 수다.
3. **원문 의미의 완전 복원은 보장하지 않는다.** 수식 위·아래첨자·표 병합·캡션 관계·읽기 순서·벡터 그림 등에 미해결 영역이 남는다. `COMPLETE`도 자동 검사 조건의 통과를 뜻한다.
4. **OCR 검증 범위는 제한적이다.** 영어의 합성 혼합 입력과 실제 논문을 래스터화한 별도 입력에서 확인했다. 다양한 실제 스캔 논문, 저해상도·기울기·한국어·복잡한 수식 OCR의 정확도는 별도 검증 대상이다.
5. **GROBID도 오류 후보를 반환한다.** 실제 제목 충돌을 확인했으므로 구조화 값과 원본 문자열을 함께 보존해야 한다. 서비스 RSS는 Docling/OCR 프로세스와 별개이며 합산 자원을 고려해야 한다.
6. **장기간 대규모 운영·Linux 실기기 검증은 미실행이다.** 재개·중복·예산·오류 경로는 테스트했지만 수만 편 PDF의 연속 처리 부하·스토리지 소진·다양한 출판사 응답을 전부 검증하지 않았다.
7. **유료 보관 콘텐츠와 PostgreSQL은 별도 범위다.** OpenAlex 보호 콘텐츠는 예산·인증·후보 처리의 모의 테스트만 수행했고 실제 유료 다운로드는 하지 않았다. PostgreSQL backend는 구현하지 않았다.
8. **업데이트 완전성에는 소스·계정 제약이 있다.** 갱신일 필터 권한이 없을 때 출판일 재검색과 ID 비교로 모든 과거 메타데이터 변경·병합·삭제를 포착할 수 없다. 전체 데이터 확보는 공식 Snapshot 경로를 검토해야 한다.

2026-10-30 문서화 단계의 완료 범위는 위 구현·검증·미완료 상태를 소스 저장소에서 읽을 수 있게 정리한 것이다. 원격 저장소 생성·commit·push의 성공 여부, 이후 전체 OPV 수집 완료, 추가 테스트 결과는 각각 확인 가능한 새 근거와 함께 후속 이력으로 기록한다.
