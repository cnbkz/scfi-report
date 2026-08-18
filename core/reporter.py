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


def _build_unified_figure(
    graph_data: list,
    ksg_route_data: dict,
    current_raw: dict | None = None,
    current_date: str = "",
    weeks: int = 27,
):
    """5개 노선의 SCFI 주간 시계열을 통합하여 Plotly Figure 생성."""
    import copy
    import plotly.graph_objects as go
    from core.kobc import get_kobc_route_history

    kobc_hist = get_kobc_route_history()

    # 날짜별 데이터 딕셔너리 구성
    date_map = {}

    # 1. KOBC 리포트 데이터 (USEC, USWC, Europe, S.Asia, Composite 37주)
    for row in kobc_hist:
        d = row["date"]
        date_map[d] = {
            "scfi_composite":          row.get("scfi_composite"),
            "scfi_north_america_east": row.get("scfi_north_america_east"),
            "scfi_north_america_west": row.get("scfi_north_america_west"),
            "scfi_europe":             row.get("scfi_europe"),
            "scfi_southeast_asia":     row.get("scfi_southeast_asia"),
        }

    # 2. KSG 공개 데이터 보완/갱신
    if ksg_route_data:
        for field in ["scfi_composite", "scfi_north_america_west", "scfi_europe"]:
            pts = ksg_route_data.get(field, [])
            for p in pts:
                d = p["date"]
                if d not in date_map:
                    date_map[d] = {}
                date_map[d][field] = p["value"]

    # 3. surff.kr graph_data (종합지수) 보완
    if graph_data:
        for item in graph_data:
            d = item.get("date")
            val = item.get("scfi_composite")
            if d and val:
                if d not in date_map:
                    date_map[d] = {}
                date_map[d]["scfi_composite"] = val

    # 4. 당일 최신 수집값 (current_raw) 보완
    if current_raw and current_date:
        if current_date not in date_map:
            date_map[current_date] = {}
        for f in ["scfi_composite", "scfi_north_america_east", "scfi_north_america_west", "scfi_europe", "scfi_southeast_asia"]:
            v = current_raw.get(f)
            if v is not None:
                date_map[current_date][f] = float(v)

    # 날짜 정렬 및 최근 N주 필터링
    sorted_dates = sorted(date_map.keys())
    if len(sorted_dates) > weeks:
        recent_dates = sorted_dates[-weeks:]
    else:
        recent_dates = sorted_dates

    if not recent_dates:
        return None, 0

    fig = go.Figure()

    # 각 지표별 유효 데이터 추출
    trace_data = []
    for field, name, color, symbol, mode_type in [
        ("scfi_composite",          "SCFI 종합",     "#1a365d", "circle",      "area"),
        ("scfi_north_america_east", "USEC (FEU)",   "#7cb34a", "triangle-up",  "line"),
        ("scfi_north_america_west", "USWC (FEU)",   "#e53e3e", "square",       "line"),
        ("scfi_europe",             "Europe (TEU)", "#8e24aa", "circle",       "line"),
        ("scfi_southeast_asia",     "S. Asia (TEU)","#0288d1", "diamond",      "line"),
    ]:
        pts_x = []
        pts_y = []
        for d in recent_dates:
            v = date_map[d].get(field)
            if v is not None:
                pts_x.append(d)
                pts_y.append(float(v))

        if pts_x and pts_y:
            trace_data.append({
                "field": field,
                "name": name,
                "color": color,
                "symbol": symbol,
                "mode_type": mode_type,
                "x": pts_x,
                "y": pts_y,
                "first_x": pts_x[0],
                "first_y": pts_y[0],
                "last_x": pts_x[-1],
                "last_y": pts_y[-1],
            })

    if not trace_data:
        return None, 0

    # ── 스마트 어노테이션 수직 오프셋 (텍스트 겹침 방지) ─────────────────────
    def compute_smart_shifts(items, is_left=True):
        """Y값 순서로 정렬하여 수직 오프셋(yshift)을 스마트 배치."""
        sorted_items = sorted(items, key=lambda it: it["y"], reverse=True)
        n = len(sorted_items)
        shifts = {}

        for i, item in enumerate(sorted_items):
            field = item["field"]
            # 기본 offset
            shift = 0
            if i > 0:
                prev = sorted_items[i-1]
                diff = prev["y"] - item["y"]
                # 지수 차이가 300 미만으로 가까우면 분산
                if diff < 150:
                    shift = shifts[prev["field"]] - 16
                elif diff < 300:
                    shift = shifts[prev["field"]] - 10
                else:
                    shift = 0
            shifts[field] = shift

        # 오프셋 평균이 0에 가깝도록 조정
        avg_shift = sum(shifts.values()) / n if n > 0 else 0
        for f in shifts:
            shifts[f] = round(shifts[f] - avg_shift)
        return shifts

    left_items  = [{"field": t["field"], "y": t["first_y"]} for t in trace_data]
    right_items = [{"field": t["field"], "y": t["last_y"]}  for t in trace_data]

    left_shifts  = compute_smart_shifts(left_items,  is_left=True)
    right_shifts = compute_smart_shifts(right_items, is_left=False)

    # ── Plotly Trace 추가 ──────────────────────────────────────────────────
    for t in trace_data:
        field = t["field"]
        if t["mode_type"] == "area":
            fig.add_trace(go.Scatter(
                x=t["x"], y=t["y"],
                name=t["name"],
                mode="lines",
                fill="tozeroy",
                fillcolor="rgba(200,200,200,0.35)",
                line=dict(color=t["color"], width=2.5),
                hovertemplate="%{x}<br>" + t["name"] + ": %{y:,.0f}<extra></extra>",
                connectgaps=True,
                cliponaxis=False,
            ))
        else:
            fig.add_trace(go.Scatter(
                x=t["x"], y=t["y"],
                name=t["name"],
                mode="lines+markers",
                line=dict(color=t["color"], width=2.2),
                marker=dict(size=7, color=t["color"], symbol=t["symbol"]),
                hovertemplate="%{x}<br>" + t["name"] + ": %{y:,.0f}<extra></extra>",
                connectgaps=True,
                cliponaxis=False,
            ))

        # 좌측 시작점 어노테이션
        l_shift = left_shifts.get(field, 0)
        fig.add_annotation(
            x=t["first_x"], y=t["first_y"],
            text=f"{t['first_y']:,.0f}",
            showarrow=False,
            xanchor="right", yanchor="middle",
            xshift=-8, yshift=l_shift,
            font=dict(size=12, color=t["color"]),
        )

        # 우측 끝점 어노테이션
        r_shift = right_shifts.get(field, 0)
        fig.add_annotation(
            x=t["last_x"], y=t["last_y"],
            text=f"{t['last_y']:,.0f}",
            showarrow=False,
            xanchor="left", yanchor="middle",
            xshift=8, yshift=r_shift,
            font=dict(size=12, color=t["color"]),
        )

    return fig, len(recent_dates)


