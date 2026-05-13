import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime, timedelta

st.set_page_config(
    page_title="국내 주식 대시보드",
    page_icon="📈",
    layout="wide",
)

STOCKS = {
    "삼성전자": "005930.KS",
    "SK하이닉스": "000660.KS",
    "LG에너지솔루션": "373220.KS",
    "삼성바이오로직스": "207940.KS",
    "현대차": "005380.KS",
    "NAVER": "035420.KS",
    "카카오": "035720.KS",
    "POSCO홀딩스": "005490.KS",
    "셀트리온": "068270.KS",
    "KB금융": "105560.KS",
}

PERIOD_MAP = {
    "1개월": "1mo",
    "3개월": "3mo",
    "6개월": "6mo",
    "1년": "1y",
    "2년": "2y",
}


@st.cache_data(ttl=300)
def load_stock_data(ticker: str, period: str) -> pd.DataFrame:
    df = yf.download(ticker, period=period, auto_adjust=True, progress=False)
    df.columns = df.columns.get_level_values(0) if isinstance(df.columns, pd.MultiIndex) else df.columns
    return df


@st.cache_data(ttl=300)
def load_current_info(ticker: str) -> dict:
    info = yf.Ticker(ticker).fast_info
    return {
        "last_price": getattr(info, "last_price", None),
        "previous_close": getattr(info, "previous_close", None),
        "market_cap": getattr(info, "market_cap", None),
    }


def format_krw(value: float) -> str:
    if value is None:
        return "N/A"
    if value >= 1_000_000_000_000:
        return f"{value / 1_000_000_000_000:.2f}조"
    if value >= 100_000_000:
        return f"{value / 100_000_000:.0f}억"
    return f"{value:,.0f}원"


def render_metric_card(name: str, ticker: str):
    info = load_current_info(ticker)
    price = info["last_price"]
    prev = info["previous_close"]

    if price and prev:
        change = price - prev
        pct = change / prev * 100
        delta_str = f"{change:+,.0f}원 ({pct:+.2f}%)"
        color = "normal" if change >= 0 else "inverse"
    else:
        delta_str = None
        color = "off"

    price_str = f"{price:,.0f}원" if price else "N/A"
    st.metric(label=name, value=price_str, delta=delta_str, delta_color=color)


def render_candlestick(df: pd.DataFrame, name: str) -> go.Figure:
    ma20 = df["Close"].rolling(20).mean()
    ma60 = df["Close"].rolling(60).mean()

    fig = go.Figure()
    fig.add_trace(go.Candlestick(
        x=df.index,
        open=df["Open"],
        high=df["High"],
        low=df["Low"],
        close=df["Close"],
        name=name,
        increasing_line_color="#ef5350",
        decreasing_line_color="#42a5f5",
    ))
    fig.add_trace(go.Scatter(x=df.index, y=ma20, name="MA20", line=dict(color="orange", width=1)))
    fig.add_trace(go.Scatter(x=df.index, y=ma60, name="MA60", line=dict(color="purple", width=1)))
    fig.update_layout(
        title=f"{name} 주가 차트",
        xaxis_rangeslider_visible=False,
        height=450,
        margin=dict(l=0, r=0, t=40, b=0),
    )
    return fig


def render_volume(df: pd.DataFrame) -> go.Figure:
    colors = ["#ef5350" if c >= o else "#42a5f5"
              for c, o in zip(df["Close"], df["Open"])]
    fig = go.Figure(go.Bar(x=df.index, y=df["Volume"], marker_color=colors, name="거래량"))
    fig.update_layout(
        title="거래량",
        height=200,
        margin=dict(l=0, r=0, t=40, b=0),
        showlegend=False,
    )
    return fig


def render_performance_comparison(period: str) -> go.Figure:
    returns = {}
    for name, ticker in STOCKS.items():
        df = load_stock_data(ticker, period)
        if df.empty:
            continue
        close = df["Close"].squeeze()
        ret = (close / close.iloc[0] - 1) * 100
        returns[name] = ret

    fig = go.Figure()
    for name, series in returns.items():
        fig.add_trace(go.Scatter(x=series.index, y=series, name=name, mode="lines"))
    fig.add_hline(y=0, line_dash="dash", line_color="gray")
    fig.update_layout(
        title="수익률 비교 (%)",
        height=400,
        margin=dict(l=0, r=0, t=40, b=0),
        legend=dict(orientation="h", y=-0.2),
    )
    return fig


