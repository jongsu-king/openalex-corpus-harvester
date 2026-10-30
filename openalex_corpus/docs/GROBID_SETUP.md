# 로컬 GROBID 설치·운영

2026-10-29에 macOS ARM64에서 GROBID **0.9.1**을 소스로 빌드하고 실제 OA 논문으로 검증했다. Docker 없이 OpenJDK 21, 배포본에 포함된 ARM64 Wapiti와 pdfalto를 사용한다. header, fulltext, citation, reference-segmenter, 저자·소속, figure, table, funding 모델을 포함한 학술 문서 서비스다. DeLFT 신경망 모델은 설치하지 않았다.

공식 자료: [설치 문서](https://grobid.readthedocs.io/en/latest/Install-Grobid/), [0.9.1 릴리스](https://github.com/grobidOrg/grobid/releases/tag/0.9.1), [FAQ](https://grobid.readthedocs.io/en/latest/Frequently-asked-questions/).

## 실행

프로젝트 루트에서 실행한다. 설치된 JDK는 `/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home`이며, 다른 설치 위치는 `GROBID_JAVA_HOME`으로 지정할 수 있다. 전역 Java 심볼릭 링크나 셸 설정 변경은 필요하지 않다.

```bash
# 처음 설치하거나 빌드를 재현할 때
.venv/bin/python scripts/grobid_service.py install

# 백그라운드 서비스 시작·상태 확인·종료
.venv/bin/python scripts/grobid_service.py start
.venv/bin/python scripts/grobid_service.py status
.venv/bin/python scripts/grobid_service.py stop
```

서비스 주소는 `http://127.0.0.1:8070`, 관리 포트는 `127.0.0.1:8071`이다. 양쪽 모두 로컬 주소에만 바인딩한다. 서비스는 셸 명령 종료 후에도 유지되며 재로그인·재부팅 때 자동 시작하지 않는다. PID와 프로세스 생성 시각을 확인하여 이 도구가 실행한 프로세스만 종료한다.

프로젝트의 GROBID 요청 어댑터는 header, citation, funder의 외부 메타데이터 consolidation을 모두 `0`으로 지정한다. 원문에서 추출한 값, raw citation, 좌표와 원본 TEI를 보존하고 추출 결과는 `unverified`로 표시한다. TEI의 `application`에서 소프트웨어 식별자·버전·추출 시각도 기록한다. 별도 클라이언트가 직접 요청하는 경우 consolidation 옵션도 직접 지정해야 한다.

## 설치 파일과 자원 설정

- `data/services/grobid/grobid-0.9.1/`: 원본 소스, 모델, 빌드 결과.
- `data/services/grobid/installation.json`: 버전, JDK, 소스 체크섬.
- `data/services/grobid/runtime/grobid.local.yaml`: 생성되는 로컬 설정.
- `data/services/grobid/runtime/service.log`: 서비스 로그.
- `data/services/grobid/runtime/build.log`: 설치 빌드 로그.

Java 최대 heap은 1,536 MiB, direct memory 상한은 256 MiB다. Wapiti 네이티브 메모리는 Java heap 외부에 있으므로 실제 RSS는 첫 전문 요청 후 약 1.62 GiB(1,659 MiB)였다. 처리 엔진은 1개이고 pdfalto에는 1,024 MiB/120초 제한을 지정했다. 이는 전체 파이프라인의 합산 메모리 제한이 아니므로 Docling·OCR 프로세스와 함께 실행할 때 여유 메모리가 필요하다.

Dropwizard와 GROBID 코어가 같은 설정 파일을 읽도록 JVM 옵션 `-Dorg.grobid.config=...`도 지정한다. 실제 로그의 `Number of Engines in pool active/max: 1/1`로 적용을 확인했다.

소스 아카이브 SHA-256은 `e5fc91e2231d2ef0ae4e7f9d098cd84a702fb14fc823d2f70ae048481f15c374`이며 설치 스크립트가 이 값을 확인한다. 0.9.1의 `installDist`에서 발생한 중복 langdetect JAR 처리 오류는 원본 소스를 바꾸지 않고 로컬 Gradle init 스크립트의 `DuplicatesStrategy.EXCLUDE`로 해결했다. 이 방식은 배포본의 `distZip` 설정과 같다. 빌드는 최대 2개 작업자, Gradle heap 1 GiB로 제한한다.

## 실제 연동 검증

```bash
OPENALEX_RUN_GROBID=1 .venv/bin/pytest -q tests/test_grobid_integration.py
```

다른 보관 논문은 `GROBID_TEST_PDF`로 지정한다. 이 테스트는 가동 상태, 실제 전문 요청, TEI 파일, 구조화 참고문헌, 저자, raw citation, 좌표, 소프트웨어 버전 및 원본 PDF 체크섬 보존을 확인한다. 검증 논문은 참고문헌을 포함해야 한다.

보관된 `W4402950921`(Docling Technical Report)으로 검증했다. TEI는 약 103 KB, 본문·구조 항목은 108개, 구조화 참고문헌은 16개였다. 16개 모두 raw citation과 좌표가 있었다. 첫 요청은 약 14초, 재시작 후 연동 테스트는 **1 passed**였다. 증거는 `data/validation/grobid/integration_evidence.json`, `W4402950921.tei.xml`, `W4402950921.tei.parsed.json`에 보관한다.

연동 성공과 추출 정확도는 구분한다. 이 PDF에서 GROBID는 문서 안에 삽입된 DocLayNet 예시의 제목을 헤더 제목으로 선택했다. OpenAlex의 `Docling Technical Report`를 이 값으로 덮어쓰면 안 되며 제목 불일치를 검토 대상으로 유지한다. 서비스 자체도 일부 figure/table 구조가 불완전하다고 기록했다. TEI만으로 수식 위·아래첨자의 완전성, 모든 표의 셀 정확도나 메타데이터 정확성을 보장하지 않는다.
