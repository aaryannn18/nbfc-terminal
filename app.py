"""
NBFC / Private-Credit Terminal — v1
-----------------------------------
A dark, data-dense dashboard covering NBFC/lending-sector stocks, Indian
large-caps, major US companies, two headline indices, RBI regulatory
updates, and a per-company deep-dive view (price history in multiple
timeframes and chart types, valuation ratios, and a peer-comparison
table).

Data sources (all free):
  - Yahoo Finance (via yfinance) for quotes, history and fundamentals
  - RBI's own RSS feeds for circulars/notifications and press releases

Run it:
    pip install -r requirements.txt
    streamlit run app.py

Known limits of a free-data v1 (be upfront about these with anyone you
show this to):
  - Quotes are delayed, not real-time tick data — fine for a dashboard,
    not for execution.
  - Intraday history only goes back a handful of days (a Yahoo Finance
    limit, not a bug) — that's why "1 Day" / "5 Days" only show recent
    sessions.
  - "Competitors" are just other companies in the same watchlist
    category, not a true sector-matched peer set — good enough for a
    v1, worth refining later with real sector tags if this gets traction.
"""

from datetime import datetime

import feedparser
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf
from plotly.subplots import make_subplots

try:
    from streamlit_autorefresh import st_autorefresh

    AUTOREFRESH_AVAILABLE = True
except ImportError:
    AUTOREFRESH_AVAILABLE = False

st.set_page_config(page_title="Northgate Credit", layout="wide", page_icon="📊")

# ---------------------------------------------------------------------------
# Dark terminal styling
# ---------------------------------------------------------------------------