def render_summary_table(period: str) -> pd.DataFrame:
    rows = []
    for name, ticker in STOCKS.items():
        df = load_stock_data(ticker, period)
        info = load_current_info(ticker)
        if df.empty:
            continue
        close = df["Close"].squeeze()
        price = info["last_price"] or float(close.iloc[-1])
        prev = info["previous_close"] or float(close.iloc[-2])
        change_pct = (price - prev) / prev * 100
        period_ret = (close.iloc[-1] / close.iloc[0] - 1) * 100
        high = float(df["High"].max())
        low = float(df["Low"].min())
        rows.append({
            "종목": name,
            "현재가": f"{price:,.0f}",
            "전일대비(%)": f"{change_pct:+.2f}%",
            f"기간수익률(%)": f"{float(period_ret):+.2f}%",
            "기간고가": f"{high:,.0f}",
            "기간저가": f"{low:,.0f}",
            "시가총액": format_krw(info["market_cap"]),
        })
    return pd.DataFrame(rows).set_index("종목")


# ── UI ─────────────────────────────────────────────────────────────────────────

st.title("📈 국내 주식 대시보드")
st.caption(f"데이터 출처: Yahoo Finance  |  갱신 주기: 5분  |  기준 시각: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

with st.sidebar:
    st.header("설정")
    period_label = st.selectbox("조회 기간", list(PERIOD_MAP.keys()), index=2)
    period = PERIOD_MAP[period_label]

    st.divider()
    selected_name = st.selectbox("개별 종목 선택", list(STOCKS.keys()))
    selected_ticker = STOCKS[selected_name]

    st.divider()
    if st.button("데이터 새로고침", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

# ── 현재가 카드 ────────────────────────────────────────────────────────────────
st.subheader("현재가 현황")
cols = st.columns(5)
for i, (name, ticker) in enumerate(STOCKS.items()):
    with cols[i % 5]:
        render_metric_card(name, ticker)

st.divider()

# ── 탭 구성 ────────────────────────────────────────────────────────────────────
tab1, tab2, tab3 = st.tabs(["개별 종목 분석", "종목 비교", "요약 테이블"])

with tab1:
    st.subheader(f"{selected_name} ({selected_ticker})")
    df = load_stock_data(selected_ticker, period)

    if df.empty:
        st.warning("데이터를 불러올 수 없습니다.")
    else:
        c1, c2, c3 = st.columns(3)
        close = df["Close"].squeeze()
        vol = df["Volume"].squeeze()
        c1.metric("기간 최고가", f"{float(df['High'].max()):,.0f}원")
        c2.metric("기간 최저가", f"{float(df['Low'].min()):,.0f}원")
        c3.metric("평균 거래량", f"{float(vol.mean()):,.0f}주")

        st.plotly_chart(render_candlestick(df, selected_name), use_container_width=True)
        st.plotly_chart(render_volume(df), use_container_width=True)

with tab2:
    st.subheader(f"수익률 비교 ({period_label})")
    with st.spinner("데이터 로딩 중..."):
        st.plotly_chart(render_performance_comparison(period), use_container_width=True)

    # 상관관계 히트맵
    st.subheader("종목 간 수익률 상관관계")
    close_dict = {}
    for name, ticker in STOCKS.items():
        df_tmp = load_stock_data(ticker, period)
        if not df_tmp.empty:
            close_dict[name] = df_tmp["Close"].squeeze().values[:min(len(df_tmp), 252)]

    min_len = min(len(v) for v in close_dict.values())
    close_df = pd.DataFrame({k: v[:min_len] for k, v in close_dict.items()})
    corr = close_df.pct_change().corr()

    fig_corr = px.imshow(
        corr,
        text_auto=".2f",
        color_continuous_scale="RdBu_r",
        zmin=-1, zmax=1,
        title="수익률 상관계수",
    )
    fig_corr.update_layout(height=450, margin=dict(l=0, r=0, t=40, b=0))
    st.plotly_chart(fig_corr, use_container_width=True)

with tab3:
    st.subheader(f"종목 요약 ({period_label})")
    with st.spinner("데이터 로딩 중..."):
        summary_df = render_summary_table(period)
    st.dataframe(summary_df, use_container_width=True)
