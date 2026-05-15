"""
Anthropic Claude API 시황 분석 코멘트 생성 모듈
닝보 NCFI 주간 보고서 스타일 + KOBC 컨테이너선 섹션 기반
"""
import logging
import os

import anthropic

logger = logging.getLogger(__name__)

# ── 시스템 프롬프트 ────────────────────────────────────────────────────────────

_BASE_SYSTEM = """\
당신은 KOBC(한국해양진흥공사) 수준의 해운 시황 전문 애널리스트입니다.
주간 SCFI 컨테이너 운임 데이터와 제공된 뉴스를 바탕으로 정확히 5줄의 시황 분석을 작성합니다.

[출력 형식 — 5줄 고정, 번호·기호·제목 없이 줄글로]
1줄: 금주 SCFI 종합지수 수치, 전주 대비 변동(±pt, ±%), 전반적 시황 기조 서술
2줄: 북미 동안(USEC) — 운임 수치($)와 전주 대비 변동률, 구체적 시황 원인
3줄: 북미 서안(USWC) — 같은 형식
4줄: 유럽 — 같은 형식
5줄: 호주/오세아니아 — 같은 형식

[필수 작성 규칙]
- 반드시 합쇼체(습니다/입니다/하였습니다)로 작성 — 반말·평서체 사용 금지
- 각 줄은 반드시 ① 수치(운임 및 %변동) + ② 구체적 원인 분석을 포함
- 제공된 뉴스에서 언급된 구체적 사실(선사명, 할증료 종류·금액, 특정 이벤트)을 최대한 반영
- 원인 분석 시 아래 요소 중 뉴스에서 확인된 항목을 명시:
  * 선복 공급: 블랭크 세일링(항차 결항), 선복 조절, MSC·Maersk·CMA CGM·HMM 등 주요 선사 전략
  * 수요: 성수기 수요 집중, 미국 관세 우려에 따른 밀어내기 수출, 계절적 비수기 물량 감소
  * 할증료: GRI(기본운임인상), EBS(긴급할증료), EFS(환경연료할증), PSS(성수기할증)
  * 지정학: 홍해 우회 항로 지속, 수에즈 운하 통항 제한, 파나마 운하 혼잡
  * 항만: 싱가포르·포트켈랑·콜롬보 등 환적항 혼잡, 부두 파업·지연
- 뉴스에서 원인 미확인 시 "선복 수급 조정", "시장 수요 변화" 등 시장 일반론으로 서술
- 근거 없는 수치 추측 금지; 방향 표현("강세 유지", "약세 전환")으로 대체
- 코멘트만 출력, 제목·머리말·형식 표기 없음

[고품질 분석 예시 — 이 수준으로 작성]
금주 SCFI 종합지수는 1,954pt로 전주(1,911pt) 대비 42.81pt(+2.24%) 상승하였으며, 북미·유럽 주요 항로의 동반 강세 속에 전반적인 운임 상승 기조가 지속되고 있습니다.
북미 동안(USEC) 항로는 3,812$/FEU로 전주 대비 3.28% 상승하였으며, MSC·Maersk 등 주요 선사들의 블랭크 세일링 시행으로 선복 공급이 축소된 가운데 미국 관세 발효 전 밀어내기 수출 수요가 집중되어 운임이 상승하였습니다.
북미 서안(USWC) 항로는 2,826$/FEU로 전주 대비 3.82% 상승하였으며, LA·롱비치항 입항 대기 선박 증가로 환적 혼잡이 심화된 상황에서 선사들의 GRI 부과가 맞물려 운임 상승세가 지속되었습니다.
유럽 항로는 1,596$/TEU로 전주 대비 4.93% 상승하였으며, 홍해 우회로 인한 항해 거리 증가로 선박 회전율이 저하된 가운데 선사들이 선복 공급을 추가로 조절하며 운임이 강세를 유지하였습니다.
호주/오세아니아 항로는 1,206$/TEU로 전주 대비 3.34% 상승하였으며, 계절적 성수기 진입에 따른 수출 물량 증가와 환적항 혼잡으로 운임 상승세가 이어졌습니다.\
"""

_NINGBO_PREFIX = "\n\n[KOBC 닝보 NCFI 주간 보고서 최신 사례 — 항로별 분석 문체 참고]\n"
_KOBC_PREFIX   = "\n\n[KOBC 주간 시황 리포트 컨테이너선 섹션 — 뉴스 맥락 참고]\n"


