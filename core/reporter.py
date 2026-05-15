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


def _make_chart_html(calc_result: dict) -> str:
    """calc_result 기반 금주/전주 비교 막대 차트 (Plotly HTML 스니펫)."""
    try:
        import plotly.graph_objects as go
        import plotly.io as pio
        from core.calculator import scfi_rows

        rows = scfi_rows(calc_result)
        if not rows:
            return ""

        labels  = [r["label"]    for r in rows]
        current = [r["current"]  for r in rows]
        prev    = [r["previous"] for r in rows]
        colors  = ["#c53030" if c >= p else "#2b6cb0" for c, p in zip(current, prev)]

        fig = go.Figure()
        fig.add_bar(name="전주", x=labels, y=prev,    marker_color="#cbd5e0", opacity=0.75)
        fig.add_bar(name="금주", x=labels, y=current, marker_color=colors)
        fig.update_layout(
            barmode="group",
            height=280,
            margin=dict(l=0, r=0, t=10, b=50),
            plot_bgcolor="#ffffff",
            paper_bgcolor="#ffffff",
            yaxis=dict(tickformat=",", gridcolor="#f0f0f0"),
            xaxis=dict(tickfont=dict(size=11)),
            legend=dict(orientation="h", x=0.5, xanchor="center", y=-0.22,
                        font=dict(size=12)),
            font=dict(family="'Apple SD Gothic Neo','Noto Sans KR',Arial,sans-serif"),
        )
        return pio.to_html(fig, full_html=False, include_plotlyjs="cdn",
                           config={"displayModeBar": False})
    except Exception as e:
        logger.warning(f"보고서 차트 생성 실패: {e}")
        return ""


def render_report(
    calc_result: dict,
    news: list[dict],
    comment: str,
    week_year: int,
    week_no: int,
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
        chart_html   = _make_chart_html(calc_result),
    )
    logger.info("보고서 HTML 렌더링 완료")
    return html


def get_email_subject(week_year: int, week_no: int) -> str:
    now = datetime.now(KST)
    return f"[SCFI 주간시황] {week_year}년 {week_no}주차 ({now.strftime('%m/%d')} 기준)"
