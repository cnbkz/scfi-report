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
주간 SCFI 컨테이너 운임 데이터와 제공된 뉴스를 바탕으로 시황 분석 코멘트를 작성합니다.

[출력 형식 — 아래 예시와 동일한 레이아웃·포맷 엄수]

{날짜} SCFI는 {종합지수}pt로 집계 ({변동pt}, {변동%})
- 북미 서안 {값}pt({변동pt}, {변동%})
  북미 동안 {값}pt({변동pt}, {변동%})
  유럽 {값}({변동pt}, {변동%})
  호주/오세아니아 ({변동pt}, {변동%})
- {주요 원인 1 — 공급·수요·할증료·지정학 중 가장 중요한 요인 2~3가지, 2~3문장}
{원인 2가 있으면 줄바꿈 없이 연속 서술}

- {주요 항구별 운임 (해당 주 실제 데이터 있으면 기재, 없으면 생략)}
  {도시}=${현물_20ft}:{현물_40ft}:{계약_40ft}
  ...

[날짜 표기] MM/DD 형식 (예: 05/15)

[필수 작성 규칙]
- 첫 줄: 반드시 "MM/DD SCFI는 Npt로 집계 (+N, +N.N%)" 형식
- 항로별 줄: "- 북미 서안 Npt(+N, +N.N%)" 형식, 들여쓰기 유지
- 원인 분석: 뉴스에서 확인된 구체적 사실(선사명, 할증료 종류, 이벤트) 반영
  * 수요: 노동절·연휴 이후 중국發 수출 급등, 밀어내기 수출, 성수기 물량, GRI·PSS 부과
  * 공급: 블랭크세일링(임시결항), 선복 조절, MSC·Maersk·CMA CGM·HMM 전략
  * 지정학: 홍해 우회, 미-중 관세, 전쟁·분쟁 지속
  * 부킹 클로징: "이미 클로징된 상태" / "빠르게 클로징 될 것으로 예상"
- 항구 운임: LA, Savannah, Hamburg, Sydney 등 주요 항구 운임 (데이터 없으면 생략)
- 반말·합쇼체 혼용 가능, 마지막 분석 문장은 "~것으로 분석" / "~것으로 예상" 어미
- 코멘트만 출력, 제목·머리말 없음, 근거 없는 수치 추측 금지

[실제 작성 예시 — 이 포맷 그대로 따를 것]
05/15 SCFI는 2,141pt로 집계 (+229, +12.0%)
- 북미 서안 3,118pt(+292, +10.3%)
  북미 동안 4,224pt(+412, +10.8%)
  유럽 1,816(+220, +13.8%)
  호주/오세아니아 (+111, +9.2%)
- 노동절 이후 중국發 수출수요가 급등하며 전반적인 운임 상승세(주요 지역 +GRI 및 PSS반영), 5월 부킹은 대부분 지역 이미 클로징된 상태.
6월도 수출수요 급등 지속시 월초 6월 부킹도 빠르게 클로징 될 것으로 예상. 전쟁이 장기화 되며 석유 재고 부족이 장기화될 것이 명확해지며, 운임상승세에 영향을 준것으로 분석.

- LA=$1,900:1,250:3,098
- Savannah=$3,160:2,250:3,550
- (독) Hamburg=$1,540:1,800:2,800
- (호) Sydney(20')=$780:700:1,700\
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