st.markdown(
    """
    <style>
    .stApp { background-color: #0a0e14; color: #e8e8e8; }
    section[data-testid="stSidebar"] { background-color: #0d1117; }
    div[data-testid="stMetricValue"] { font-family: 'Courier New', monospace; }
    .stDataFrame { font-family: 'Courier New', monospace; }
    h1, h2, h3 { font-family: 'Courier New', monospace; letter-spacing: 0.5px; }
    .stTabs [data-baseweb="tab"] { font-family: 'Courier New', monospace; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Config — edit these dicts to match the book of whoever you're building
# this for. NSE tickers end in .NS; US tickers need no suffix. Verify
# against nseindia.com / finance.yahoo.com if a ticker ever looks wrong.
# ---------------------------------------------------------------------------

NBFC_WATCHLIST = {
    "Bajaj Finance": "BAJFINANCE.NS",
    "Cholamandalam Inv & Fin": "CHOLAFIN.NS",
    "Shriram Finance": "SHRIRAMFIN.NS",
    "M&M Financial Services": "M&MFIN.NS",
    "LIC Housing Finance": "LICHSGFIN.NS",
    "Muthoot Finance": "MUTHOOTFIN.NS",
    "PNB Housing Finance": "PNBHOUSING.NS",
    "Power Finance Corp": "PFC.NS",
    "REC Limited": "RECLTD.NS",
    "L&T Finance": "LTF.NS",
}

INDIA_LARGECAP_WATCHLIST = {
    "Reliance Industries": "RELIANCE.NS",
    "Tata Consultancy Services": "TCS.NS",
    "HDFC Bank": "HDFCBANK.NS",
    "ICICI Bank": "ICICIBANK.NS",
    "Infosys": "INFY.NS",
    "Hindustan Unilever": "HINDUNILVR.NS",
    "ITC": "ITC.NS",
    "State Bank of India": "SBIN.NS",
    "Bharti Airtel": "BHARTIARTL.NS",
    "Kotak Mahindra Bank": "KOTAKBANK.NS",
    "Larsen & Toubro": "LT.NS",
    "Asian Paints": "ASIANPAINT.NS",
    "Axis Bank": "AXISBANK.NS",
    "Maruti Suzuki": "MARUTI.NS",
    "Sun Pharma": "SUNPHARMA.NS",
    "Titan Company": "TITAN.NS",
    "UltraTech Cement": "ULTRACEMCO.NS",
    "Wipro": "WIPRO.NS",
    "Nestle India": "NESTLEIND.NS",
    "Power Grid Corp": "POWERGRID.NS",
    "NTPC": "NTPC.NS",
    "HCL Technologies": "HCLTECH.NS",
    "Tata Motors": "TATAMOTORS.NS",
    "Tata Steel": "TATASTEEL.NS",
    "Bajaj Finserv": "BAJAJFINSV.NS",
    "IndusInd Bank": "INDUSINDBK.NS",
    "JSW Steel": "JSWSTEEL.NS",
    "Grasim Industries": "GRASIM.NS",
    "Adani Enterprises": "ADANIENT.NS",
    "Coal India": "COALINDIA.NS",
}

US_WATCHLIST = {
    "Apple": "AAPL",
    "Microsoft": "MSFT",
    "Alphabet (Google)": "GOOGL",
    "Amazon": "AMZN",
    "Nvidia": "NVDA",
    "Meta Platforms": "META",
    "Tesla": "TSLA",
}

ALL_WATCHLISTS = {
    "NBFC / Lending": NBFC_WATCHLIST,
    "Indian Large-Cap": INDIA_LARGECAP_WATCHLIST,
    "US Companies": US_WATCHLIST,
}

INDEX_TICKERS = {
    "Nifty 50": "^NSEI",
    "NASDAQ Composite": "^IXIC",
}

RBI_FEEDS = {
    "Notifications / Circulars": "https://rbi.org.in/notifications_rss.xml",
    "Press Releases": "https://rbi.org.in/pressreleases_rss.xml",
}

# period, interval — matched to what Yahoo Finance actually supports for
# free. True tick-by-tick / 1-second data needs a paid market-data feed
# and isn't available here; 5-minute bars is the finest free granularity,
# and only for recent sessions.
CHART_RANGES = {
    "1 Day": ("1d", "5m"),
    "5 Days": ("5d", "15m"),
    "1 Month": ("1mo", "1d"),
    "6 Months": ("6mo", "1d"),
    "1 Year": ("1y", "1wk"),
    "5 Years": ("5y", "1wk"),
}


# ---------------------------------------------------------------------------
# Data fetchers — cached and defensive. A bad ticker, a dead feed, or a
# missing field should degrade to an empty/NA result, never crash the page.
# ---------------------------------------------------------------------------

@st.cache_data(ttl=60)
def fetch_watchlist(tickers: dict) -> pd.DataFrame:
    rows = []
    for name, symbol in tickers.items():
        price = prev_close = day_high = day_low = None
        try:
            info = yf.Ticker(symbol).info
            price = info.get("currentPrice") or info.get("regularMarketPrice")
            prev_close = info.get("previousClose")
            day_high = info.get("dayHigh")
            day_low = info.get("dayLow")
        except Exception:
            pass

        change_pct = None
        if price and prev_close:
            change_pct = (price - prev_close) / prev_close * 100

        rows.append(
            {
                "Name": name,
                "Symbol": symbol,
                "Price": round(price, 2) if price else None,
                "Change %": round(change_pct, 2) if change_pct is not None else None,
                "Day High": day_high,
                "Day Low": day_low,
            }
        )
    return pd.DataFrame(rows)


@st.cache_data(ttl=60)
def fetch_index_snapshot(ticker: str):
    try:
        info = yf.Ticker(ticker).info
        price = info.get("regularMarketPrice") or info.get("currentPrice")
        prev_close = info.get("previousClose")
        change_pct = (
            (price - prev_close) / prev_close * 100 if price and prev_close else None
        )
        return price, change_pct
    except Exception:
        return None, None


@st.cache_data(ttl=300)
def fetch_rbi_feed(url: str, limit: int = 12) -> list[dict]:
    try:
        parsed = feedparser.parse(url)
        return [
            {
                "title": entry.get("title", "Untitled"),
                "link": entry.get("link", ""),
                "published": entry.get("published", ""),
            }
            for entry in parsed.entries[:limit]
        ]
    except Exception:
        return []


@st.cache_data(ttl=120)
def fetch_history(symbol: str, period: str, interval: str) -> pd.DataFrame:
    try:
        hist = yf.Ticker(symbol).history(period=period, interval=interval)
        return hist if hist is not None else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=300)
def fetch_fundamentals(symbol: str) -> dict:
    try:
        info = yf.Ticker(symbol).info
    except Exception:
        info = {}
    return {
        "price": info.get("currentPrice") or info.get("regularMarketPrice"),
        "prev_close": info.get("previousClose"),
        "day_high": info.get("dayHigh"),
        "day_low": info.get("dayLow"),
        "fifty_two_wk_high": info.get("fiftyTwoWeekHigh"),
        "fifty_two_wk_low": info.get("fiftyTwoWeekLow"),
        "volume": info.get("volume") or info.get("regularMarketVolume"),
        "pe_ratio": info.get("trailingPE"),
        "ev_ebitda": info.get("enterpriseToEbitda"),
        "profit_margin": info.get("profitMargins"),
        "roe": info.get("returnOnEquity"),
    }


def get_peers(company_name: str) -> dict:
    """Other companies in the same watchlist category — a same-category
    stand-in for true sector peers, which free data doesn't reliably give."""
    for group in ALL_WATCHLISTS.values():
        if company_name in group:
            return {k: v for k, v in group.items() if k != company_name}
    return {}


def _fmt_pct(x):
    return f"{x * 100:.1f}%" if isinstance(x, (int, float)) else "—"


def _fmt_ratio(x):
    return f"{x:.2f}" if isinstance(x, (int, float)) else "—"


def _fmt_num(x):
    return f"{x:,.2f}" if isinstance(x, (int, float)) else "—"


# ---------------------------------------------------------------------------
# Chart builder
# ---------------------------------------------------------------------------

def render_price_chart(symbol: str, period: str, interval: str, chart_type: str):
    hist = fetch_history(symbol, period, interval)
    if hist.empty:
        st.warning(
            "No historical data for this range/ticker right now — Yahoo "
            "Finance limits how far back intraday data goes, so try a "
            "longer range."
        )
        return

    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.75, 0.25],
        vertical_spacing=0.03,
    )

    if chart_type == "Candlestick" and {"Open", "High", "Low", "Close"}.issubset(hist.columns):
        fig.add_trace(
            go.Candlestick(
                x=hist.index,
                open=hist["Open"],
                high=hist["High"],
                low=hist["Low"],
                close=hist["Close"],
                increasing_line_color="#26a69a",
                decreasing_line_color="#ef5350",
                name="Price",
            ),
            row=1,
            col=1,
        )
    else:
        fig.add_trace(
            go.Scatter(
                x=hist.index,
                y=hist["Close"],
                mode="lines",
                line=dict(color="#f0b429", width=1.5),
                name="Close",
            ),
            row=1,
            col=1,
        )

    if "Volume" in hist.columns:
        fig.add_trace(
            go.Bar(x=hist.index, y=hist["Volume"], marker_color="#4a5568", name="Volume"),
            row=2,
            col=1,
        )

    fig.update_layout(
        height=480,
        template="plotly_dark",
        paper_bgcolor="#0a0e14",
        plot_bgcolor="#0a0e14",
        showlegend=False,
        margin=dict(l=10, r=10, t=10, b=10),
        xaxis_rangeslider_visible=False,
    )
    st.plotly_chart(fig, use_container_width=True)