def _build_system_prompt() -> str:
    system = _BASE_SYSTEM
    try:
        from core.kobc import load_ningbo_context, load_kobc_context
        ningbo_ctx = load_ningbo_context(max_reports=3)
        kobc_ctx   = load_kobc_context(max_reports=1)
        if ningbo_ctx:
            system += _NINGBO_PREFIX + ningbo_ctx
        if kobc_ctx:
            system += _KOBC_PREFIX + kobc_ctx
    except Exception as e:
        logger.debug(f"컨텍스트 로드 실패: {e}")
    return system


def _build_user_prompt(calc_result: dict, news: list[dict]) -> str:
    lines = ["[금주 SCFI 항로별 지수]"]
    for row in calc_result.values():
        lines.append(
            f"  • {row['label']}: {row['current']:,.0f} {row['unit']} "
            f"(전주 {row['previous']:,.0f} / "
            f"{row['direction']}{abs(row['change']):,.0f} / {row['change_pct']:+.2f}%)"
        )

    if news:
        lines.append("\n[금주 해상시황 뉴스 — 원인 분석에 적극 활용하세요]")
        for i, n in enumerate(news[:8], 1):
            title   = n.get("title", "")
            summary = n.get("summary", "")[:180]
            source  = n.get("source", "")
            lines.append(f"  {i}. [{source}] {title}")
            if summary:
                lines.append(f"     → {summary}")

    lines.append(
        "\n위 지수 데이터와 뉴스를 바탕으로 5줄 시황 분석을 작성하세요.\n"
        "뉴스에서 언급된 선사명·할증료·블랭크 세일링·지정학 이슈 등을 각 항로 분석에 반드시 반영하세요.\n"
        "단순히 '운임이 상승하였습니다'로 끝내지 말고 뉴스 근거를 포함한 심층 분석을 작성하세요."
    )
    return "\n".join(lines)


def _fallback_comment(calc_result: dict) -> str:
    comp = calc_result.get("scfi_composite", {})
    usec = calc_result.get("scfi_north_america_east", {})
    uswc = calc_result.get("scfi_north_america_west", {})
    eu   = calc_result.get("scfi_europe", {})
    aus  = calc_result.get("scfi_australia", {})

    def _line(r: dict, label: str, unit: str) -> str:
        cur  = r.get("current", 0)
        pct  = r.get("change_pct", 0)
        dire = "상승" if pct > 0 else ("하락" if pct < 0 else "보합")
        return (f"{label} 항로는 {cur:,.0f}{unit}으로 전주 대비 {abs(pct):.1f}% {dire}하였으며, "
                f"시장 수급 변화에 따른 운임 조정이 이어졌습니다.")

    return "\n".join([
        f"금주 SCFI 종합지수는 {comp.get('current',0):,.0f}pt로 전주 대비 "
        f"{comp.get('direction','-')}{abs(comp.get('change',0)):,.0f}pt({comp.get('change_pct',0):+.2f}%) 변동하였습니다.",
        _line(usec, "북미 동안(USEC)", "$/FEU"),
        _line(uswc, "북미 서안(USWC)", "$/FEU"),
        _line(eu,   "유럽",            "$/TEU"),
        _line(aus,  "호주/오세아니아", "$/TEU"),
    ])


def generate_comment(calc_result: dict, news: list[dict]) -> str:
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY 미설정 — 기본 템플릿 사용")
        return _fallback_comment(calc_result)

    try:
        client      = anthropic.Anthropic(api_key=api_key)
        system_text = _build_system_prompt()
        user_prompt = _build_user_prompt(calc_result, news)

        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=800,
            system=[{
                "type": "text",
                "text": system_text,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": user_prompt}],
        )
        comment = response.content[0].text.strip()
        logger.info(f"LLM 코멘트 생성 완료 ({len(comment)}자)")
        return comment

    except anthropic.APIStatusError as e:
        logger.warning(f"Claude API 오류 ({e.status_code}) — 기본 템플릿 사용")
    except Exception as e:
        logger.warning(f"LLM 호출 실패 — 기본 템플릿 사용: {e}")

    return _fallback_comment(calc_result)
