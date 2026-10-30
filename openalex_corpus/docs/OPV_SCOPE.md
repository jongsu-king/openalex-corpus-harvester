# OPV 문헌 후보 검색과 재개

검색일: **2026-10-29, Asia/Seoul**. 이 작업에서 OPV는 **organic photovoltaics / organic solar cells**, 즉 유기 태양광으로 해석했습니다. 같은 약어를 쓰는 경구용 소아마비 백신을 검색 대상으로 삼지 않습니다.

## 수집 범위

OpenAlex의 **제목·초록·색인된 키워드**를 대상으로 영어 표기와 문맥을 조합한 후보 검색입니다. 연도·언어·OA·PDF 보유 여부·문헌 유형을 제한하지 않았습니다. 따라서 논문 외에도 리뷰, 학회자료, 학위논문, 책의 장, 데이터셋 등 OpenAlex Work 객체가 포함될 수 있습니다. 원본은 유형과 함께 보존합니다.

최종 검색은 `config/opv.yaml`의 `search.query`, `scope: title_abstract_keywords`, `exact: true`에 저장했습니다. 다음 표현을 합칩니다.

- organic과 photovoltaic/photovoltaics/solar cell(s)의 조합
- polymer solar cell(s), polymer photovoltaic(s)
- plastic solar cell(s), plastic photovoltaic(s)
- OPV/OPVs/organic PV와 solar/photovoltaic(s) 문맥

이것은 **검색 조건에 맞는 후보 문헌 전체를 열거하기 위한 정의**입니다. 전 세계의 모든 관련 논문이 OpenAlex에 존재하거나 이 문자열/키워드와 일치한다는 보장은 없습니다. 다른 언어로만 표기된 문헌, 초록·키워드가 없는 기록, 특정 재료명만 쓰는 논문 등을 놓칠 수 있습니다. 반대로 organic-inorganic hybrid/perovskite 관련 문헌도 포함될 수 있습니다. 이를 관련성 검증을 마친 OPV 논문 목록이라고 표시하지 않습니다.

현재 첫페이지 합집합의 제목에서 `perovskite`가 있는 기록 3개를 검토 플래그로 표시했습니다. 키워드 플래그는 문자열이 원문 제목에 있다는 근거일 뿐, 해당 논문을 자동 제외하거나 내용 평가를 한 결과가 아닙니다. 다른 기록 역시 관련성이 검증되었다고 간주하지 않습니다.

## 실제 API 결과

| 검색 정의 | API가 보고한 전체 일치 수 | 보존한 첫페이지 |
|---|---:|---:|
| `"organic photovoltaic*"` | 16,482 | 100 |
| `"organic solar cell*"` | 25,780 | 100 |
| `"polymer solar cell*"` | 9,558 | 100 |
| `"plastic solar cell*"` | 333 | 100 |
| OPV/OPVs와 photovoltaic/solar-cell 문맥 | 6,301 | 100 |
| 핵심 표현 합집합 `core_union` | 62,586 | 100 |
| organic+PV의 넓은 합집합 `broad_union` | 82,110 | 100 |
| 최종 보완 합집합 `expanded_union` | **82,363** | 100 |

8개 첫페이지의 800개 출현에서 **고유 Work ID 608개**를 확인하고 **중복 출현 192개**를 식별했습니다. 검색별 전체 수는 서로 겹치므로 합산하지 않습니다. 단일 wildcard 검색과 명시적 단수/복수 Boolean 검색도 정확히 같은 문법이 아니므로, 개별 총합으로 최종 합집합 크기를 추정하지 않았습니다. 82,363은 최종 검색 하나에 대해 API가 직접 보고한 수입니다.

최종 검색은 100개/페이지 기준 **약 824페이지**입니다. 이는 조회 시점의 추정치이며 진행 중 데이터가 바뀔 수 있습니다. **첫페이지 조사 시에는 API 키가 없어 다음 cursor를 따라가지 않았습니다.** 위 82,363개가 이미 다운로드되거나 메타데이터로 모두 저장되었다는 뜻이 아닙니다. 실제 수집 진행률은 `data/opv/collection_report.json`의 `unique_collected_for_query`와 `reported_matches`를 확인하세요.

발견 작업에는 거부된 쿼리 진단을 포함해 **10회 요청, 보수적 예약액 $0.0100**을 사용했습니다. HTTP 400 두 건은 예약액에 남기며, 실제 응답이 청구하지 않았다고 추정해 예산을 돌려주지 않았습니다. 이전 일반 검증 요청의 $0.0001은 별도입니다. OpenAlex 보관 PDF/TEI는 내려받지 않았습니다.

## 실제 API와 문서 차이