# ---------------------------------------------------------------------------
# Company deep-dive panel
# ---------------------------------------------------------------------------

def render_company_detail(company_name: str, symbol: str):
    f = fetch_fundamentals(symbol)

    change_pct = None
    if f["price"] and f["prev_close"]:
        change_pct = (f["price"] - f["prev_close"]) / f["prev_close"] * 100

    st.subheader(f"{company_name}  ·  {symbol}")

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Price", _fmt_num(f["price"]), f"{change_pct:.2f}%" if change_pct is not None else None)
    m2.metric("Day Range", f"{_fmt_num(f['day_low'])} – {_fmt_num(f['day_high'])}")
    m3.metric("52-Wk Range", f"{_fmt_num(f['fifty_two_wk_low'])} – {_fmt_num(f['fifty_two_wk_high'])}")
    m4.metric("Volume", f"{f['volume']:,}" if isinstance(f["volume"], (int, float)) else "—")
    m5.metric("P/E Ratio", _fmt_ratio(f["pe_ratio"]))

    c1, c2 = st.columns([3, 1])
    with c1:
        range_choice = st.radio(
            "Range", list(CHART_RANGES.keys()), horizontal=True, index=3, key=f"range_{symbol}"
        )
    with c2:
        chart_type = st.radio(
            "Chart", ["Candlestick", "Line"], horizontal=True, key=f"type_{symbol}"
        )

    period, interval = CHART_RANGES[range_choice]
    render_price_chart(symbol, period, interval, chart_type)

    v1, v2, v3 = st.columns(3)
    v1.metric("EV / EBITDA", _fmt_ratio(f["ev_ebitda"]))
    v2.metric("Profit Margin", _fmt_pct(f["profit_margin"]))
    v3.metric("Return on Equity", _fmt_pct(f["roe"]))

    st.markdown("**Peer Comparison**")
    peers = get_peers(company_name)
    if not peers:
        st.caption("No peers found in the current watchlists.")
    else:
        peer_df = fetch_watchlist(peers)
        st.dataframe(
            peer_df[["Name", "Symbol", "Price", "Change %"]],
            use_container_width=True,
            hide_index=True,
        )
    st.caption(
        "Peers shown are other companies in the same watchlist category, "
        "not a verified sector match — a reasonable free-data approximation."
    )


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

