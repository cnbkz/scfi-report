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


def _make_trend_chart_html(
    graph_data: list,
    ksg_route_data: dict,
    current_raw: dict | None = None,   # 수집된 최신 raw_data
    current_date: str = "",            # YYYY-MM-DD
) -> str:
    """KSG 기반 멀티라인 추이 차트 — 이메일/미리보기 공용 PNG base64."""
    try:
        import copy
        import plotly.graph_objects as go
        import plotly.io as pio

        # ── KSG 캐시 미반영분 보완: 최신 수집값을 마지막 포인트로 주입 ─────
        if current_raw and current_date and ksg_route_data:
            ksg_route_data = copy.deepcopy(ksg_route_data)
            for field in ["scfi_composite", "scfi_north_america_west", "scfi_europe"]:
                pts = ksg_route_data.get(field, [])
                if not pts:
                    continue
                last_date = pts[-1]["date"]
                if current_date > last_date:
                    val = current_raw.get(field)
                    if val is not None:
                        ksg_route_data[field] = pts + [{"date": current_date, "value": float(val)}]

        fig     = go.Figure()
        has_ksg = bool(ksg_route_data)

        # 종합지수 — KSG 26주 우선, surff.kr fallback
        ksg_comp = ksg_route_data.get("scfi_composite", []) if has_ksg else []
        if ksg_comp:
            _d = [p["date"]  for p in ksg_comp]
            _v = [p["value"] for p in ksg_comp]
            fig.add_trace(go.Scatter(
                x=_d, y=_v, name="SCFI 종합",
                mode="lines+markers",
                line=dict(color="#1a365d", width=3.0),
                marker=dict(size=5, color="#1a365d"),
            ))
        elif graph_data:
            _d = [d["date"] for d in graph_data]
            _v = [d["scfi_composite"] for d in graph_data]
            fig.add_trace(go.Scatter(
                x=_d, y=_v, name="SCFI 종합",
                mode="lines+markers",
                line=dict(color="#1a365d", width=3.0),
                marker=dict(size=5, color="#1a365d"),
            ))

        # 항로별 라인 — USWC / Europe
        for field, (name, color) in [
            ("scfi_north_america_west", ("북미 서안 (USWC)", "#dd6b20")),
            ("scfi_europe",             ("유럽",              "#38a169")),
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
                text=f"SCFI 지수 주간 추이 (최근 {n_weeks}주)",
                font=dict(size=14),
                x=0.02,
            ),
            height=380,
            margin=dict(l=10, r=70, t=50, b=110),
            plot_bgcolor="#ffffff", paper_bgcolor="#ffffff",
            yaxis=dict(tickformat=",", gridcolor="#f0f0f0", side="right",
                       tickfont=dict(size=12)),
            xaxis=dict(showgrid=False, tickangle=-30, tickfont=dict(size=11)),
            legend=dict(
                orientation="h",
                x=0.5, xanchor="center",
                y=-0.30, yanchor="top",
                font=dict(size=12),
                bgcolor="rgba(255,255,255,0.9)",
                bordercolor="#e2e8f0", borderwidth=1,
            ),
        )

        import base64
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


def build_trend_fig(graph_data: list, ksg_route_data: dict):
    """대시보드/보고서 탭용 Plotly Figure 반환 (st.plotly_chart 직접 렌더링용)."""
    try:
        import plotly.graph_objects as go
        fig     = go.Figure()
        has_ksg = bool(ksg_route_data)
        ksg_comp = ksg_route_data.get("scfi_composite", []) if has_ksg else []
        if ksg_comp:
            _d = [p["date"] for p in ksg_comp]
            _v = [p["value"] for p in ksg_comp]
            fig.add_trace(go.Scatter(x=_d, y=_v, name="SCFI 종합",
                mode="lines+markers+text",
                text=[f"{v:,.0f}" for v in _v], textposition="top center",
                textfont=dict(size=11, color="#1a365d"),
                line=dict(color="#1a365d", width=3.0), marker=dict(size=5)))
        elif graph_data:
            _d = [d["date"] for d in graph_data]
            _v = [d["scfi_composite"] for d in graph_data]
            fig.add_trace(go.Scatter(x=_d, y=_v, name="SCFI 종합",
                mode="lines+markers+text",
                text=[f"{v:,.0f}" for v in _v], textposition="top center",
                textfont=dict(size=11, color="#1a365d"),
                line=dict(color="#1a365d", width=3.0), marker=dict(size=5)))
        for field, (name, color) in [
            ("scfi_north_america_west", ("북미 서안 (USWC)", "#dd6b20")),
            ("scfi_europe",             ("유럽",              "#38a169")),
        ]:
            pts = ksg_route_data.get(field, []) if has_ksg else []
            if not pts:
                continue
            fig.add_trace(go.Scatter(
                x=[p["date"] for p in pts], y=[p["value"] for p in pts],
                name=name, mode="lines+markers",
                line=dict(color=color, width=1.8), marker=dict(size=5)))
        if not fig.data:
            return None
        n_weeks = len(ksg_comp) if ksg_comp else len(graph_data)
        fig.update_layout(
            title=dict(text=f"SCFI 지수 주간 추이 (최근 {n_weeks}주)", font=dict(size=14), x=0.02),
            height=400,
            margin=dict(l=10, r=80, t=50, b=100),
            plot_bgcolor="#ffffff", paper_bgcolor="#ffffff",
            yaxis=dict(tickformat=",", gridcolor="#f0f0f0", side="right", tickfont=dict(size=12)),
            xaxis=dict(showgrid=False, tickangle=-30, tickfont=dict(size=11)),
            legend=dict(orientation="h", x=0.5, xanchor="center", y=-0.25, yanchor="top", font=dict(size=12)),
        )
        return fig
    except Exception as e:
        logger.warning(f"보고서 추이 차트 Figure 생성 실패: {e}")
        return None


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
    current_raw: dict | None = None,   # 추가: 팜치용 최신 raw_data
    current_date: str = "",           # 추가: 팜치용 날짜 YYYY-MM-DD
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
        chart_html   = _make_trend_chart_html(
            graph_data or [],
            ksg_route_data or {},
            current_raw=current_raw,
            current_date=current_date,
        ),
        curr_date    = curr_date,
        prev_date    = prev_date,
    )
    logger.info("보고서 HTML 렌더링 완료")
    return html


def get_email_subject(week_year: int, week_no: int) -> str:
    now = datetime.now(KST)
    return f"[SCFI 주간시황] {week_year}년 {week_no}주차 ({now.strftime('%m/%d')} 기준)"
