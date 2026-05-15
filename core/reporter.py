"""
주간 SCFI 분석보고서 HTML 렌더링 모듈 (Jinja2)
"""
import logging
import os
from datetime import datetime

import pytz
from jinja2 import Environment, FileSystemLoader

logger = logging.getLogger(__name__)

KST          = pytz.timezone("Asia/Seoul")
TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "..", "templates")


def _make_trend_chart_html(graph_data: list, ksg_route_data: dict) -> str:
    """대시보드와 동일한 KSG 기반 멀티라인 추이 차트 — 이메일용 PNG base64 임베드."""
    try:
        import base64
        import plotly.graph_objects as go
        import plotly.io as pio

        fig     = go.Figure()
        has_ksg = bool(ksg_route_data)

        # 종합지수 — KSG 26주 우선, surff.kr fallback (숫자 라벨 없음)
        ksg_comp = ksg_route_data.get("scfi_composite", []) if has_ksg else []
        if ksg_comp:
            _d = [p["date"]  for p in ksg_comp]
            _v = [p["value"] for p in ksg_comp]
            fig.add_trace(go.Scatter(
                x=_d, y=_v, name="SCFI Composite",
                mode="lines+markers",
                line=dict(color="#1a365d", width=3.0),
                marker=dict(size=5, color="#1a365d"),
            ))
        elif graph_data:
            _d = [d["date"] for d in graph_data]
            _v = [d["scfi_composite"] for d in graph_data]
            fig.add_trace(go.Scatter(
                x=_d, y=_v, name="SCFI Composite",
                mode="lines+markers",
                line=dict(color="#1a365d", width=3.0),
                marker=dict(size=5, color="#1a365d"),
            ))

        # 항로별 라인 — USWC / Europe (영문 약칭 사용, 범례 렌더링 안정화)
        for field, (name, color) in [
            ("scfi_north_america_west", ("USWC",   "#dd6b20")),
            ("scfi_europe",             ("Europe", "#38a169")),
        ]:
            pts = ksg_route_data.get(field, []) if has_ksg else []
            if not pts:
                continue
            fig.add_trace(go.Scatter(
                x=[p["date"] for p in pts], y=[p["value"] for p in pts],
                name=name, mode="lines+markers",
                line=dict(color=color, width=1.8),
                marker=dict(size=5, color=color),
            ))

        if not fig.data:
            return ""

        n_weeks = len(ksg_comp) if ksg_comp else len(graph_data)
        fig.update_layout(
            title=dict(
                text=f"SCFI지수 주간 추이  (최근 {n_weeks}주)",
                font=dict(size=14),
                x=0.02,
            ),
            height=420,
            margin=dict(l=10, r=70, t=50, b=110),
            plot_bgcolor="#ffffff", paper_bgcolor="#ffffff",
            yaxis=dict(tickformat=",", gridcolor="#f0f0f0", side="right",
                       tickfont=dict(size=12)),
            xaxis=dict(showgrid=False, tickangle=-30, tickfont=dict(size=11)),
            legend=dict(
                orientation="h",
                x=0.5, xanchor="center",
                y=-0.28, yanchor="top",
                font=dict(size=12),
                bgcolor="rgba(255,255,255,0.9)",
                bordercolor="#e2e8f0", borderwidth=1,
            ),
        )

        # 이메일 호환 정적 PNG (JavaScript 차단 우회)
        png_bytes = pio.to_image(fig, format="png", width=680, height=380, scale=2)
        b64 = base64.b64encode(png_bytes).decode()
        return (
            f'<img src="data:image/png;base64,{b64}" '
            f'style="width:100%;max-width:680px;display:block;margin:0 auto;" '
            f'alt="SCFI Weekly Trend" />'
        )
    except Exception as e:
        logger.warning(f"보고서 추이 차트 생성 실패: {e}")
        return ""


def render_report(
    calc_result: dict,
    news: list[dict],
    comment: str,
    week_year: int,
    week_no: int,
    graph_data: list | None = None,
    ksg_route_data: dict | None = None,
    curr_date: str = "",
    prev_date: str = "",
) -> str:
    env      = Environment(loader=FileSystemLoader(TEMPLATE_DIR), autoescape=True)
    template = env.get_template("report.html")
    now      = datetime.now(KST)

    from core.calculator import scfi_rows

    html = template.render(
        week_year    = week_year,
        week_no      = week_no,
        generated_at = now.strftime("%Y-%m-%d %H:%M"),
        scfi_rows    = scfi_rows(calc_result),
        news         = news,
        comment      = comment,
        chart_html   = _make_trend_chart_html(graph_data or [], ksg_route_data or {}),
        curr_date    = curr_date,
        prev_date    = prev_date,
    )
    logger.info("보고서 HTML 렌더링 완료")
    return html


def get_email_subject(week_year: int, week_no: int) -> str:
    now = datetime.now(KST)
    return f"[SCFI 주간시황] {week_year}년 {week_no}주차 ({now.strftime('%m/%d')} 기준)"
