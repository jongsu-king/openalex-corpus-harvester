#!/usr/bin/env python3
"""Rebuild the repository's editable SVG overview using only the standard library."""

from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "diagrams" / "pipeline-overview.svg"


def render() -> str:
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="1000" '
        'viewBox="0 0 1600 1000" role="img" aria-labelledby="title desc">',
        '<title id="title">OpenAlex Corpus Harvester — 원본 보존과 검증 흐름</title>',
        '<desc id="desc">검색 및 예산 제어, 원본 보존, 다중 파서 추출, 검증 및 저장의 네 단계. '
        '모든 단계에서 출처와 불변 이력을 남기고 중단된 작업은 체크포인트에서 재개합니다. '
        'COMPLETE는 자동 검사 통과이며 원문 전체의 의미적 복원을 보장하지 않습니다.</desc>',
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        '<path d="M 0 0 L 10 5 L 0 10 z" fill="#60758a"/></marker></defs>',
        '<rect width="1600" height="1000" rx="24" fill="#f3f6fa"/>',
        '<g font-family="Apple SD Gothic Neo, Noto Sans KR, Malgun Gothic, sans-serif">',
    ]

    def text(x, y, value, size=22, color="#34475c", weight=400):
        parts.append(
            f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" '
            f'font-weight="{weight}">{escape(value)}</text>'
        )

    def rect(x, y, width, height, fill, stroke="none", radius=20):
        parts.append(
            f'<rect x="{x}" y="{y}" width="{width}" height="{height}" '
            f'rx="{radius}" fill="{fill}" stroke="{stroke}"/>'
        )

    def arrow(x1, y1, x2, y2):
        parts.append(
            f'<path d="M{x1} {y1} L{x2} {y2}" fill="none" stroke="#60758a" '
            'stroke-width="3" marker-end="url(#arrow)"/>'
        )

    text(60, 65, "OPENALEX CORPUS HARVESTER", 19, "#25735e", 700)
    text(60, 122, "원본을 보존하고, 결과의 근거를 추적한다", 40, "#142d43", 700)
    text(60, 161, "검색 → 수집 → 추출 → 검증   ·   추측으로 빈칸을 채우지 않는 과학 문헌 파이프라인", 23)

    cards = [
        (60, "01", "목록 수집", "#2064a5", "#e9f2fc", [
            "OpenAlex / SearchSpec", "범위·필터·검색식을 고정", "전송 전에 예산을 예약",
            "원본 응답·cursor·offset 저장", "Work ID로 중복 처리 방지",
        ], "인증·한도 도달 시 일시정지"),
        (440, "02", "원본 보존", "#21755d", "#e9f5ef", [
            "JSON 전체 필드 + SHA-256", "첫 정상 original.pdf 보존", "다른 PDF는 버전으로 분리",
            ".part 검증 후 원자 이동", "출처·시각·license를 기록",
        ], "원본과 파생 결과를 분리"),
        (820, "03", "독립 추출", "#886221", "#fff4df", [
            "Docling — 문서 구조 후보", "PyMuPDF — 텍스트·좌표", "pdfplumber — 표 교차 검증",
            "GROBID — 학술 구조·TEI", "OCR — 필요한 스캔 페이지",
        ], "원시 후보와 불일치를 함께 보존"),
        (1200, "04", "검증 · 저장", "#755294", "#f2ebf8", [
            "전체 페이지·파일 해시 확인", "누락·불일치·결측 이유 기록", "SQLite + 불변 extraction run",
            "Markdown / JSON / 표·그림", "검토가 남으면 PARTIAL 유지",
        ], "자동 검사와 의미적 정확성을 구분"),
    ]
    for x, number, title, color, pale, lines, note in cards:
        rect(x, 215, 340, 355, "#ffffff", "#dce4ed")
        rect(x + 20, 235, 54, 40, pale, radius=10)
        text(x + 32, 263, number, 23, color, 700)
        text(x + 20, 318, title, 29, "#142d43", 700)
        for i, line in enumerate(lines):
            text(x + 20, 361 + 33 * i, line, 21)
        rect(x + 14, 523, 312, 32, pale, radius=8)
        text(x + 23, 545, note, 17, color, 600)
    for x in (406, 786, 1166):
        arrow(x, 396, x + 26, 396)

    rect(60, 610, 1480, 94, "#153c52")
    text(85, 648, "모든 단계의 공통 원칙", 23, "#a9e2d3", 700)
    text(85, 683, "source · missing_reason · retrieval_status · validation_status  /  원본 이력 유지  /  실패를 숨기지 않음", 24, "#ffffff")
    for x in (230, 610, 990, 1370):
        arrow(x, 576, x, 603)

    panels = [
        (60, "자동 검사 통과", "#21755d", [
            "COMPLETE / COMPLETE_WITH_WARNINGS", "정의된 페이지·파일·품질 검사 결과", "원문 의미 전체의 복원 인증은 아님",
        ]),
        (565, "검토할 근거가 남음", "#886221", [
            "PARTIAL / FAILED / PDF_UNAVAILABLE", "수식·표·OCR·다운로드 원인을 분리", "메타데이터만 수집한 문헌도 구분",
        ]),
        (1070, "중단 이후에도 재개", "#2064a5", [
            "cursor + offset / bounded retry", "파일 승격과 DB commit 사이를 복구", "이전 정상 추출을 실패로 덮어쓰지 않음",
        ]),
    ]
    for x, title, color, lines in panels:
        rect(x, 747, 470, 172, "#ffffff", "#dce4ed")
        text(x + 22, 786, title, 25, color, 700)
        for i, line in enumerate(lines):
            text(x + 22, 824 + 31 * i, line, 20)
    text(60, 966, "구현·예외·상태 전이의 상세 근거: docs/ARCHITECTURE.md  ·  그림 재생성: python scripts/render_architecture.py", 21)
    parts.extend(["</g>", "</svg>"])
    return "\n".join(parts) + "\n"


if __name__ == "__main__":
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(render(), encoding="utf-8")
    print(OUTPUT.relative_to(ROOT))
