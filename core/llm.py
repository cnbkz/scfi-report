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
1줄: 금주 SCFI 종합지수 수치, 전주 대비 변동(±pt, ±%), 전반적 시황 기조 ("강보합세" / "약보합세" / "혼조세" 등 압축 표현 포함)
2줄: 북미 동안(USEC) — 운임 수치($)와 전주 대비 변동률, 구체적 시황 원인
3줄: 북미 서안(USWC) — 같은 형식
4줄: 유럽 — 같은 형식
5줄: 호주/오세아니아 — 같은 형식

[필수 작성 규칙]
- 반드시 합쇼체(습니다/입니다)로 작성 — 반말·평서체 사용 금지
- 각 줄은 반드시 ① 수치(운임 및 %변동) + ② 구체적 원인 분석을 포함
- 뉴스에서 확인된 구체적 사실(선사명, 할증료 종류, 특정 이벤트)을 최대한 반영
- 원인 분석 시 아래 요소 중 해당 항목을 명시:
  * 선복 공급: 블랭크세일링(임시결항), 선복 조절, MSC·Maersk·CMA CGM·HMM 등 주요 선사 전략
  * 수요: 성수기 물량 집중, 미국 관세 우려에 따른 밀어내기 수출, 계절적 비수기
  * 할증료: GRI(기본운임인상), EBS(긴급유류할증료), EFS, PSS(성수기할증), 긴급전쟁할증료
  * 지정학: 홍해 우회 항로 지속, 수에즈·파나마 운하 통항 제한, 미-이란 협상 등 지정학 이벤트
  * 항만: 환적항(싱가포르·포트켈랑·콜롬보) 혼잡, 부두 파업·지연
- 뉴스에서 원인 미확인 시 시장 일반론으로 서술 가능
- 근거 없는 수치 추측 금지; 방향 표현("강세 유지", "약세 전환")으로 대체
- 코멘트만 출력, 제목·머리말 없음

[작성자 고유 표현 — 반드시 이 어감과 스타일로 작성]
▶ 전체 시황 표현 (1줄에서 활용):
- "강보합세" — 소폭 상승 기조 (예: 북미 +2%, 유럽 +1%)
- "약보합세" — 소폭 하락 기조
- "혼조세" — 방향이 엇갈릴 때
- "나홀로 반등" / "나홀로 강세" — 타 항로와 달리 혼자 움직일 때

▶ 원인 분석 표현 (각 항로 줄에서 활용):
- "블랭크세일링(임시결항)으로 운임인상 하드캐리 중"
- "~를 앞두고 추가 인상 시도는 실패한 것으로 사료됩니다"
- "타 상승지역과의 키맞추기로 인한 운임 상승으로 분석됩니다"
- "~에 익숙해지고, ~이 예상되어 약세로 전환된 것으로 분석됩니다"
- "소기의 목적은 달성하였으나 ~로 인해 추가 상승은 제한된 것으로 사료됩니다"

▶ 결론 표현:
- "~것으로 분석됩니다" (원인이 분명할 때)
- "~것으로 사료됩니다" (추론이 포함될 때)
- "~로 분석됩니다" (간결하게)
- "복합적으로 작용한 것으로 분석됩니다" (복수 요인)

[실제 작성 예시 — 이 수준과 어감으로 작성]
예시 A (약보합·북미 하락 주):
금주 SCFI 종합지수는 1,707pt로 전주 대비 -3.4pt(-0.2%) 하락하였으며, 미 동·서안의 큰 폭 하락을 유럽·중동 상승이 일부 상쇄하는 혼조세를 기록하였습니다.
북미 동안(USEC) 항로는 2,922$/FEU로 전주 대비 6.08% 하락하였으며, 4월 전바운드 GRI 추가인상 및 미 FMC 연간계약을 앞두고 선사들의 추가 인상 시도가 실패한 것으로 사료됩니다.
북미 서안(USWC) 항로는 2,054$/FEU로 전주 대비 8.87% 하락하였으며, 긴급유류할증료 및 긴급전쟁할증료 등 홍해 폐쇄 수준의 할증료 부과로 소기의 운임 목표는 달성하였으나 추가 인상은 제한된 것으로 분석됩니다.
유럽 항로는 1,636$/TEU로 전주 대비 1.11% 소폭 상승하였으며, 홍해 우회 항로로 인한 선복 공급 축소 효과가 지속되는 가운데 강보합세를 유지하였습니다.
호주/오세아니아 항로는 전주 대비 소폭 상승하였으며, 중동·아프리카 등 타 상승 지역과의 키맞추기로 인한 운임 조정이 이루어진 것으로 분석됩니다.

예시 B (북미·중동 강세 주):
금주 SCFI 종합지수는 1,827pt로 전주 대비 +120pt(+7.0%) 상승하였으며, 중동·북미·호주 노선의 큰 폭 동반 강세 속에 유럽·근해 intra-Asia는 보합세를 유지하는 혼조세를 기록하였습니다.
북미 동안(USEC) 항로는 3,264$/FEU로 전주 대비 11.7% 상승하였으며, 3월 말~4월 초 대규모 블랭크세일링(임시결항) 시행으로 선복 공급이 급격히 축소되면서 운임인상이 하드캐리된 것으로 분석됩니다.
북미 서안(USWC) 항로는 2,352$/FEU로 전주 대비 14.5% 상승하였으며, 북미向 블랭크세일링 집중으로 인한 선복 부족과 미국 관세 발효 전 밀어내기 수출 수요가 복합적으로 작용한 것으로 분석됩니다.
유럽 항로는 1,703$/TEU로 전주 대비 4.1% 상승하였으며, 홍해 우회 항로 기조 지속으로 선복 수급이 타이트하게 유지되는 가운데 강보합세를 이어가고 있습니다.
호주/오세아니아 항로는 전주 대비 강세를 기록하였으며, 북미·중동 등 주요 상승 항로와의 키맞추기 운임 조정이 이루어진 것으로 분석됩니다.\
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