공식 문서는 scoped exact 검색, Boolean, 후행 wildcard를 설명합니다. 실제 API는 단독 `"organic photovoltaic*"` 검색은 허용했지만, Boolean 안의 `"solar cell*"` 문구를 HTTP 400으로 거부했습니다. 응답을 `data/opv_discovery/errors/opv_context_rejection.json`에 보존하고, 합집합 쿼리에서는 **wildcard 대신 단수·복수를 직접 나열**했습니다. 작동하지 않은 쿼리를 성공으로 표시하지 않았습니다. [공식 검색 문서](https://help.openalex.org/api/searching/)

## 수집 실행

기본 출력은 기존 테스트 논문 2편과 분리된 **`data/opv`**입니다. 기본 동작은 메타데이터 목록 수집이며, 이미 보존한 최종 첫페이지는 해시·검색식 확인 후 재사용합니다. PDF와 파싱은 명시적 옵션으로만 켭니다.

```bash
# 첫페이지 저장 후 키가 없으면 PAUSED_API_KEY_REQUIRED로 종료
.venv/bin/python scripts/harvest_opv.py --all

# 프로젝트 .env에 OPENALEX_API_KEY를 저장한 뒤 명시적으로 로드
.venv/bin/python scripts/harvest_opv.py --env-file .env --all

# 한 번에 10,000개까지만 처리하고 다음 실행에서 계속
.venv/bin/python scripts/harvest_opv.py --env-file .env --max-works 10000

# 명시적으로 지정한 로컬 예산으로 실행(예: 사용자 승인한 한도)
.venv/bin/python scripts/harvest_opv.py --env-file .env --all \
  --daily-budget-usd 0.05 --daily-request-budget 1000

# 목록 확인 및 내보내기
.venv/bin/openalex-corpus --config config/opv.yaml stats
.venv/bin/openalex-corpus --config config/opv.yaml export --format jsonl
.venv/bin/openalex-corpus --config config/opv.yaml export --format parquet
```

`.env`는 셸 코드로 실행하지 않습니다. `python-dotenv`의 `dotenv_values`로 요청한 파일의 `OPENALEX_API_KEY`만 읽고 환경변수 확장도 끕니다. UTF-8 BOM을 허용하며 빈 값·대표적인 placeholder는 거부합니다. 키 값은 결과·로그에 출력하지 않습니다. `--env-file`을 생략하면 해당 파일을 자동으로 읽지 않습니다.

`--all`은 이번 실행의 문헌 수 제한만 해제합니다. **일일 금액·요청 횟수·서버 제한·인증·재시도 제한을 해제하지 않습니다.** 기본 일일 메타데이터 cap은 기존 설정과 같은 $0.05/1,000요청이고, 보관 콘텐츠 cap은 0회/$0입니다. 이 예산으로 최종 검색 전체를 하루에 끝낸다고 약속하지 않습니다. 예산 소진 시 체크포인트와 재개 시각을 기록하고 멈춥니다. `--daily-budget-usd`/`--daily-request-budget`으로 명시적으로 다른 cap을 선택할 수 있지만, 이 기능은 유료 크레딧 구매나 계정 요금제를 변경하지 않습니다.

OpenAlex는 첫 cursor 이후 무료 API 키를 요구합니다. 이 프로그램은 키가 없을 때 날짜를 쪼개거나 페이지 제한을 우회하여 같은 결과를 긁지 않습니다. [인증](https://help.openalex.org/api/authentication/) · [페이지 순회](https://help.openalex.org/api/paging/)

`--fresh`는 같은 검색을 처음부터 다시 시작하며 이전 원본은 유지합니다. 기본 재실행은 기존 cursor/페이지 offset을 이어갑니다. `COMPLETE_QUERY_MATCHES`는 해당 검색이 끝 cursor까지 도달하고 보고 수와 고유 ID 수가 일치한 경우입니다. 데이터 변경으로 수가 다르면 `COMPLETED_WITH_COUNT_MISMATCH_REVIEW`를 기록합니다. 모든 경우 `all_world_literature_complete=false`를 유지합니다.

PDF 수집은 필요할 때 `--download-pdf`, 구조 추출은 `--parse-markdown`을 추가합니다. 대량 메타데이터 목록 완성과 모든 PDF 확보는 서로 다른 작업이며, 유료/접근 제한 PDF는 우회하지 않습니다. 스크립트 전체에서 하나의 downloader를 재사용하므로 `download.max_downloads` cap이 청크마다 초기화되지 않습니다.

요청한 PDF·파싱 작업에 실패·재시도·미완료가 남으면 메타데이터 cursor가 끝나도 전체 상태는 `QUERY_COMPLETE_PROCESSING_INCOMPLETE`이고 종료코드는 2입니다. `metadata_status`와 `query_exhausted`는 목록의 완료 여부를 별도로 보존하고, `requested_processing`에는 해당 검색의 처리 상태별 수와 확보 불가 PDF 수가 기록됩니다. 완료 보고서를 재사용해도 현재 데이터베이스 통계·무결성을 다시 검사하며, 무결성 실패는 `FAILED_DATABASE_INTEGRITY`와 종료코드 2로 보고합니다.

`pdf_unavailable_works`는 실제 `PDF_NOT_AVAILABLE`만 셉니다. 손상된 PDF, 다운로드 실패, 접근 제한, 콘텐츠 한도로 중단된 건은 `download_error_counts`에 원인을 남기고 처리가 미완료인 것으로 보고합니다.

## 보존 파일과 테스트

- `config/opv_searches.yaml`: 조사한 모든 검색과 선택된 검색의 정의.
- `data/opv_discovery/pages/*.json`: 전체 원본 첫페이지와 `.receipt.json`의 요청·해시·rate 헤더.
- `data/opv_discovery/summary.json`: 검색별 총수, 첫페이지 교집합, 고유 ID 수, 예산·완료 범위.
- `data/opv_discovery/first_pages_unique_raw.jsonl`: 고유 608개 Work 원본 객체.
- `data/opv_discovery/first_pages_index.json`: DOI·제목·연도·검색별 소속·제목 문자열 검토 플래그.
- `data/opv/collection_report.json`: 현재 corpus 수집 상태. `collection_runs/`에는 실행별 보고서가 남습니다.

`tests/test_opv.py`와 `tests/test_opv_download_budget.py`는 scope/exact 전송, BOM·비실행 credential 읽기, 청크 사이 cursor·다운로드 한도 유지, 페이지 중간 재개, 키 없는 seed 후 일시정지, seed 변조 거부, 실시간 전체 수 불일치, downstream 미완료·무결성 실패 보고를 검사합니다. 실제 API 첫페이지와 모의 cursor 테스트는 서로 다른 검증 범위입니다.