if AUTOREFRESH_AVAILABLE:
    st_autorefresh(interval=60_000, key="data_refresh")

st.title("📊 Northgate Credit")
st.caption("NBFC & Private-Credit Terminal")
st.caption(
    f"Last refreshed: {datetime.now().strftime('%d %b %Y, %I:%M:%S %p')}"
    + ("  ·  auto-updates every 60s" if AUTOREFRESH_AVAILABLE else "")
)

# --- Headline indices ---
idx_cols = st.columns(len(INDEX_TICKERS))
for col, (name, ticker) in zip(idx_cols, INDEX_TICKERS.items()):
    price, change_pct = fetch_index_snapshot(ticker)
    col.metric(
        name,
        _fmt_num(price),
        f"{change_pct:.2f}%" if change_pct is not None else None,
    )

st.divider()

col_watchlist, col_feed = st.columns([2, 1])

with col_watchlist:
    st.subheader("Watchlists")
    tabs = st.tabs(list(ALL_WATCHLISTS.keys()))
    for tab, (group_name, group) in zip(tabs, ALL_WATCHLISTS.items()):
        with tab:
            df = fetch_watchlist(group)

            def _highlight_change(val):
                if pd.isna(val):
                    return ""
                return "color: #26a69a" if val >= 0 else "color: #ef5350"

            st.dataframe(
                df.style.map(_highlight_change, subset=["Change %"]),
                use_container_width=True,
                hide_index=True,
            )
    st.caption(
        "Source: Yahoo Finance via yfinance — delayed quotes, fine for a "
        "dashboard, not for execution."
    )

with col_feed:
    st.subheader("RBI Regulatory Feed")
    feed_choice = st.selectbox("Feed", list(RBI_FEEDS.keys()))
    items = fetch_rbi_feed(RBI_FEEDS[feed_choice])

    if not items:
        st.warning(
            "Couldn't load this feed right now — RBI occasionally changes "
            "its RSS endpoints. Double-check the URL in RBI_FEEDS at the "
            "top of this file."
        )
    else:
        for item in items:
            st.markdown(f"**[{item['title']}]({item['link']})**")
            if item["published"]:
                st.caption(item["published"])
            st.divider()

st.divider()

# --- Company deep-dive ---
st.subheader("🔍 Company Deep Dive")
all_companies = {}
for group in ALL_WATCHLISTS.values():
    all_companies.update(group)

selected_company = st.selectbox(
    "Select a company", list(all_companies.keys()), key="deep_dive_select"
)
if selected_company:
    render_company_detail(selected_company, all_companies[selected_company])
