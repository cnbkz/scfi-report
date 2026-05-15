"""
SCFI 자동보고 시스템 — Streamlit 메인 대시보드
"""
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import pytz
import streamlit as st
from dotenv import load_dotenv

load_dotenv(override=True)
sys.path.insert(0, os.path.dirname(__file__))

from core.calculator  import FIELD_META, scfi_rows
from core.pipeline    import run as run_pipeline, PipelineResult
from core.storage     import load_history, is_new_data_available, save_pipeline_cache, load_pipeline_cache
from core.llm         import generate_comment
from core.reporter    import render_report, get_email_subject
from core.mailer      import send_report
from core.news        import crawl_blog_news
from core.kobc        import download_all_reports, load_kobc_context, get_kobc_route_history
from core.ksg         import fetch_route_history as ksg_fetch_routes
from core.smtp_config import load_smtp_config, save_smtp_config
from core.approver    import (send_review_email, check_reply,
                               approve_manually, reset_state, load_state)

# ── 로깅 설정 ──────────────────────────────────────────────────────────────
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    handlers=[
        logging.FileHandler("logs/run.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

KST = pytz.timezone("Asia/Seoul")
_AUTO_FLAG = Path("data/.last_auto_collect")


# ── 자동 수집 헬퍼 ─────────────────────────────────────────────────────────

def _should_auto_collect() -> bool:
    """오늘 날짜에 아직 자동 수집이 실행되지 않았으면 True."""
    today = datetime.now(KST).date().isoformat()
    if _AUTO_FLAG.exists():
        try:
            if _AUTO_FLAG.read_text().strip() == today:
                return False
        except Exception:
            pass
    return True


def _mark_auto_collected() -> None:
    _AUTO_FLAG.parent.mkdir(parents=True, exist_ok=True)
    _AUTO_FLAG.write_text(datetime.now(KST).date().isoformat())


def _report_date_labels(ran_at: str, graph_data: list | None = None) -> tuple[str, str]:
    """SCFI 실제 발행 날짜를 graph_data에서 추출해 curr/prev 레이블 반환 (M/D 형식).
    graph_data 없으면 ran_at 기반 fallback."""
    from datetime import timedelta

    if graph_data and len(graph_data) >= 2:
        try:
            dates = sorted(d["date"] for d in graph_data if d.get("date"))
            if len(dates) >= 2:
                def _fmt(d: str) -> str:
                    p = d.replace("/", "-").split("-")
                    return f"{int(p[1])}/{int(p[2])}" if len(p) == 3 else d
                return _fmt(dates[-1]), _fmt(dates[-2])
        except Exception:
            pass

    try:
        cd  = datetime.strptime(ran_at[:10], "%Y-%m-%d")
        pd_ = cd - timedelta(days=7)
        return f"{cd.month}/{cd.day}", f"{pd_.month}/{pd_.day}"
    except Exception:
        return "", ""


# ── 페이지 설정 ────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="SCFI 자동보고 시스템",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── 세션 상태 초기화 ───────────────────────────────────────────────────────
for key, default in {
    "pipeline_result":      None,
    "comment_text":         "",
    "last_ran_at":          "",
    "email_status":         "",
    "graph_data":           [],
    "blog_news":            None,
    "ksg_route_data":       None,
    "auto_collected_today": False,
    "review_status":        None,   # None | "pending" | "approved"
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

# ── 새로고침 시 디스크 캐시에서 세션 복원 ────────────────────────────────
if st.session_state.pipeline_result is None:
    _cache = load_pipeline_cache()
    if _cache:
        try:
            st.session_state.pipeline_result = PipelineResult(
                success     = True,
                raw_data    = _cache.get("raw_data",    {}),
                calc_result = _cache.get("calc_result", {}),
                news        = _cache.get("news",        []),
                graph_data  = _cache.get("graph_data",  []),
                comment     = _cache.get("comment",     ""),
                week_year   = _cache.get("week_year",   0),
                week_no     = _cache.get("week_no",     0),
                ran_at      = _cache.get("ran_at",      ""),
            )
            if not st.session_state.comment_text:
                st.session_state.comment_text = _cache.get("comment", "")
            if not st.session_state.last_ran_at:
                st.session_state.last_ran_at  = _cache.get("ran_at",  "")
            if not st.session_state.graph_data:
                st.session_state.graph_data   = _cache.get("graph_data", [])
            # 캐시가 있으면 당일 자동수집 재실행 방지
            st.session_state.auto_collected_today = True
        except Exception as _e:
            logging.warning(f"캐시 복원 실패: {_e}")

# ── 앱 시작 시 저장된 검수 상태 복원 ─────────────────────────────────────
if st.session_state.review_status is None:
    _rs = load_state()
    st.session_state.review_status = _rs.get("status") if _rs else "none"


# ── 헬퍼 함수 ──────────────────────────────────────────────────────────────

def direction_color(d: str) -> str:
    return {"▲": "#e53e3e", "▼": "#3182ce", "-": "#718096"}.get(d, "#718096")


def fmt_val(v: float) -> str:
    return f"{v:,.2f}" if v != int(v) else f"{v:,.0f}"


def fmt_change(row: dict) -> str:
    sign = "+" if row["change"] > 0 else ""
    return f"{row['direction']} {sign}{fmt_val(row['change'])}"


def fmt_pct(row: dict) -> str:
    return f"{row['change_pct']:+.2f}%"


def get_history_cached() -> pd.DataFrame:
    return load_history()


def render_index_table(rows: list[dict], title: str,
                       curr_date: str = "", prev_date: str = "") -> None:
    if title:
        st.markdown(f"**{title}**")
    curr_lbl = f"금주{'  (' + curr_date + ')' if curr_date else ''}"
    prev_lbl = f"전주{'  (' + prev_date + ')' if prev_date else ''}"
    cols = st.columns([3, 2, 2, 2, 2])
    for col, h in zip(cols, ["지표", prev_lbl, curr_lbl, "증감", "증감률"]):
        col.markdown(f"<div style='font-size:12px;color:#718096;font-weight:600;'>{h}</div>",
                     unsafe_allow_html=True)
    for row in rows:
        c = direction_color(row["direction"])
        cols = st.columns([3, 2, 2, 2, 2])
        cols[0].write(row["label"])
        cols[1].write(fmt_val(row["previous"]))                              # 전주 먼저
        cols[2].write(f"**{fmt_val(row['current'])}** {row['unit']}")       # 금주 나중
        cols[3].markdown(f"<span style='color:{c};font-weight:600;'>{fmt_change(row)}</span>",
                         unsafe_allow_html=True)
        cols[4].markdown(f"<span style='color:{c};font-weight:600;'>{fmt_pct(row)}</span>",
                         unsafe_allow_html=True)


def render_trend_chart(
    graph_data: list[dict],
    ksg_data: dict,
    kobc_hist: list[dict],
) -> None:
    """
    graph_data  : surff.kr graphData — 종합지수 24주
    ksg_data    : ksg.co.kr 공개 JSON — composite/USWC/Europe 26주
    kobc_hist   : KOBC PDF 추출 — USEC/Australia 11주+
    """
    has_graph = bool(graph_data) and len(graph_data) >= 2
    has_ksg   = bool(ksg_data)
    has_kobc  = bool(kobc_hist)

    if not has_graph and not has_ksg and not has_kobc:
        st.info("추이 차트를 표시하려면 수동 수집을 한 번 이상 실행하세요.")
        return

    fig = go.Figure()

    # ── 종합지수: KSG 26주 우선(USWC·유럽과 기간 일치), surff.kr fallback ──
    ksg_comp = ksg_data.get("scfi_composite", []) if has_ksg else []
    if ksg_comp:
        _d = [p["date"]  for p in ksg_comp]
        _v = [p["value"] for p in ksg_comp]
        _lbl = [f"{v:,.0f}" for v in _v]
        fig.add_trace(go.Scatter(
            x=_d, y=_v,
            name="SCFI 종합",
            mode="lines+markers+text",
            text=_lbl,
            textposition="top center",
            textfont=dict(size=13, color="#1a365d"),
            line=dict(color="#1a365d", width=3.5),
            marker=dict(size=6, color="#1a365d"),
            hovertemplate="%{x}<br>종합: %{y:,.0f}<extra></extra>",
        ))
    elif has_graph:
        _d = [d["date"] for d in graph_data]
        _v = [d["scfi_composite"] for d in graph_data]
        _lbl = [f"{v:,.0f}" for v in _v]
        fig.add_trace(go.Scatter(
            x=_d, y=_v,
            name="SCFI 종합",
            mode="lines+markers+text",
            text=_lbl,
            textposition="top center",
            textfont=dict(size=13, color="#1a365d"),
            line=dict(color="#1a365d", width=3.5),
            marker=dict(size=6, color="#1a365d"),
            hovertemplate="%{x}<br>종합: %{y:,.0f}<extra></extra>",
        ))

    # ── 루트별 라인 설정 ──────────────────────────────────────────────────
    route_cfg = {
        "scfi_north_america_east": ("북미 동안 (USEC)", "#e53e3e"),
        "scfi_north_america_west": ("북미 서안 (USWC)", "#dd6b20"),
        "scfi_europe":             ("유럽",              "#38a169"),
        "scfi_australia":          ("호주/오세아니아",    "#805ad5"),
    }

    # ksg 루트 (USWC, Europe) — 26주
    ksg_fields = {"scfi_north_america_west", "scfi_europe"}
    for field, (name, color) in route_cfg.items():
        if field not in ksg_fields:
            continue
        pts = ksg_data.get(field, [])
        if not pts:
            continue
        fig.add_trace(go.Scatter(
            x=[p["date"] for p in pts],
            y=[p["value"] for p in pts],
            name=name,
            mode="lines+markers",
            line=dict(color=color, width=1.8),
            marker=dict(size=6, color=color),
            hovertemplate=f"%{{x}}<br>{name}: %{{y:,.0f}}<extra></extra>",
        ))

    # KOBC 루트 (USEC, Australia) — 11주+
    kobc_fields = {"scfi_north_america_east", "scfi_australia"}
    if has_kobc:
        kobc_df = pd.DataFrame(kobc_hist)
        for field, (name, color) in route_cfg.items():
            if field not in kobc_fields:
                continue
            if field not in kobc_df.columns:
                continue
            valid = kobc_df[["date", field]].dropna().sort_values("date")
            if valid.empty:
                continue
            fig.add_trace(go.Scatter(
                x=valid["date"].tolist(),
                y=valid[field].tolist(),
                name=name,
                mode="lines+markers",
                line=dict(color=color, width=1.8),
                marker=dict(size=6, color=color),
                hovertemplate=f"%{{x}}<br>{name}: %{{y:,.0f}}<extra></extra>",
            ))

    n_weeks = len(ksg_comp) if ksg_comp else (len(graph_data) if has_graph else len(ksg_data.get("scfi_composite", [])))
    fig.update_layout(
        title=dict(
            text=f"SCFI 지수 주간 추이 (최근 {n_weeks}주)",
            font=dict(size=16),
        ),
        height=450,
        margin=dict(l=10, r=80, t=55, b=90),
        legend=dict(
            orientation="h",
            x=0.5,
            xanchor="center",
            y=-0.22,
            font=dict(size=15),
        ),
        hovermode="x unified",
        plot_bgcolor="#ffffff",
        paper_bgcolor="#ffffff",
        xaxis=dict(
            showgrid=False,
            tickangle=-30,
            tickfont=dict(size=14),
        ),
        yaxis=dict(
            gridcolor="#f0f0f0",
            side="right",
            tickformat=",",
            tickfont=dict(size=14),
        ),
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption("종합·USWC·유럽: ksg.co.kr 26주 / USEC·호주: KOBC 주간리포트 기준")


def render_blog_news(blog_news: list[dict]) -> None:
    """뉴스 섹션 렌더링."""
    if not blog_news:
        return
    st.markdown("### 📰 해상시황 최신 뉴스")
    cols = st.columns(len(blog_news))
    for col, cat in zip(cols, blog_news):
        with col:
            post_url   = cat.get("post_url", "")
            post_title = cat.get("post_title", "")
            post_date  = cat.get("post_date", "")
            st.markdown(
                f"<div style='font-size:12px;font-weight:700;color:#2b6cb0;"
                f"border-bottom:2px solid #2b6cb0;padding-bottom:4px;margin-bottom:8px;'>"
                f"{cat['category']}</div>",
                unsafe_allow_html=True,
            )
            if post_url:
                st.markdown(
                    f"[{post_title}]({post_url})"
                    f"  \n<span style='font-size:11px;color:#718096;'>{post_date}</span>",
                    unsafe_allow_html=True,
                )
            for item in cat.get("items", [])[:4]:
                st.markdown(
                    f"<div style='margin:6px 0;padding:6px 10px;background:#f7fafc;"
                    f"border-left:3px solid #4299e1;border-radius:0 4px 4px 0;font-size:12px;'>"
                    f"<a href='{item['url']}' target='_blank' style='color:#2d3748;"
                    f"text-decoration:none;font-weight:600;'>"
                    f"{item['title'][:55]}{'…' if len(item['title']) > 55 else ''}</a><br>"
                    f"<span style='color:#718096;font-size:11px;'>"
                    f"{item.get('summary','')[:80]}</span></div>",
                    unsafe_allow_html=True,
                )


# ══════════════════════════════════════════════════════════════════════════════
#  메인 UI
# ══════════════════════════════════════════════════════════════════════════════

st.markdown(
    "<h1 style='font-size:24px;margin-bottom:0;'>📊 SCFI 자동보고 시스템</h1>"
    "<p style='color:#718096;font-size:13px;margin-top:4px;'>"
    "데이터 출처: surff.kr/indices &nbsp;|&nbsp; SCM팀 백재민</p>",
    unsafe_allow_html=True,
)

# ── 폴링 여부 (자동수집 블록 전에 먼저 계산) ──────────────────────────────
_now_kst_pre = datetime.now(KST)
_is_poll_now = _now_kst_pre.weekday() == 4 and 15 <= _now_kst_pre.hour < 18

# ── 당일 첫 자동 수집 (캐시 없고 플래그 없을 때만) ────────────────────────
if not st.session_state.auto_collected_today and _should_auto_collect():
    with st.spinner("📡 당일 최신 데이터 자동 업데이트 중..."):
        try:
            result = run_pipeline(send_email=False)
            if result.success:
                st.session_state.pipeline_result      = result
                st.session_state.comment_text         = result.comment
                st.session_state.last_ran_at          = result.ran_at
                st.session_state.graph_data           = result.graph_data
                st.session_state.blog_news            = None
                st.session_state.auto_collected_today = True
                _mark_auto_collected()
                save_pipeline_cache(result)
                st.toast("✅ 자동 업데이트 완료", icon="✅")
                # 폴링 시간대이면 검수자에게 자동으로 초안 발송
                if _is_poll_now:
                    try:
                        _ac, _pc = _report_date_labels(result.ran_at, result.graph_data or [])
                        _auto_html = render_report(
                            result.calc_result, result.news, result.comment,
                            result.week_year, result.week_no,
                            graph_data=result.graph_data,
                            ksg_route_data=st.session_state.get("ksg_route_data") or {},
                            curr_date=_ac, prev_date=_pc,
                        )
                        _auto_subj = get_email_subject(result.week_year, result.week_no)
                        send_review_email(_auto_html, _auto_subj)
                        st.session_state.review_status = "pending"
                        st.toast("📋 검수 메일 자동 발송됨", icon="📋")
                    except Exception as _ae:
                        logging.warning(f"자동 검수 메일 발송 실패: {_ae}")
                # KSG 데이터 강제 갱신 (트렌드 차트 즉시 반영)
                st.session_state.ksg_route_data = None
        except Exception as e:
            logging.warning(f"자동 수집 실패: {e}")
            st.session_state.auto_collected_today = True
        else:
            if st.session_state.pipeline_result is not None:
                st.rerun()

# ── 검수 상태 pending이면 IMAP 자동 확인 (앱 로드마다) ────────────────────
if st.session_state.review_status == "pending":
    try:
        _auto_approved, _reply_cmt = check_reply()
        if _auto_approved:
            st.session_state.review_status = "approved"
            if _reply_cmt and len(_reply_cmt) > 20:
                st.session_state.comment_text = _reply_cmt
                st.toast("✅ 검수자 회신 감지 — 코멘트 자동 반영됨", icon="✅")
            else:
                st.toast("✅ 검수자 회신 확인됨", icon="✅")
    except Exception:
        pass

# ── ksg 루트 데이터 로드 (세션당 1회) ─────────────────────────────────────
if st.session_state.ksg_route_data is None:
    try:
        st.session_state.ksg_route_data = ksg_fetch_routes(weeks=26)
    except Exception as e:
        logging.warning(f"ksg 루트 데이터 로드 실패: {e}")
        st.session_state.ksg_route_data = {}

# ── 상단 액션 버튼 ──────────────────────────────────────────────────────────
col_btn1, col_btn2, col_spacer = st.columns([1.4, 1.6, 7.0])

with col_btn1:
    collect_clicked = st.button("🔄 수동 수집", use_container_width=True)
with col_btn2:
    regen_clicked = st.button("🤖 코멘트 재생성", use_container_width=True,
                              disabled=st.session_state.pipeline_result is None)

st.divider()

# ── 수동 수집 실행 ─────────────────────────────────────────────────────────
if collect_clicked:
    with st.spinner("데이터 수집 및 분석 중..."):
        result = run_pipeline(send_email=False)
    if result.success:
        st.session_state.pipeline_result = result
        st.session_state.comment_text    = result.comment
        st.session_state.last_ran_at     = result.ran_at
        st.session_state.email_status    = ""
        st.session_state.graph_data      = result.graph_data
        st.session_state.blog_news       = None
        save_pipeline_cache(result)       # 디스크 캐시 저장 → 새로고침 후 복원
        _mark_auto_collected()
        st.success(f"✅ 수집 완료 ({result.ran_at})")
    else:
        st.error(f"❌ 수집 실패: {result.error}")

# ── 코멘트 재생성 ──────────────────────────────────────────────────────────
if regen_clicked and st.session_state.pipeline_result:
    pr = st.session_state.pipeline_result
    with st.spinner("시황 분석 생성 중..."):
        new_comment = generate_comment(pr.calc_result, pr.news)
    st.session_state.comment_text = new_comment
    st.success("시황 분석 재생성 완료")

# ── 상태 카드 ─────────────────────────────────────────────────────────────
pr = st.session_state.pipeline_result
c1, c2, c3 = st.columns(3)

now_kst = datetime.now(KST)
is_poll = now_kst.weekday() == 4 and 15 <= now_kst.hour < 18

with c1:
    status_label = "🟢 폴링 활성" if is_poll else "⚪ 대기 중"
    st.metric("시스템 상태", status_label,
              delta="금 15:00~18:00 자동 실행" if not is_poll else "폴링 중")

with c2:
    last_at = st.session_state.last_ran_at or "미수집"
    st.metric("마지막 수집", last_at)

with c3:
    if pr:
        st.metric("수집 주차", f"{pr.week_year}년 {pr.week_no}주차",
                  delta=st.session_state.email_status or "미발송")
    else:
        st.metric("수집 주차", "—", delta="수집 대기")

st.divider()

# ── 탭 구성 ──────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4 = st.tabs([
    "📈 대시보드", "📰 뉴스", "📋 실행 로그", "📧 보고서 작성 및 이메일 발송",
])

# ━━ Tab 1: 대시보드 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
with tab1:

    # 1. SCFI 지수 현황 ──────────────────────────────────────────────────────
    if pr is not None:
        st.markdown("### SCFI 지수 현황")
        _curr_lbl, _prev_lbl = _report_date_labels(
            pr.ran_at, st.session_state.graph_data or []
        )
        render_index_table(scfi_rows(pr.calc_result), "",
                           curr_date=_curr_lbl, prev_date=_prev_lbl)
        st.markdown("---")
    else:
        st.info("👆 **수동 수집** 버튼을 눌러 데이터를 수집하거나, 매일 첫 로딩 시 자동 업데이트됩니다.")

    # 2. 주간 지수 추이 ──────────────────────────────────────────────────────
    st.markdown("### 주간 지수 추이")
    kobc_hist = get_kobc_route_history()
    render_trend_chart(
        st.session_state.graph_data,
        st.session_state.ksg_route_data or {},
        kobc_hist,
    )
    st.markdown("---")

    # 3. 시황 분석 ──────────────────────────────────────────────────────────
    if pr is not None:
        st.markdown("### 시황 분석")
        kobc_ctx = load_kobc_context(max_reports=1)
        if kobc_ctx:
            st.caption("📑 KOBC 주간 리포트 기반 분석 (최근 2건 반영)")
        edited = st.text_area(
            label="코멘트 편집 가능 (이메일 발송 시 반영)",
            value=st.session_state.comment_text,
            height=160,
            key="comment_editor",
        )
        if edited != st.session_state.comment_text:
            st.session_state.comment_text = edited
        st.markdown("---")

    # 4. 해상시황 최신 뉴스 ─────────────────────────────────────────────────
    if st.session_state.blog_news is None:
        with st.spinner("뉴스 로딩 중..."):
            st.session_state.blog_news = crawl_blog_news()

    render_blog_news(st.session_state.blog_news)

    if st.session_state.blog_news:
        if st.button("🔁 뉴스 새로고침", key="news_refresh"):
            st.session_state.blog_news = None
            st.rerun()

# ━━ Tab 2: 뉴스 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
with tab2:
    blog_news = st.session_state.blog_news or []
    if blog_news:
        for cat in blog_news:
            st.markdown(f"#### {cat['category']}")
            st.caption(f"최신: [{cat['post_title']}]({cat['post_url']})  |  {cat['post_date']}")
            for item in cat.get("items", []):
                with st.container(border=True):
                    st.markdown(f"**[{item['title']}]({item['url']})**")
                    if item.get("summary"):
                        st.write(item["summary"])
            st.markdown("---")
    elif pr and pr.news:
        for n in pr.news:
            with st.container(border=True):
                st.markdown(f"**[{n['title']}]({n['url']})**")
                st.caption(f"{n['source']}  |  {n['date']}")
                st.write(n["summary"])
    else:
        st.info("수집된 뉴스가 없습니다.")

# ━━ Tab 3: 실행 로그 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
with tab3:
    log_path = "logs/run.log"
    if os.path.exists(log_path):
        with open(log_path, encoding="utf-8") as f:
            lines = f.readlines()
        recent_logs = "".join(lines[-100:])
        st.code(recent_logs, language="text")
        if st.button("로그 새로고침"):
            st.rerun()
    else:
        st.info("로그 파일이 없습니다.")

    st.markdown("### 주간 수집 이력")
    df = get_history_cached()
    if df.empty:
        st.info("저장된 이력 없음")
    else:
        st.dataframe(df.tail(12)[::-1], use_container_width=True)

# ━━ Tab 4: 보고서 작성 및 이메일 발송 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
with tab4:
    import smtplib as _smtplib
    from email.mime.text import MIMEText as _MIMEText

    st.markdown(
        "<h3 style='margin-bottom:2px;'>📧 보고서 작성 및 이메일 발송</h3>"
        "<p style='color:#718096;font-size:13px;margin-top:2px;margin-bottom:0;'>"
        "수집된 데이터와 시황 분석을 바탕으로 HTML 보고서를 생성하고 이메일로 발송합니다.</p>",
        unsafe_allow_html=True,
    )
    st.markdown("")

    # ── SMTP 프리셋 정의 ──────────────────────────────────────────────────
    _PRESETS = {
        "Gmail (smtp.gmail.com)": ("smtp.gmail.com",  "587"),
        "Naver (smtp.naver.com)": ("smtp.naver.com",  "587"),
        "Daum  (smtp.daum.net)":  ("smtp.daum.net",   "465"),
        "직접 입력":               ("",                ""),
    }

    _cfg5 = load_smtp_config()

    def _detect_preset(host: str) -> str:
        for name, (h, _) in _PRESETS.items():
            if h and h == host:
                return name
        return "직접 입력"

    if "smtp_preset_sel" not in st.session_state:
        st.session_state.smtp_preset_sel = _detect_preset(_cfg5["smtp_host"])

    _pr5 = st.session_state.pipeline_result
    _rev5_status = st.session_state.review_status or "none"

    # ── 2단 레이아웃 ──────────────────────────────────────────────────────
    _col_l, _col_r = st.columns([4, 6], gap="large")

    # ════════════════════════════════════════════════════════════════════════
    #  왼쪽: 발송 설정
    # ════════════════════════════════════════════════════════════════════════
    with _col_l:
        st.markdown(
            "<div style='display:flex;align-items:center;gap:6px;margin-bottom:10px;'>"
            "<span style='color:#3b82f6;font-size:15px;'>📋</span>"
            "<b style='font-size:14px;'>발송 설정</b></div>",
            unsafe_allow_html=True,
        )

        # SMTP 프리셋 드롭다운 (form 바깥 — 선택 시 즉시 caption 갱신)
        _preset_sel = st.selectbox(
            "SMTP 서버",
            list(_PRESETS.keys()),
            key="smtp_preset_sel",
        )
        _ph, _pp = _PRESETS[_preset_sel]
        if _preset_sel != "직접 입력":
            st.caption(f"서버: {_ph} · 포트: {_pp}")
            _fh5_default, _fp5_default = _ph, _pp
        else:
            _fh5_default = _cfg5["smtp_host"]
            _fp5_default = _cfg5["smtp_port"]

        with st.form("smtp_form5", border=True):
            # 직접 입력인 경우만 서버/포트 편집 가능
            if _preset_sel == "직접 입력":
                _c1, _c2 = st.columns([3, 1])
                with _c1:
                    _fh5 = st.text_input("서버 주소", value=_fh5_default,
                                         placeholder="mail.company.com")
                with _c2:
                    _fp5 = st.text_input("포트", value=_fp5_default, placeholder="587")
            else:
                _fh5, _fp5 = _fh5_default, _fp5_default

            _fu5 = st.text_input(
                "발신 이메일",
                value=_cfg5["smtp_user"],
                placeholder="your-email@gmail.com",
            )
            _fpw5 = st.text_input(
                "앱 비밀번호",
                value=_cfg5["smtp_pass"],
                type="password",
                placeholder="앱 비밀번호 16자리",
            )
            _fr5 = st.text_input(
                "수신 이메일",
                value=_cfg5["recipients"],
                placeholder="scm-team@hansol.com, logistics@hansol.com",
                help="쉼표(,)로 여러 수신자 입력",
            )
            # 자동 생성 제목 (읽기 전용)
            _subj5 = (get_email_subject(_pr5.week_year, _pr5.week_no)
                      if _pr5 else "(데이터 수집 후 자동 입력)")
            st.text_input("제목", value=_subj5, disabled=True)

            _save5 = st.form_submit_button("설정 저장", use_container_width=True)

        # 저장 처리
        if _save5:
            if not _fu5.strip() or not _fr5.strip():
                st.error("발신 계정과 수신 이메일은 필수 항목입니다.")
            elif not _fpw5.strip() and not _cfg5["smtp_pass"]:
                st.error("앱 비밀번호를 입력하세요.")
            else:
                save_smtp_config(_fh5, _fp5, _fu5, _fpw5, _fr5,
                                 reviewer_emails=_cfg5.get("reviewer_emails", ""),
                                 imap_host=_cfg5.get("imap_host", "imap.gmail.com"),
                                 imap_port=_cfg5.get("imap_port", "993"))
                st.success("✅ 설정이 저장되었습니다.")
                _cfg5 = load_smtp_config()

        # 저장된 설정 요약 카드
        _act5 = load_smtp_config()
        if _act5["smtp_user"]:
            with st.container(border=True):
                st.markdown(
                    f"발신: [{_act5['smtp_user']}](mailto:{_act5['smtp_user']})  \n"
                    f"수신: {_act5['recipients'] or '(미설정)'}  \n"
                    f"서버: {_act5['smtp_host']}:{_act5['smtp_port']}",
                )

        # 검수자 설정 (접이식)
        with st.expander("🔍 검수자 설정 (선택)", expanded=False):
            st.caption("최종 발송 전 담당자에게 초안을 먼저 전송합니다.")
            with st.form("reviewer_form5", border=False):
                _frev5 = st.text_input(
                    "검수자 이메일 (쉼표 구분)",
                    value=_act5.get("reviewer_emails", ""),
                    placeholder="manager@hansol.com",
                )
                _ci, _cp = st.columns([3, 1])
                with _ci:
                    _fimap5 = st.text_input("IMAP 서버",
                                            value=_act5.get("imap_host", "imap.gmail.com"),
                                            help="회신 자동 감지에 사용. Gmail은 imap.gmail.com")
                with _cp:
                    _fimapp5 = st.text_input("포트", value=_act5.get("imap_port", "993"))
                _rev_save5 = st.form_submit_button("검수자 설정 저장", use_container_width=True)
            if _rev_save5:
                save_smtp_config(
                    _act5["smtp_host"], _act5["smtp_port"],
                    _act5["smtp_user"], "",
                    _act5["recipients"],
                    reviewer_emails=_frev5,
                    imap_host=_fimap5,
                    imap_port=_fimapp5,
                )
                st.success("✅ 검수자 설정 저장 완료")

        # AI 분석 섹션
        st.markdown("---")
        st.markdown(
            "<div style='display:flex;align-items:center;gap:6px;margin-bottom:8px;'>"
            "<span>🤖</span><b style='font-size:14px;'>AI 종합 분석 (선택)</b></div>",
            unsafe_allow_html=True,
        )
        if st.button("AI 분석 생성", use_container_width=True,
                     disabled=_pr5 is None, key="regen_tab5"):
            with st.spinner("시황 분석 생성 중..."):
                _nc5 = generate_comment(_pr5.calc_result, _pr5.news)
            st.session_state.comment_text = _nc5
            st.success("재생성 완료")
            st.rerun()

    # ════════════════════════════════════════════════════════════════════════
    #  오른쪽: 보고서 미리보기 및 발송
    # ════════════════════════════════════════════════════════════════════════
    with _col_r:
        st.markdown(
            "<div style='display:flex;align-items:center;gap:6px;margin-bottom:10px;'>"
            "<span style='color:#3b82f6;font-size:15px;'>📄</span>"
            "<b style='font-size:14px;'>보고서 미리보기 및 발송</b></div>",
            unsafe_allow_html=True,
        )

        if _pr5 is None:
            st.info("👆 상단 **수동 수집** 버튼으로 데이터를 먼저 수집하세요.")
        else:
            _cmt5  = st.session_state.comment_text or _pr5.comment
            _c5, _p5 = _report_date_labels(_pr5.ran_at, st.session_state.graph_data or [])
            _html5 = render_report(_pr5.calc_result, _pr5.news, _cmt5,
                                   _pr5.week_year, _pr5.week_no,
                                   graph_data=st.session_state.graph_data,
                                   ksg_route_data=st.session_state.ksg_route_data or {},
                                   curr_date=_c5, prev_date=_p5)

            with st.expander("📋 HTML 보고서 미리보기", expanded=True):
                st.components.v1.html(_html5, height=420, scrolling=True)

        # 검수 상태 배지
        if _rev5_status == "pending":
            st.markdown(
                "<div style='padding:7px 12px;background:#fffbeb;border-radius:6px;"
                "border:1px solid #fcd34d;color:#92400e;font-size:13px;"
                "text-align:center;margin:8px 0;'>🕐 검수 메일 발송됨 — 검수자 회신 대기중</div>",
                unsafe_allow_html=True,
            )
        elif _rev5_status == "approved":
            st.markdown(
                "<div style='padding:7px 12px;background:#f0fdf4;border-radius:6px;"
                "border:1px solid #86efac;color:#15803d;font-size:13px;"
                "text-align:center;margin:8px 0;'>✅ 검수 완료 — 보고서 발송 준비됨</div>",
                unsafe_allow_html=True,
            )

        # 검수 버튼 행
        _rcol1, _rcol2 = st.columns(2)
        with _rcol1:
            _review_click5 = st.button(
                "📋 검수 메일 발송", use_container_width=True,
                disabled=_pr5 is None, key="review_btn5",
                help="담당자에게 초안을 먼저 발송합니다.",
            )
        with _rcol2:
            _chkreply5 = st.button(
                "🔍 회신 확인", use_container_width=True,
                disabled=_rev5_status != "pending", key="checkreply_btn5",
                help="IMAP으로 검수자 회신 여부를 확인합니다.",
            )

        # 최종 발송 버튼 (강조)
        _send5 = st.button(
            "📧 보고서 발송",
            use_container_width=True,
            disabled=_pr5 is None,
            type="primary",
            key="send_btn5",
        )

        # Gmail 앱 비밀번호 안내
        st.markdown("")
        st.markdown(
            "<div style='background:#fffbeb;padding:14px 16px;border-radius:8px;"
            "border-left:4px solid #f59e0b;font-size:13px;'>"
            "<b>💡 Gmail 앱 비밀번호 발급 방법</b><br><br>"
            "1. <a href='https://myaccount.google.com/security' target='_blank'>"
            "Google 계정 보안</a> 접속<br>"
            "2. 2단계 인증 활성화<br>"
            "3. 검색창에 <b>앱 비밀번호</b> 검색 → 생성<br>"
            "4. 생성된 16자리 비밀번호를 위 입력란에 붙여넣기"
            "</div>",
            unsafe_allow_html=True,
        )

    # ── 탭 내 이벤트 핸들러 ──────────────────────────────────────────────
    if _review_click5 and _pr5:
        _cmt5  = st.session_state.comment_text or _pr5.comment
        _c5, _p5 = _report_date_labels(_pr5.ran_at, st.session_state.graph_data or [])
        _html5 = render_report(_pr5.calc_result, _pr5.news, _cmt5,
                               _pr5.week_year, _pr5.week_no,
                               graph_data=st.session_state.graph_data,
                               ksg_route_data=st.session_state.ksg_route_data or {},
                               curr_date=_c5, prev_date=_p5)
        with st.spinner("검수 메일 발송 중..."):
            _tok5 = send_review_email(_html5, get_email_subject(_pr5.week_year, _pr5.week_no))
        if _tok5:
            st.session_state.review_status = "pending"
            st.success(f"✅ 검수 메일 발송 완료 (토큰: {_tok5})")
            st.rerun()
        else:
            st.error("❌ 발송 실패 — 검수자 이메일·SMTP 설정을 확인하세요.")

    if _chkreply5:
        with st.spinner("IMAP 회신 확인 중..."):
            _found5, _reply_cmt5 = check_reply()
        if _found5:
            st.session_state.review_status = "approved"
            if _reply_cmt5 and len(_reply_cmt5) > 20:
                st.session_state.comment_text = _reply_cmt5
                st.success("✅ 검수자 회신 확인! 수정된 코멘트가 자동 반영되었습니다.")
            else:
                st.success("✅ 검수자 회신 확인! 보고서 발송 버튼을 눌러주세요.")
            st.rerun()
        else:
            st.info("아직 검수자의 회신이 없습니다.")

    if _send5 and _pr5:
        _cmt5   = st.session_state.comment_text or _pr5.comment
        _c5, _p5 = _report_date_labels(_pr5.ran_at, st.session_state.graph_data or [])
        _html5  = render_report(_pr5.calc_result, _pr5.news, _cmt5,
                                _pr5.week_year, _pr5.week_no,
                                graph_data=st.session_state.graph_data,
                                ksg_route_data=st.session_state.ksg_route_data or {},
                                curr_date=_c5, prev_date=_p5)
        _subj5s = get_email_subject(_pr5.week_year, _pr5.week_no)
        with st.spinner("이메일 발송 중..."):
            _ok5 = send_report(_html5, _subj5s)
        if _ok5:
            st.session_state.email_status  = f"✅ 발송 완료 ({datetime.now(KST).strftime('%H:%M')})"
            st.session_state.review_status = "none"
            reset_state()
            st.success(st.session_state.email_status)
        else:
            st.error("❌ 발송 실패 — 발송 설정을 확인하세요.")

    # 검수 상태 초기화 버튼
    _rstate5 = load_state()
    if _rstate5:
        if st.button("🗑️ 검수 상태 초기화", key="reset_review5"):
            reset_state()
            st.session_state.review_status = "none"
            st.rerun()