def _make_trend_chart_html(
    graph_data: list,
    ksg_route_data: dict,
    current_raw: dict | None = None,
    current_date: str = "",
) -> str:
    """KSG/KOBC 기반 통합 멀티라인 추이 차트 — 이메일/미리보기 공용 PNG base64."""
    try:
        import plotly.io as pio

        fig, n_weeks = _build_unified_figure(
            graph_data, ksg_route_data, current_raw, current_date, weeks=27
        )
        if not fig or not fig.data:
            return ""

        fig.update_layout(
            title=dict(
                text=f"SCFI 지수 주간 추이 (최근 {n_weeks}주)",
                font=dict(size=14),
                x=0.02,
            ),
            height=620,
            margin=dict(l=75, r=85, t=55, b=95),
            plot_bgcolor="#ffffff", paper_bgcolor="#ffffff",
            yaxis=dict(tickformat=",", gridcolor="#f0f0f0", side="right",
                       tickfont=dict(size=12)),
            xaxis=dict(showgrid=False, tickangle=-30, tickfont=dict(size=11)),
            legend=dict(
                orientation="h",
                x=0.5, xanchor="center",
                y=-0.22, yanchor="top",
                font=dict(size=12),
                bgcolor="rgba(255,255,255,0.9)",
                bordercolor="#e2e8f0", borderwidth=1,
            ),
        )

        import base64
        png_bytes = pio.to_image(fig, format="png", width=800, height=620, scale=2)
        b64 = base64.b64encode(png_bytes).decode()
        return (
            f'<img src="data:image/png;base64,{b64}" '
            f'style="width:100%;max-width:800px;display:block;margin:0 auto;" '
            f'alt="SCFI Weekly Trend" />'
        )
    except Exception as e:
        logger.warning(f"보고서 추이 차트 생성 실패: {e}")
        return ""


def build_trend_fig(graph_data: list, ksg_route_data: dict):
    """대시보드/보고서 탭용 Plotly Figure 반환 (st.plotly_chart 직접 렌더링용)."""
    try:
        fig, n_weeks = _build_unified_figure(
            graph_data, ksg_route_data, weeks=27
        )
        if not fig or not fig.data:
            return None
        fig.update_layout(
            title=dict(text=f"SCFI 지수 주간 추이 (최근 {n_weeks}주)", font=dict(size=14), x=0.02),
            height=450,
            margin=dict(l=60, r=70, t=50, b=90),
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
