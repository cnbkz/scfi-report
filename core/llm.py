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
당신은 해운 SCM 전문가입니다. 주간 SCFI 컨테이너 운임 데이터를 분석하여 아래 형식으로 정확히 5줄의 시황 분석 코멘트를 작성합니다.

[출력 형식 — 5줄 고정, 번호·기호·제목 없이 줄글로]
1줄: 금주 SCFI 종합지수 수치, 전주 대비 변동(±pt, ±%), 전반적 시황 기조 한 문장
2줄: 북미 동안(USEC) — "N,NNN$/FEU로 전주 대비 X% 상승/하락하였으며, [선복 공급·수요·할증료·항차 결항 등 구체적 이유]" 형식
3줄: 북미 서안(USWC) — 같은 형식
4줄: 유럽 — 같은 형식, 홍해·수에즈 우회 영향 포함 가능
5줄: 호주/오세아니아 — 같은 형식

[작성 규칙]
- 반드시 합쇼체(습니다/입니다/하였습니다)로 작성할 것 — "이어졌다", "하락하였다" 등 반말·평서체 사용 금지
- 각 지역 줄은 반드시 수치(%변동) + 원인 분석을 함께 포함
- 원인 예시: 선복 공급 부족, 항차 결항(블랭크 세일링), 선사 GRI/PSS/EBS/EFS 할증료 부과,
  수출입 수요 변화, 홍해 우회 항로, 환적항 혼잡, 계절적 성수기/비수기 수요 등
- 방향이 같은 주의 NCFI 분석(아래 참고 예시)과 같은 이유는 동일하게 사용 가능
- 근거 없는 수치는 추측 금지; 방향 표현("강세", "약세")으로 대체
- 코멘트만 출력, 제목·머리말·형식 표기 없음

[합쇼체 문장 예시 — 반드시 이 문체로 작성]
(북미 운임 상승 주) 미동안 항로는 X,XXX$/FEU로 전주 대비 X.X% 상승하였으며, 전체 선복 공급이 부족한 가운데 일부 선사들의 추가 할증료 부과로 운임 상승세가 이어졌습니다.
(유럽 운임 상승 주) 유럽 항로는 X,XXX$/TEU로 전주 대비 X.X% 상승하였으며, 선사들이 선복 공급을 지속적으로 조절하면서 선복 부족이 이어짐에 따라 운임이 상승하였습니다.
(운임 하락 주) 유럽 항로는 X,XXX$/TEU로 전주 대비 X.X% 하락하였으며, 미국의 관세 부과 우려로 수출 물량이 감소하고 선복 공급 여력이 증가하면서 운임이 하락하였습니다.\
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
        lines.append("\n[금주 해상시황 뉴스 — 항로별 운임 변동 원인 참고]")
        for i, n in enumerate(news[:6], 1):
            lines.append(f"  {i}. {n['title']} — {n['summary'][:130]}")

    lines.append(
        "\n위 지수 데이터와 뉴스를 바탕으로 5줄 시황 분석을 작성하세요.\n"
        "각 지역(USEC·USWC·유럽·호주) 줄은 반드시 수치(% 변동)와 구체적 이유를 함께 포함하세요."
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
