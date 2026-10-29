"""
Tradeloop — v2
--------------
A dark, data-dense dashboard covering stock exchanges around the world, two
headline indices, RBI regulatory updates plus worldwide market news, and a
per-exchange deep-dive view (price history in multiple timeframes and chart
types, trailing returns, and a regional peer table).

Data sources (all free):
  - Yahoo Finance (via yfinance) for quotes and history
  - RBI's own RSS feeds for circulars/notifications and press releases
  - CNBC's public RSS feeds for worldwide markets/finance news

Run it:
    pip install -r requirements.txt
    streamlit run app.py

Known limits of a free-data v1 (be upfront about these with anyone you
show this to):
  - Quotes are delayed, not real-time tick data — fine for a dashboard,
    not for execution.
  - An exchange doesn't have a single price, so each one is shown through its
    benchmark index (NYSE -> NYSE Composite, NSE -> Nifty 50, and so on).
    Levels are index points, not currency.
  - Not every exchange is on Yahoo Finance. Any that come back empty are
    hidden by default; untick the box above the table to see them.
  - Intraday history only goes back a handful of days (a Yahoo Finance
    limit, not a bug) — that's why "1 Day" / "5 Days" only show recent
    sessions.
  - "Peers" are the other exchanges in the same region, not a statistical
    peer set.
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

st.set_page_config(page_title="Tradeloop", layout="wide", page_icon="📊")

# ---------------------------------------------------------------------------
# Dark terminal styling
# ---------------------------------------------------------------------------

st.markdown(
    """
    <style>
    .stApp {
        background-color: #0e1117;
        color: #d1d4dc;
        font-family: -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
    }
    section[data-testid="stSidebar"] { background-color: #131722; }
    h1, h2, h3 { font-weight: 700; letter-spacing: -0.3px; }
    .stTabs [data-baseweb="tab"] { font-weight: 500; font-size: 0.95rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Config — one line per exchange:
#     (exchange, country, benchmark index, Yahoo Finance symbol)
#
# Yahoo has no single "price" for an exchange, so each one is tracked through
# its benchmark index. To add an exchange, drop a new line into a region (or
# add a new region) — the tabs, the table and the deep-dive menu all build
# themselves from this dict. If a row ever comes back empty, check its symbol
# on finance.yahoo.com. Keep exchange names unique.
# ---------------------------------------------------------------------------

EXCHANGES = {
    "Americas": [
        ("New York Stock Exchange (NYSE)", "United States", "NYSE Composite", "^NYA"),
        ("Nasdaq", "United States", "Nasdaq Composite", "^IXIC"),
        ("NYSE American", "United States", "NYSE American Composite", "^XAX"),
        ("Toronto Stock Exchange (TSX)", "Canada", "S&P/TSX Composite", "^GSPTSE"),
        ("TSX Venture Exchange", "Canada", "S&P/TSX Venture Composite", "^SPCDNX"),
        ("Bolsa Mexicana de Valores (BMV)", "Mexico", "S&P/BMV IPC", "^MXX"),
        ("B3 (Brasil Bolsa Balcão)", "Brazil", "Ibovespa", "^BVSP"),
        ("Bolsa de Comercio de Buenos Aires", "Argentina", "S&P Merval", "^MERV"),
        ("Bolsa de Santiago", "Chile", "S&P CLX IPSA", "^IPSA"),
        ("Bolsa de Valores de Lima (BVL)", "Peru", "S&P/BVL Peru General", "^SPBLPGPT"),
        ("Bolsa de Valores de Colombia (BVC)", "Colombia", "MSCI COLCAP", "^COLCAP"),
    ],
    "Europe": [
        ("London Stock Exchange (LSE)", "United Kingdom", "FTSE 100", "^FTSE"),
        ("Euronext (pan-European)", "Europe", "Euronext 100", "^N100"),
        ("Euronext Paris", "France", "CAC 40", "^FCHI"),
        ("Euronext Amsterdam", "Netherlands", "AEX", "^AEX"),
        ("Euronext Brussels", "Belgium", "BEL 20", "^BFX"),
        ("Euronext Lisbon", "Portugal", "PSI", "PSI20.LS"),
        ("Euronext Dublin", "Ireland", "ISEQ Overall", "^ISEQ"),
        ("Euronext Oslo", "Norway", "OSE Benchmark", "OSEBX.OL"),
        ("Euronext Milan (Borsa Italiana)", "Italy", "FTSE MIB", "FTSEMIB.MI"),
        ("Deutsche Börse (Frankfurt / Xetra)", "Germany", "DAX", "^GDAXI"),
        ("SIX Swiss Exchange", "Switzerland", "SMI", "^SSMI"),
        ("Bolsas y Mercados Españoles (BME)", "Spain", "IBEX 35", "^IBEX"),
        ("Nasdaq Stockholm", "Sweden", "OMX Stockholm 30", "^OMX"),
        ("Nasdaq Copenhagen", "Denmark", "OMX Copenhagen 25", "^OMXC25"),
        ("Nasdaq Helsinki", "Finland", "OMX Helsinki 25", "^OMXH25"),
        ("Wiener Börse (Vienna)", "Austria", "ATX", "^ATX"),
        ("Warsaw Stock Exchange (GPW)", "Poland", "WIG20", "WIG20.WA"),
        ("Athens Exchange (ATHEX)", "Greece", "Athens General Composite", "GD.AT"),
        ("Borsa Istanbul", "Türkiye", "BIST 100", "XU100.IS"),
    ],
    "Asia-Pacific": [
        ("National Stock Exchange of India (NSE)", "India", "Nifty 50", "^NSEI"),
        ("BSE (Bombay Stock Exchange)", "India", "S&P BSE Sensex", "^BSESN"),
        ("Japan Exchange Group (Tokyo)", "Japan", "Nikkei 225", "^N225"),
        ("Shanghai Stock Exchange (SSE)", "China", "SSE Composite", "000001.SS"),
        ("Shenzhen Stock Exchange (SZSE)", "China", "SZSE Component", "399001.SZ"),
        ("Hong Kong Exchanges (HKEX)", "Hong Kong", "Hang Seng", "^HSI"),
        ("Korea Exchange (KOSPI)", "South Korea", "KOSPI Composite", "^KS11"),
        ("Korea Exchange (KOSDAQ)", "South Korea", "KOSDAQ Composite", "^KQ11"),
        ("Taiwan Stock Exchange (TWSE)", "Taiwan", "TAIEX", "^TWII"),
        ("Singapore Exchange (SGX)", "Singapore", "Straits Times Index", "^STI"),
        ("Bursa Malaysia", "Malaysia", "FTSE Bursa Malaysia KLCI", "^KLSE"),
        ("Indonesia Stock Exchange (IDX)", "Indonesia", "IDX Composite", "^JKSE"),
        ("Stock Exchange of Thailand (SET)", "Thailand", "SET Index", "^SET.BK"),
        ("Philippine Stock Exchange (PSE)", "Philippines", "PSEi", "PSEI.PS"),
        ("Pakistan Stock Exchange (PSX)", "Pakistan", "KSE-100", "^KSE"),
        ("Australian Securities Exchange (ASX)", "Australia", "S&P/ASX 200", "^AXJO"),
        ("NZX", "New Zealand", "S&P/NZX 50", "^NZ50"),
    ],
    "Middle East & Africa": [
        ("Tel Aviv Stock Exchange (TASE)", "Israel", "TA-125", "^TA125.TA"),
        ("Saudi Exchange (Tadawul)", "Saudi Arabia", "Tadawul All Share (TASI)", "^TASI.SR"),
        ("Qatar Stock Exchange", "Qatar", "QE Index", "^GNRI.QA"),
        ("Egyptian Exchange (EGX)", "Egypt", "EGX 30", "^CASE30"),
        ("Johannesburg Stock Exchange (JSE)", "South Africa", "FTSE/JSE All Share", "^J203.JO"),
    ],
}

EXCHANGE_TABLE = pd.DataFrame(
    [
        {
            "Region": region,
            "Exchange": exchange,
            "Country": country,
            "Benchmark": benchmark,
            "Symbol": symbol,
        }
        for region, rows in EXCHANGES.items()
        for exchange, country, benchmark, symbol in rows
    ]
)
EXCHANGE_BY_NAME = EXCHANGE_TABLE.set_index("Exchange").to_dict("index")

TABLE_COLUMNS = [
    "Exchange",
    "Country",
    "Benchmark",
    "Symbol",
    "Level",
    "Change %",
    "Day High",
    "Day Low",
    "As of",
]

INDEX_TICKERS = {
    "Nifty 50": "^NSEI",
    "NASDAQ Composite": "^IXIC",
}

NEWS_FEEDS = {
    "RBI — Notifications / Circulars": "https://rbi.org.in/notifications_rss.xml",
    "RBI — Press Releases": "https://rbi.org.in/pressreleases_rss.xml",
    "World — Top News (CNBC)": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "World — Global Markets (CNBC)": "https://www.cnbc.com/id/100727362/device/rss/rss.html",
    "World — Finance (CNBC)": "https://www.cnbc.com/id/10000664/device/rss/rss.html",
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

def _quotes_from_download(raw: pd.DataFrame, symbols) -> pd.DataFrame:
    """Boil a multi-ticker yf.download() frame down to one row per symbol:
    latest level, change vs the previous close, that session's high/low, and
    the date of the latest bar (exchanges sit in different time zones, so the
    'as of' date tells you whether Tokyo is showing today or yesterday)."""
    rows = []
    for symbol in dict.fromkeys(symbols):
        row = {
            "Symbol": symbol,
            "Level": None,
            "Change %": None,
            "Day High": None,
            "Day Low": None,
            "As of": None,
        }
        try:
            bars = raw[symbol].dropna(subset=["Close"])
            if not bars.empty:
                last = bars.iloc[-1]
                price = float(last["Close"])
                row["Level"] = round(price, 2)
                row["Day High"] = round(float(last["High"]), 2)
                row["Day Low"] = round(float(last["Low"]), 2)
                row["As of"] = bars.index[-1].strftime("%d %b")
                if len(bars) > 1:
                    prev_close = float(bars["Close"].iloc[-2])
                    if prev_close:
                        row["Change %"] = round((price - prev_close) / prev_close * 100, 2)
        except Exception:
            pass
        rows.append(row)
    return pd.DataFrame(rows)


@st.cache_data(ttl=60)
def fetch_exchange_quotes(symbols: tuple) -> pd.DataFrame:
    """One batched Yahoo call for every exchange. Looking up 50+ symbols one
    by one through .info would be slow and invites rate limiting."""
    try:
        raw = yf.download(
            list(symbols),
            period="1mo",
            interval="1d",
            group_by="ticker",
            auto_adjust=False,
            progress=False,
            threads=True,
        )
    except Exception:
        raw = pd.DataFrame()
    return _quotes_from_download(raw, symbols)


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
def fetch_news_feed(url: str, limit: int = 12) -> list[dict]:
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
def fetch_exchange_stats(symbol: str) -> dict:
    """Headline numbers for the deep dive, worked out from a year of daily
    bars. Indices have no P/E, margins or ROE, and Yahoo's .info is thin for
    them, so everything here comes straight from price history."""
    stats = dict.fromkeys(
        (
            "price",
            "prev_close",
            "day_high",
            "day_low",
            "wk52_high",
            "wk52_low",
            "volume",
            "ret_1m",
            "ret_3m",
            "ret_6m",
            "ret_1y",
        )
    )
    hist = fetch_history(symbol, "1y", "1d")
    if hist.empty or "Close" not in hist.columns:
        return stats

    try:
        bars = hist.dropna(subset=["Close"])
        if bars.empty:
            return stats

        close = bars["Close"]
        last = bars.iloc[-1]
        price = float(close.iloc[-1])

        stats["price"] = price
        stats["prev_close"] = float(close.iloc[-2]) if len(close) > 1 else None
        stats["day_high"] = float(last["High"])
        stats["day_low"] = float(last["Low"])
        stats["wk52_high"] = float(bars["High"].max())
        stats["wk52_low"] = float(bars["Low"].min())

        volume = last.get("Volume")
        stats["volume"] = int(volume) if volume and pd.notna(volume) else None

        end = close.index[-1]
        for key, months in (("ret_1m", 1), ("ret_3m", 3), ("ret_6m", 6)):
            base = close.asof(end - pd.DateOffset(months=months))
            if pd.notna(base) and base:
                stats[key] = (price / base - 1) * 100
        if close.iloc[0]:
            stats["ret_1y"] = (price / close.iloc[0] - 1) * 100
    except Exception:
        pass
    return stats


def _fmt_num(x):
    return f"{x:,.2f}" if isinstance(x, (int, float)) and pd.notna(x) else "—"


def _fmt_signed_pct(x):
    return f"{x:+.2f}%" if isinstance(x, (int, float)) and pd.notna(x) else "—"


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

    # Most indices report no volume at all; skip the empty panel for those.
    has_volume = "Volume" in hist.columns and hist["Volume"].fillna(0).sum() > 0

    fig = make_subplots(
        rows=2 if has_volume else 1,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.75, 0.25] if has_volume else [1.0],
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

    if has_volume:
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
    st.plotly_chart(fig, width="stretch")


# ---------------------------------------------------------------------------
# Exchange deep-dive panel
# ---------------------------------------------------------------------------

def render_exchange_detail(name: str, table: pd.DataFrame):
    meta = EXCHANGE_BY_NAME[name]
    symbol = meta["Symbol"]
    s = fetch_exchange_stats(symbol)

    change_pct = None
    if s["price"] and s["prev_close"]:
        change_pct = (s["price"] - s["prev_close"]) / s["prev_close"] * 100

    st.subheader(name)
    st.caption(f"{meta['Benchmark']} ({symbol}), {meta['Country']}")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Index Level", _fmt_num(s["price"]), f"{change_pct:.2f}%" if change_pct is not None else None)
    m2.metric("Day Range", f"{_fmt_num(s['day_low'])} – {_fmt_num(s['day_high'])}")
    m3.metric("52-Wk Range", f"{_fmt_num(s['wk52_low'])} – {_fmt_num(s['wk52_high'])}")
    m4.metric("Volume", f"{s['volume']:,}" if s["volume"] else "—")

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

    r1, r2, r3, r4 = st.columns(4)
    r1.metric("1 Month", _fmt_signed_pct(s["ret_1m"]))
    r2.metric("3 Months", _fmt_signed_pct(s["ret_3m"]))
    r3.metric("6 Months", _fmt_signed_pct(s["ret_6m"]))
    r4.metric("1 Year", _fmt_signed_pct(s["ret_1y"]))

    st.markdown("**Peer Comparison**")
    peers = table[(table["Region"] == meta["Region"]) & (table["Exchange"] != name)]
    peers = peers.dropna(subset=["Level"])
    if peers.empty:
        st.caption("No other exchanges in this region are reporting data right now.")
    else:
        st.dataframe(
            peers[["Exchange", "Benchmark", "Symbol", "Level", "Change %"]],
            width="stretch",
            hide_index=True,
        )
    st.caption(
        f"Peers are the other exchanges in {meta['Region']}, not a statistical "
        "peer set."
    )


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

if AUTOREFRESH_AVAILABLE:
    st_autorefresh(interval=60_000, key="data_refresh")

st.title("📊 Tradeloop")
st.caption("Global Stock Exchange Terminal")
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

col_exchanges, col_feed = st.columns([2, 1])

with col_exchanges:
    st.subheader("Stock Exchanges")

    quotes = fetch_exchange_quotes(tuple(EXCHANGE_TABLE["Symbol"]))
    all_exchanges = EXCHANGE_TABLE.merge(quotes, on="Symbol", how="left")
    live_count = int(all_exchanges["Level"].notna().sum())

    hide_empty = st.checkbox("Hide exchanges with no data", value=True)
    st.caption(f"{live_count} of {len(all_exchanges)} exchanges reporting")

    if live_count == 0:
        st.warning(
            "Yahoo Finance returned no quotes just now, which is usually a "
            "brief rate limit or connection issue. Reload in a minute."
        )

    shown = all_exchanges.dropna(subset=["Level"]) if hide_empty else all_exchanges

    tab_labels = ["All Exchanges", *EXCHANGES.keys()]
    for tab, label in zip(st.tabs(tab_labels), tab_labels):
        with tab:
            if label == "All Exchanges":
                view = shown[["Region", *TABLE_COLUMNS]]
            else:
                view = shown[shown["Region"] == label][TABLE_COLUMNS]
            st.dataframe(view, width="stretch", hide_index=True)

    st.caption(
        "Source: Yahoo Finance via yfinance — delayed quotes, fine for a "
        "dashboard, not for execution. Each exchange is shown through its "
        "benchmark index, so levels are index points, not prices."
    )

with col_feed:
    st.subheader("News Feed")
    feed_choice = st.selectbox("Feed", list(NEWS_FEEDS.keys()))
    items = fetch_news_feed(NEWS_FEEDS[feed_choice])

    if not items:
        st.warning(
            "Couldn't load this feed right now — sources occasionally change "
            "their RSS endpoints. Double-check the URL in NEWS_FEEDS at the "
            "top of this file."
        )
    else:
        for item in items:
            st.markdown(f"**[{item['title']}]({item['link']})**")
            if item["published"]:
                st.caption(item["published"])
            st.divider()

st.divider()

# --- Exchange deep-dive ---
st.subheader("🔍 Exchange Deep Dive")
selected_exchange = st.selectbox(
    "Select an exchange", list(EXCHANGE_BY_NAME.keys()), key="deep_dive_select"
)
if selected_exchange:
    render_exchange_detail(selected_exchange, all_exchanges)
