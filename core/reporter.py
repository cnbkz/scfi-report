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
    """대시보드와 동일한 KSG 기반 멀티라인 추이 차트 (Plotly HTML 스니펫)."""
    try:
        import plotly.graph_objects as go
        import plotly.io as pio

        fig      = go.Figure()
        has_ksg  = bool(ksg_route_data)

        # 종합지수 — KSG 26주 우선
        ksg_comp = ksg_route_data.get("scfi_composite", []) if has_ksg else []
        if ksg_comp:
            _d  = [p["date"]  for p in ksg_comp]
            _v  = [p["value"] for p in ksg_comp]
            _lb = [f"{v:,.0f}" for v in _v]
            fig.add_trace(go.Scatter(
                x=_d, y=_v, name="SCFI 종합",
                mode="lines+markers+text", text=_lb,
                textposition="top center",
                textfont=dict(size=10, color="#1a365d"),
                line=dict(color="#1a365d", width=2.5),
                marker=dict(size=4, color="#1a365d"),
                hovertemplate="%{x}<br>종합: %{y:,.0f}<extra></extra>",
            ))
        elif graph_data:
            _d = [d["date"] for d in graph_data]
            _v = [d["scfi_composite"] for d in graph_data]
            fig.add_trace(go.Scatter(
                x=_d, y=_v, name="SCFI 종합",
                mode="lines+markers",
                line=dict(color="#1a365d", width=2.5),
                marker=dict(size=4, color="#1a365d"),
                hovertemplate="%{x}<br>종합: %{y:,.0f}<extra></extra>",
            ))

        # 항로별 라인 (USWC / Europe / USEC)
        for field, (name, color) in [
            ("scfi_north_america_west", ("북미 서안 (USWC)", "#dd6b20")),
            ("scfi_europe",             ("유럽",              "#38a169")),
            ("scfi_north_america_east", ("북미 동안 (USEC)", "#e53e3e")),
        ]:
            pts = ksg_route_data.get(field, []) if has_ksg else []
            if not pts:
                continue
            fig.add_trace(go.Scatter(
                x=[p["date"] for p in pts], y=[p["value"] for p in pts],
                name=name, mode="lines+markers",
                line=dict(color=color, width=1.6),
                marker=dict(size=3, color=color),
                hovertemplate=f"%{{x}}<br>{name}: %{{y:,.0f}}<extra></extra>",
            ))

        if not fig.data:
            return ""

        fig.update_layout(
            height=320,
            margin=dict(l=0, r=60, t=10, b=90),
            plot_bgcolor="#ffffff", paper_bgcolor="#ffffff",
            yaxis=dict(tickformat=",", gridcolor="#f0f0f0", side="right",
                       tickfont=dict(size=11)),
            xaxis=dict(showgrid=False, tickangle=-30, tickfont=dict(size=10)),
            legend=dict(orientation="h", x=0.5, xanchor="center", y=-0.30,
                        font=dict(size=11)),
            hovermode="x unified",
            font=dict(family="'Apple SD Gothic Neo','Noto Sans KR',Arial,sans-serif"),
        )
        return pio.to_html(fig, full_html=False, include_plotlyjs="cdn",
                           config={"displayModeBar": False})
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
    )
    logger.info("보고서 HTML 렌더링 완료")
    return html


def get_email_subject(week_year: int, week_no: int) -> str:
    now = datetime.now(KST)
    return f"[SCFI 주간시황] {week_year}년 {week_no}주차 ({now.strftime('%m/%d')} 기준)"
