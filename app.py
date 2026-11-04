"""
Tradeloop — v3
--------------
A dark, data-dense dashboard covering stock exchanges around the world. The
centerpiece is one shared chart workspace (search box, candlestick/line
chart with SMA/RSI/MACD, a plain-English key-facts readout, and valuation
ratios for companies) fed by clickable watchlists throughout the app —
TradingView-style, built from Streamlit's own widgets rather than copying
TradingView's exact look. Below that: exchange tables, worldwide news, and
a per-exchange deep dive (trailing returns, that exchange's companies as a
clickable watchlist, and a regional peer table).

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
  - Company lists are hand-picked large caps per exchange, because Yahoo has
    no free list of every listing. Symbols for the smaller exchanges are not
    all verified, so a row that shows dashes is the first thing to check.
"""

from datetime import datetime

import feedparser
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf
from plotly.subplots import make_subplots

try:
    from supabase import create_client

    SUPABASE_SDK_AVAILABLE = True
except ImportError:
    SUPABASE_SDK_AVAILABLE = False

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

    /* Watchlist rows: plain Streamlit buttons re-styled as flat,
       left-aligned list rows instead of boxed buttons. */
    div.stButton > button {
        background-color: transparent;
        border: none;
        color: #d1d4dc;
        text-align: left;
        font-weight: 500;
        padding: 2px 6px;
    }
    div.stButton > button:hover {
        color: #42a5f5;
        background-color: rgba(255, 255, 255, 0.06);
        border: none;
    }

    /* Cosmetic SELL/BUY price pills — display only, not clickable trade
       buttons (Tradeloop has no brokerage connection). */
    .price-pill-row { display: flex; gap: 10px; margin: 10px 0 6px 0; }
    .price-pill {
        flex: 1;
        border-radius: 6px;
        padding: 8px 16px;
        text-align: center;
    }
    .price-pill.sell { background-color: rgba(239, 83, 80, 0.15); border: 1px solid #ef5350; }
    .price-pill.buy { background-color: rgba(66, 165, 245, 0.15); border: 1px solid #42a5f5; }
    .price-pill .pill-label { font-size: 0.72rem; font-weight: 700; letter-spacing: 0.5px; opacity: 0.85; }
    .price-pill.sell .pill-label { color: #ef5350; }
    .price-pill.buy .pill-label { color: #42a5f5; }
    .price-pill .pill-price { font-size: 1.25rem; font-weight: 700; margin-top: 2px; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Accounts (Supabase Auth) — free, real email/password accounts, not just a
# shared password gate. Needs two secrets set in Streamlit Cloud (Manage
# app -> Settings -> Secrets), from a free project at supabase.com:
#
#     SUPABASE_URL = "https://xxxxxxxx.supabase.co"
#     SUPABASE_ANON_KEY = "ey...."
#
# Both are on the project's Settings -> API page. By default Supabase
# emails new users a confirmation link before they can log in — fine for a
# real launch, but for testing you can turn it off under Authentication ->
# Providers -> Email -> "Confirm email".
# ---------------------------------------------------------------------------

def password_strength(password: str) -> tuple[str, str, float]:
    """A simple, visible strength meter — length plus a mix of upper/lower
    case, digits and symbols. Not a security guarantee, just the same kind
    of live feedback most sign-up forms show."""
    if not password:
        return "", "", 0.0
    score = 0
    if len(password) >= 8:
        score += 1
    if len(password) >= 12:
        score += 1
    if any(c.islower() for c in password) and any(c.isupper() for c in password):
        score += 1
    if any(c.isdigit() for c in password):
        score += 1
    if any(not c.isalnum() for c in password):
        score += 1

    if score <= 1:
        return "Weak", "#ef5350", score / 5
    if score <= 3:
        return "Fair", "#ffa726", score / 5
    if score == 4:
        return "Good", "#42a5f5", score / 5
    return "Strong", "#26a69a", score / 5


def render_password_strength(password: str):
    if not password:
        return
    label, color, frac = password_strength(password)
    st.markdown(
        f"""
        <div style="margin: -6px 0 12px 0;">
            <div style="background: rgba(255,255,255,0.08); border-radius: 4px; height: 6px; overflow: hidden;">
                <div style="width: {int(frac * 100)}%; height: 100%; background: {color};"></div>
            </div>
            <div style="font-size: 0.78rem; color: {color}; margin-top: 3px;">{label}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


@st.cache_resource
def get_supabase_client():
    url = st.secrets.get("SUPABASE_URL") if hasattr(st, "secrets") else None
    key = st.secrets.get("SUPABASE_ANON_KEY") if hasattr(st, "secrets") else None
    if not url or not key:
        return None
    try:
        return create_client(url, key)
    except Exception:
        return None


def require_login():
    """Blocks the rest of the app behind a Log In / Register screen. Must
    run before anything else draws, so a signed-out visitor never sees the
    dashboard (or triggers any of its Yahoo Finance calls)."""
    if st.session_state.get("user_email"):
        return  # already signed in this session

    if not SUPABASE_SDK_AVAILABLE:
        st.error(
            "Accounts aren't set up yet — add `supabase` to requirements.txt "
            "and redeploy."
        )
        st.stop()

    client = get_supabase_client()
    if client is None:
        st.error(
            "Accounts aren't configured yet — add SUPABASE_URL and "
            "SUPABASE_ANON_KEY under this app's Settings -> Secrets on "
            "Streamlit Cloud, then reload."
        )
        st.stop()

    st.title("📊 Tradeloop")
    st.caption("Sign in or create a free account to continue.")

    tab_login, tab_register = st.tabs(["Log In", "Register"])

    with tab_login:
        with st.form("login_form"):
            email = st.text_input("Email", key="login_email")
            password = st.text_input("Password", type="password", key="login_password")
            submitted = st.form_submit_button("Log In")
        if submitted:
            try:
                result = client.auth.sign_in_with_password(
                    {"email": email, "password": password}
                )
                st.session_state.user_email = result.user.email
                st.session_state.user_id = result.user.id
                st.rerun()
            except Exception as exc:
                st.error(f"Couldn't log in: {exc}")

    with tab_register:
        st.caption("Takes a few seconds — just an email and a password.")
        # Not wrapped in st.form, on purpose: a form only reruns the script
        # on submit, so the strength meter below couldn't update live as
        # you type. Outside a form, every keystroke reruns the page, which
        # is what makes the live meter possible.
        reg_email = st.text_input("Email", key="register_email")
        reg_password = st.text_input(
            "Password (min. 6 characters)", type="password", key="register_password"
        )
        render_password_strength(reg_password)
        reg_submitted = st.button("Create Account", key="register_submit")
        if reg_submitted:
            if "@" not in reg_email:
                st.error("Enter a valid email address.")
            elif len(reg_password) < 6:
                st.error("Password must be at least 6 characters.")
            else:
                try:
                    result = client.auth.sign_up(
                        {"email": reg_email, "password": reg_password}
                    )
                    if result.session is not None:
                        # Email confirmation is off for this project —
                        # signed in immediately.
                        st.session_state.user_email = result.user.email
                        st.session_state.user_id = result.user.id
                        st.rerun()
                    else:
                        st.success(
                            "Account created — check your email for a "
                            "confirmation link, then log in from the Log "
                            "In tab."
                        )
                except Exception as exc:
                    st.error(f"Couldn't create that account: {exc}")

    st.stop()


require_login()

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

# ---------------------------------------------------------------------------
# Companies listed on each exchange, as (company, Yahoo symbol). Yahoo has no
# free list of every company on an exchange, so these are large caps picked by
# hand. Add, swap or drop rows freely. Keys must match the names in EXCHANGES
# exactly. Prices are in each company's local currency.
# Symbols for the smaller exchanges are not all verified: a row that shows
# dashes is the first thing to check.
# ---------------------------------------------------------------------------

LISTED_COMPANIES = {
    "New York Stock Exchange (NYSE)": [
        ("JPMorgan Chase", "JPM"), ("Visa", "V"), ("Walmart", "WMT"),
        ("Exxon Mobil", "XOM"), ("Johnson & Johnson", "JNJ"), ("Berkshire Hathaway", "BRK-B"),
        ("Procter & Gamble", "PG"), ("Coca-Cola", "KO"), ("Caterpillar", "CAT"),
    ],
    "Nasdaq": [
        ("Apple", "AAPL"), ("Microsoft", "MSFT"), ("Nvidia", "NVDA"),
        ("Amazon", "AMZN"), ("Alphabet", "GOOGL"), ("Meta Platforms", "META"),
        ("Tesla", "TSLA"), ("Broadcom", "AVGO"), ("Netflix", "NFLX"),
        ("Costco", "COST"),
    ],
    "NYSE American": [
        ("Cheniere Energy", "LNG"), ("Uranium Energy", "UEC"),
        ("NovaGold", "NG"), ("B2Gold", "BTG"),
    ],
    "Toronto Stock Exchange (TSX)": [
        ("Royal Bank of Canada", "RY.TO"), ("TD Bank", "TD.TO"), ("Shopify", "SHOP.TO"),
        ("Enbridge", "ENB.TO"), ("Canadian National Railway", "CNR.TO"),
        ("Canadian Natural Resources", "CNQ.TO"), ("Bank of Nova Scotia", "BNS.TO"),
        ("Constellation Software", "CSU.TO"),
    ],
    "TSX Venture Exchange": [
        ("Standard Lithium", "SLI.V"), ("Frontier Lithium", "FL.V"),
        ("GreenPower Motor", "GPV.V"),
    ],
    "Bolsa Mexicana de Valores (BMV)": [
        ("América Móvil", "AMXL.MX"), ("Walmart de México", "WALMEX.MX"),
        ("Banorte", "GFNORTEO.MX"), ("Grupo México", "GMEXICOB.MX"),
        ("FEMSA", "FEMSAUBD.MX"), ("Cemex", "CEMEXCPO.MX"), ("Grupo Bimbo", "BIMBOA.MX"),
    ],
    "B3 (Brasil Bolsa Balcão)": [
        ("Petrobras", "PETR4.SA"), ("Vale", "VALE3.SA"), ("Itaú Unibanco", "ITUB4.SA"),
        ("Banco do Brasil", "BBAS3.SA"), ("Ambev", "ABEV3.SA"), ("WEG", "WEGE3.SA"),
    ],
    "Bolsa de Comercio de Buenos Aires": [
        ("YPF", "YPFD.BA"), ("Grupo Galicia", "GGAL.BA"), ("Pampa Energía", "PAMP.BA"),
        ("Banco Macro", "BMA.BA"), ("Grupo Supervielle", "SUPV.BA"),
        ("Telecom Argentina", "TECO2.BA"),
    ],
    "Bolsa de Santiago": [
        ("Enel Américas", "ENELAM.SN"), ("Empresas Copec", "COPEC.SN"),
        ("Falabella", "FALABELLA.SN"), ("SQM", "SQM-B.SN"),
        ("Banco de Crédito e Inversiones", "BCI.SN"), ("Cencosud", "CENCOSUD.SN"),
    ],
    "Bolsa de Valores de Lima (BVL)": [],  # add Peruvian companies here
    "Bolsa de Valores de Colombia (BVC)": [
        ("Ecopetrol", "ECOPETROL.CL"), ("Bancolombia", "PFBCOLOM.CL"),
        ("Interconexión Eléctrica (ISA)", "ISA.CL"), ("Cementos Argos", "CEMARGOS.CL"),
    ],
    "London Stock Exchange (LSE)": [
        ("Shell", "SHEL.L"), ("AstraZeneca", "AZN.L"), ("HSBC", "HSBA.L"),
        ("Unilever", "ULVR.L"), ("BP", "BP.L"), ("Diageo", "DGE.L"),
        ("GSK", "GSK.L"), ("Rio Tinto", "RIO.L"),
    ],
    "Euronext (pan-European)": [
        ("ASML", "ASML.AS"), ("LVMH", "MC.PA"), ("TotalEnergies", "TTE.PA"),
        ("Airbus", "AIR.PA"), ("Schneider Electric", "SU.PA"), ("L'Oréal", "OR.PA"),
    ],
    "Euronext Paris": [
        ("LVMH", "MC.PA"), ("TotalEnergies", "TTE.PA"), ("L'Oréal", "OR.PA"),
        ("Sanofi", "SAN.PA"), ("Airbus", "AIR.PA"), ("Schneider Electric", "SU.PA"),
        ("BNP Paribas", "BNP.PA"), ("Air Liquide", "AI.PA"),
    ],
    "Euronext Amsterdam": [
        ("ASML", "ASML.AS"), ("Adyen", "ADYEN.AS"), ("Heineken", "HEIA.AS"),
        ("ING Group", "INGA.AS"), ("Philips", "PHIA.AS"), ("Prosus", "PRX.AS"),
        ("Ahold Delhaize", "AD.AS"),
    ],
    "Euronext Brussels": [
        ("AB InBev", "ABI.BR"), ("KBC Group", "KBC.BR"), ("UCB", "UCB.BR"),
        ("Solvay", "SOLB.BR"), ("Umicore", "UMI.BR"),
    ],
    "Euronext Lisbon": [
        ("EDP", "EDP.LS"), ("Galp Energia", "GALP.LS"), ("Jerónimo Martins", "JMT.LS"),
        ("Millennium BCP", "BCP.LS"), ("Navigator", "NVG.LS"),
    ],
    "Euronext Dublin": [
        ("Kerry Group", "KYG.IR"), ("Bank of Ireland", "BIRG.IR"), ("AIB Group", "AIB.IR"),
        ("Kingspan", "KRX.IR"), ("Ryanair", "RYA.IR"),
    ],
    "Euronext Oslo": [
        ("Equinor", "EQNR.OL"), ("DNB Bank", "DNB.OL"), ("Telenor", "TEL.OL"),
        ("Norsk Hydro", "NHY.OL"), ("Mowi", "MOWI.OL"), ("Yara", "YAR.OL"),
    ],
    "Euronext Milan (Borsa Italiana)": [
        ("Enel", "ENEL.MI"), ("Eni", "ENI.MI"), ("UniCredit", "UCG.MI"),
        ("Intesa Sanpaolo", "ISP.MI"), ("Ferrari", "RACE.MI"), ("Stellantis", "STLAM.MI"),
        ("Generali", "G.MI"), ("Leonardo", "LDO.MI"),
    ],
    "Deutsche Börse (Frankfurt / Xetra)": [
        ("SAP", "SAP.DE"), ("Siemens", "SIE.DE"), ("Allianz", "ALV.DE"),
        ("Deutsche Telekom", "DTE.DE"), ("Mercedes-Benz", "MBG.DE"), ("BMW", "BMW.DE"),
        ("Munich Re", "MUV2.DE"), ("Volkswagen", "VOW3.DE"), ("Deutsche Bank", "DBK.DE"),
    ],
    "SIX Swiss Exchange": [
        ("Nestlé", "NESN.SW"), ("Novartis", "NOVN.SW"), ("Roche", "ROG.SW"),
        ("UBS", "UBSG.SW"), ("Zurich Insurance", "ZURN.SW"), ("ABB", "ABBN.SW"),
        ("Richemont", "CFR.SW"),
    ],
    "Bolsas y Mercados Españoles (BME)": [
        ("Santander", "SAN.MC"), ("BBVA", "BBVA.MC"), ("Iberdrola", "IBE.MC"),
        ("Inditex", "ITX.MC"), ("Telefónica", "TEF.MC"), ("Repsol", "REP.MC"),
        ("CaixaBank", "CABK.MC"), ("Amadeus", "AMS.MC"),
    ],
    "Nasdaq Stockholm": [
        ("Volvo", "VOLV-B.ST"), ("Investor", "INVE-B.ST"), ("Atlas Copco", "ATCO-A.ST"),
        ("Ericsson", "ERIC-B.ST"), ("Evolution", "EVO.ST"), ("H&M", "HM-B.ST"),
    ],
    "Nasdaq Copenhagen": [
        ("Novo Nordisk", "NOVO-B.CO"), ("Maersk", "MAERSK-B.CO"), ("DSV", "DSV.CO"),
        ("Carlsberg", "CARL-B.CO"), ("Vestas", "VWS.CO"), ("Pandora", "PNDORA.CO"),
    ],
    "Nasdaq Helsinki": [
        ("Nokia", "NOKIA.HE"), ("Nordea", "NDA-FI.HE"), ("Sampo", "SAMPO.HE"),
        ("Kone", "KNEBV.HE"), ("Fortum", "FORTUM.HE"), ("UPM-Kymmene", "UPM.HE"),
    ],
    "Wiener Börse (Vienna)": [
        ("Erste Group", "EBS.VI"), ("OMV", "OMV.VI"), ("Verbund", "VER.VI"),
        ("Raiffeisen Bank International", "RBI.VI"), ("Andritz", "ANDR.VI"),
        ("voestalpine", "VOE.VI"),
    ],
    "Warsaw Stock Exchange (GPW)": [
        ("PKO Bank Polski", "PKO.WA"), ("PKN Orlen", "PKN.WA"), ("KGHM", "KGH.WA"),
        ("PZU", "PZU.WA"), ("Dino Polska", "DNP.WA"), ("CD Projekt", "CDR.WA"),
    ],
    "Athens Exchange (ATHEX)": [
        ("National Bank of Greece", "ETE.AT"), ("Piraeus Bank", "TPEIR.AT"),
        ("Alpha Bank", "ALPHA.AT"), ("Eurobank", "EUROB.AT"), ("OTE", "HTO.AT"),
        ("Motor Oil", "MOH.AT"), ("PPC", "PPC.AT"),
    ],
    "Borsa Istanbul": [
        ("Turkish Airlines", "THYAO.IS"), ("Akbank", "AKBNK.IS"), ("Garanti BBVA", "GARAN.IS"),
        ("İş Bankası (C)", "ISCTR.IS"), ("Ereğli Demir Çelik", "EREGL.IS"),
        ("Koç Holding", "KCHOL.IS"),
    ],
    "National Stock Exchange of India (NSE)": [
        ("Reliance Industries", "RELIANCE.NS"), ("TCS", "TCS.NS"), ("HDFC Bank", "HDFCBANK.NS"),
        ("Infosys", "INFY.NS"), ("ICICI Bank", "ICICIBANK.NS"), ("Bharti Airtel", "BHARTIARTL.NS"),
        ("ITC", "ITC.NS"), ("State Bank of India", "SBIN.NS"), ("Larsen & Toubro", "LT.NS"),
    ],
    "BSE (Bombay Stock Exchange)": [
        ("Reliance Industries", "RELIANCE.BO"), ("TCS", "TCS.BO"), ("HDFC Bank", "HDFCBANK.BO"),
        ("Infosys", "INFY.BO"), ("ICICI Bank", "ICICIBANK.BO"), ("State Bank of India", "SBIN.BO"),
    ],
    "Japan Exchange Group (Tokyo)": [
        ("Toyota Motor", "7203.T"), ("Sony Group", "6758.T"), ("SoftBank Group", "9984.T"),
        ("Keyence", "6861.T"), ("Mitsubishi UFJ Financial", "8306.T"), ("Nintendo", "7974.T"),
        ("Tokyo Electron", "8035.T"), ("Hitachi", "6501.T"),
    ],
    "Shanghai Stock Exchange (SSE)": [
        ("Kweichow Moutai", "600519.SS"), ("ICBC", "601398.SS"),
        ("China Construction Bank", "601939.SS"), ("Ping An Insurance", "601318.SS"),
        ("Bank of China", "601988.SS"), ("PetroChina", "601857.SS"),
        ("China Yangtze Power", "600900.SS"),
    ],
    "Shenzhen Stock Exchange (SZSE)": [
        ("CATL", "300750.SZ"), ("BYD", "002594.SZ"), ("Wuliangye", "000858.SZ"),
        ("Midea Group", "000333.SZ"), ("Ping An Bank", "000001.SZ"), ("Mindray", "300760.SZ"),
    ],
    "Hong Kong Exchanges (HKEX)": [
        ("Tencent", "0700.HK"), ("Alibaba", "9988.HK"), ("AIA Group", "1299.HK"),
        ("HSBC Holdings", "0005.HK"), ("Meituan", "3690.HK"), ("Xiaomi", "1810.HK"),
        ("China Mobile", "0941.HK"), ("BYD", "1211.HK"),
    ],
    "Korea Exchange (KOSPI)": [
        ("Samsung Electronics", "005930.KS"), ("SK Hynix", "000660.KS"),
        ("LG Energy Solution", "373220.KS"), ("Hyundai Motor", "005380.KS"),
        ("Kia", "000270.KS"), ("Samsung Biologics", "207940.KS"),
    ],
    "Korea Exchange (KOSDAQ)": [
        ("Alteogen", "196170.KQ"), ("HLB", "028300.KQ"), ("EcoPro BM", "247540.KQ"),
        ("Pearl Abyss", "263750.KQ"), ("L&F", "066970.KQ"),
    ],
    "Taiwan Stock Exchange (TWSE)": [
        ("TSMC", "2330.TW"), ("Hon Hai (Foxconn)", "2317.TW"), ("MediaTek", "2454.TW"),
        ("Delta Electronics", "2308.TW"), ("Fubon Financial", "2881.TW"),
        ("Chunghwa Telecom", "2412.TW"),
    ],
    "Singapore Exchange (SGX)": [
        ("DBS Group", "D05.SI"), ("OCBC", "O39.SI"), ("UOB", "U11.SI"),
        ("Singtel", "Z74.SI"), ("Jardine Matheson", "J36.SI"), ("Keppel", "BN4.SI"),
    ],
    "Bursa Malaysia": [
        ("Maybank", "1155.KL"), ("Public Bank", "1295.KL"), ("CIMB Group", "1023.KL"),
        ("Tenaga Nasional", "5347.KL"), ("Petronas Chemicals", "5183.KL"),
        ("IHH Healthcare", "5225.KL"),
    ],
    "Indonesia Stock Exchange (IDX)": [
        ("Bank Central Asia", "BBCA.JK"), ("Bank Rakyat Indonesia", "BBRI.JK"),
        ("Bank Mandiri", "BMRI.JK"), ("Telkom Indonesia", "TLKM.JK"),
        ("Astra International", "ASII.JK"), ("Bank Negara Indonesia", "BBNI.JK"),
    ],
    "Stock Exchange of Thailand (SET)": [
        ("PTT", "PTT.BK"), ("Advanced Info Service", "ADVANC.BK"), ("Kasikornbank", "KBANK.BK"),
        ("CP All", "CPALL.BK"), ("Bangkok Bank", "BBL.BK"), ("Siam Cement", "SCC.BK"),
    ],
    "Philippine Stock Exchange (PSE)": [
        ("SM Investments", "SM.PS"), ("BDO Unibank", "BDO.PS"), ("Ayala Corp", "AC.PS"),
        ("Jollibee Foods", "JFC.PS"), ("Globe Telecom", "GLO.PS"), ("PLDT", "TEL.PS"),
    ],
    "Pakistan Stock Exchange (PSX)": [
        ("Engro", "ENGRO.KA"), ("Habib Bank", "HBL.KA"), ("Oil & Gas Development", "OGDC.KA"),
        ("Pakistan Petroleum", "PPL.KA"), ("MCB Bank", "MCB.KA"), ("Lucky Cement", "LUCK.KA"),
    ],
    "Australian Securities Exchange (ASX)": [
        ("BHP", "BHP.AX"), ("CSL", "CSL.AX"), ("Commonwealth Bank", "CBA.AX"),
        ("National Australia Bank", "NAB.AX"), ("Westpac", "WBC.AX"), ("Rio Tinto", "RIO.AX"),
        ("Fortescue", "FMG.AX"), ("Wesfarmers", "WES.AX"),
    ],
    "NZX": [
        ("Fisher & Paykel Healthcare", "FPH.NZ"), ("Auckland Airport", "AIA.NZ"),
        ("Spark New Zealand", "SPK.NZ"), ("Meridian Energy", "MEL.NZ"),
        ("Infratil", "IFT.NZ"), ("Contact Energy", "CEN.NZ"),
    ],
    "Tel Aviv Stock Exchange (TASE)": [
        ("Bank Leumi", "LUMI.TA"), ("Bank Hapoalim", "POLI.TA"), ("Elbit Systems", "ESLT.TA"),
        ("ICL Group", "ICL.TA"), ("NICE", "NICE.TA"), ("Mizrahi Tefahot", "MZTF.TA"),
    ],
    "Saudi Exchange (Tadawul)": [
        ("Saudi Aramco", "2222.SR"), ("Al Rajhi Bank", "1120.SR"),
        ("Saudi National Bank", "1180.SR"), ("SABIC", "2010.SR"),
        ("Saudi Telecom (STC)", "7010.SR"), ("Ma'aden", "1211.SR"),
    ],
    "Qatar Stock Exchange": [
        ("Qatar National Bank", "QNBK.QA"), ("Industries Qatar", "IQCD.QA"),
        ("Qatar Islamic Bank", "QIBK.QA"), ("Qatar Fuel (Woqod)", "QFLS.QA"),
        ("Nakilat", "QGTS.QA"), ("Vodafone Qatar", "VFQS.QA"),
    ],
    "Egyptian Exchange (EGX)": [
        ("Commercial International Bank", "COMI.CA"), ("Talaat Moustafa Group", "TMGH.CA"),
        ("Telecom Egypt", "ETEL.CA"), ("Abu Qir Fertilizers", "ABUK.CA"),
        ("EFG Hermes", "HRHO.CA"), ("Eastern Company", "EAST.CA"),
    ],
    "Johannesburg Stock Exchange (JSE)": [
        ("Naspers", "NPN.JO"), ("Prosus", "PRX.JO"), ("Standard Bank", "SBK.JO"),
        ("FirstRand", "FSR.JO"), ("Anglo American", "AGL.JO"), ("Sasol", "SOL.JO"),
        ("MTN Group", "MTN.JO"), ("Capitec Bank", "CPI.JO"),
    ],
}

DEFAULT_EXCHANGE = "New York Stock Exchange (NYSE)"


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

# ---------------------------------------------------------------------------
# One flat lookup of everything chartable — every exchange benchmark plus
# every listed company — so a single search box and a single chart can
# cover the whole app (TradingView-style: one chart, many ways to pick a
# symbol for it), instead of a separate chart per screen.
# ---------------------------------------------------------------------------

SYMBOL_UNIVERSE: dict[str, dict] = {}
for _, _row in EXCHANGE_TABLE.iterrows():
    SYMBOL_UNIVERSE[_row["Symbol"]] = {
        "name": _row["Benchmark"],
        "kind": "Index",
        "exchange": _row["Exchange"],
        "region": _row["Region"],
    }
for _exchange, _rows in LISTED_COMPANIES.items():
    _region = EXCHANGE_BY_NAME.get(_exchange, {}).get("Region", "")
    for _company, _sym in _rows:
        SYMBOL_UNIVERSE[_sym] = {
            "name": _company,
            "kind": "Stock",
            "exchange": _exchange,
            "region": _region,
        }

DEFAULT_ACTIVE_SYMBOL = "AAPL"

# A short, curated watchlist for the sidebar — a handful of major indices
# spanning every region, not an exhaustive list (the "Stock Exchanges"
# tables further down already cover everything).
CURATED_INDEX_WATCHLIST = ["^IXIC", "^NYA", "^FTSE", "^N225", "^HSI", "^NSEI", "^GDAXI", "^AXJO"]
DEFAULT_STOCK_WATCHLIST = ["AAPL", "MSFT", "NVDA", "TSLA", "GOOGL"]

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
    return pd.DataFrame(
        rows, columns=["Symbol", "Level", "Change %", "Day High", "Day Low", "As of"]
    )


@st.cache_data(ttl=60)
def fetch_quotes(symbols: tuple) -> pd.DataFrame:
    """One batched Yahoo call for a set of symbols: every exchange on the
    overview, or the companies on one exchange. Looking symbols up one by one
    through .info would be slow and invites rate limiting."""
    if not symbols:
        return _quotes_from_download(pd.DataFrame(), ())
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


FUNDAMENTAL_FIELDS = [
    "marketCap", "trailingPE", "forwardPE", "trailingEps", "dividendYield",
    "beta", "debtToEquity", "profitMargins", "returnOnEquity",
    "fiftyTwoWeekHigh", "fiftyTwoWeekLow", "sector", "industry", "currency",
]


@st.cache_data(ttl=300)
def fetch_company_fundamentals(symbol: str) -> dict:
    """One .info lookup for a single, user-selected company — never looped
    over a whole exchange's worth of tickers, which is what rate-limits
    Yahoo. Smaller/less-covered exchanges often return mostly blanks here;
    that's Yahoo's coverage gap, not a bug."""
    try:
        info = yf.Ticker(symbol).info or {}
    except Exception:
        info = {}
    return {field: info.get(field) for field in FUNDAMENTAL_FIELDS}


def compute_indicators(hist: pd.DataFrame) -> dict:
    """20/50/200-day moving averages, RSI(14) and MACD(12,26,9) from a price
    history frame. These are the standard free-data technical indicators —
    nothing here needs a paid feed, just enough history (a 200-day average
    needs ~200 trading days; shorter histories just show fewer dots)."""
    if hist.empty or "Close" not in hist.columns:
        return {}
    close = hist["Close"]

    sma20 = close.rolling(20).mean()
    sma50 = close.rolling(50).mean()
    sma200 = close.rolling(200).mean()

    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    rsi = 100 - (100 / (1 + rs))

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()

    return {
        "sma20": sma20,
        "sma50": sma50,
        "sma200": sma200,
        "rsi": rsi,
        "macd": macd_line,
        "macd_signal": signal_line,
        "macd_hist": macd_line - signal_line,
    }


def _fmt_num(x):
    return f"{x:,.2f}" if isinstance(x, (int, float)) and pd.notna(x) else "—"


def _fmt_signed_pct(x):
    return f"{x:+.2f}%" if isinstance(x, (int, float)) and pd.notna(x) else "—"


def _fmt_large(x, suffix: str = ""):
    """1,250,000,000 -> '1.25B'. Used for market cap, which otherwise prints
    as a wall of digits."""
    if not isinstance(x, (int, float)) or pd.isna(x):
        return "—"
    for unit, divisor in (("T", 1e12), ("B", 1e9), ("M", 1e6)):
        if abs(x) >= divisor:
            return f"{x / divisor:,.2f}{unit} {suffix}".strip()
    return f"{x:,.0f} {suffix}".strip()


def _fmt_frac_pct(x):
    """yfinance reports dividend yield / margins / ROE as a fraction
    (0.023 == 2.3%), so this multiplies by 100 before formatting."""
    return f"{x * 100:.2f}%" if isinstance(x, (int, float)) and pd.notna(x) else "—"


def generate_snapshot(name: str, stats: dict, indicators: dict, fundamentals: dict | None = None) -> str:
    """A short, rule-based readout of the numbers already on the page —
    trend, moving-average position, RSI, and (for companies) valuation.
    This is NOT AI commentary and NOT a buy/sell call: it only restates what
    the data already shows, in plain English, so it's worth double-checking
    the figures above before acting on anything here."""
    parts = []

    ret_1m = stats.get("ret_1m")
    if isinstance(ret_1m, (int, float)) and pd.notna(ret_1m):
        direction = "up" if ret_1m > 0.1 else "down" if ret_1m < -0.1 else "roughly flat"
        parts.append(f"{name} is {direction} {abs(ret_1m):.1f}% over the past month")

    price = stats.get("price")
    sma50 = indicators.get("sma50")
    sma200 = indicators.get("sma200")
    if price and isinstance(sma50, pd.Series) and not sma50.dropna().empty:
        last_sma50 = sma50.dropna().iloc[-1]
        side = "above" if price > last_sma50 else "below"
        parts.append(f"trading {side} its 50-day average")
    if price and isinstance(sma200, pd.Series) and not sma200.dropna().empty:
        last_sma200 = sma200.dropna().iloc[-1]
        side = "above" if price > last_sma200 else "below"
        parts.append(f"{side} its 200-day average")

    rsi = indicators.get("rsi")
    if isinstance(rsi, pd.Series) and not rsi.dropna().empty:
        last_rsi = rsi.dropna().iloc[-1]
        if last_rsi >= 70:
            parts.append(f"RSI is at {last_rsi:.0f}, in overbought territory")
        elif last_rsi <= 30:
            parts.append(f"RSI is at {last_rsi:.0f}, in oversold territory")
        else:
            parts.append(f"RSI is at {last_rsi:.0f}, a neutral reading")

    macd = indicators.get("macd")
    macd_signal = indicators.get("macd_signal")
    if isinstance(macd, pd.Series) and isinstance(macd_signal, pd.Series):
        m, s = macd.dropna(), macd_signal.dropna()
        if not m.empty and not s.empty:
            bias = "bullish" if m.iloc[-1] > s.iloc[-1] else "bearish"
            parts.append(f"MACD is currently {bias} (line {'above' if bias == 'bullish' else 'below'} signal)")

    if fundamentals:
        pe = fundamentals.get("trailingPE")
        if isinstance(pe, (int, float)) and pd.notna(pe):
            parts.append(f"trades at a trailing P/E of {pe:.1f}")
        div_yield = fundamentals.get("dividendYield")
        if isinstance(div_yield, (int, float)) and pd.notna(div_yield) and div_yield > 0:
            # Newer yfinance sometimes reports this already as a percent
            # rather than a fraction — treat anything over 1 as already %.
            pct = div_yield if div_yield > 1 else div_yield * 100
            parts.append(f"carries a dividend yield of {pct:.2f}%")

    if not parts:
        return "Not enough data yet to summarize this one — try again in a moment."

    sentence = ", ".join(parts)
    return sentence[0].upper() + sentence[1:] + "."


def compute_signal(price, indicators: dict) -> str:
    """A simple, transparent technical rating — "Buy" / "Sell" / "Hold" —
    built from the same SMA/RSI/MACD numbers already on the page: one vote
    each for price vs. its 50- and 200-day average, RSI overbought/oversold,
    and the MACD/signal crossover. Majority of votes wins; a tie is "Hold".
    This is a rule-based label, not a recommendation — it can't see
    news, earnings, or anything outside the price history."""
    score = 0
    votes = 0

    for key in ("sma50", "sma200"):
        series = indicators.get(key)
        if price and isinstance(series, pd.Series) and not series.dropna().empty:
            score += 1 if price > series.dropna().iloc[-1] else -1
            votes += 1

    rsi = indicators.get("rsi")
    if isinstance(rsi, pd.Series) and not rsi.dropna().empty:
        last_rsi = rsi.dropna().iloc[-1]
        if last_rsi <= 30:
            score += 1
        elif last_rsi >= 70:
            score -= 1
        votes += 1

    macd, macd_signal = indicators.get("macd"), indicators.get("macd_signal")
    if isinstance(macd, pd.Series) and isinstance(macd_signal, pd.Series):
        m, s = macd.dropna(), macd_signal.dropna()
        if not m.empty and not s.empty:
            score += 1 if m.iloc[-1] > s.iloc[-1] else -1
            votes += 1

    if votes == 0:
        return "—"
    ratio = score / votes
    if ratio >= 0.5:
        return "Buy"
    if ratio <= -0.5:
        return "Sell"
    return "Hold"


# ---------------------------------------------------------------------------
# Chart builder
# ---------------------------------------------------------------------------

def render_price_chart(
    symbol: str, period: str, interval: str, chart_type: str, show_indicators: bool = False
):
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
    indicators = compute_indicators(hist) if show_indicators else {}

    # Layout: price (+ SMA overlay) always on row 1; volume only when the
    # indicator panels are off, to keep a 4-row chart from getting cramped;
    # RSI and MACD get their own rows underneath when indicators are on.
    rows = 1
    row_heights = [1.0]
    volume_row = None
    rsi_row = None
    macd_row = None
    if show_indicators:
        rows += 2
        rsi_row, macd_row = 2, 3
        row_heights = [0.5, 0.25, 0.25]
    elif has_volume:
        rows += 1
        volume_row = 2
        row_heights = [0.75, 0.25]

    fig = make_subplots(
        rows=rows,
        cols=1,
        shared_xaxes=True,
        row_heights=row_heights,
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

    if volume_row:
        fig.add_trace(
            go.Bar(x=hist.index, y=hist["Volume"], marker_color="#4a5568", name="Volume"),
            row=volume_row,
            col=1,
        )

    if show_indicators and indicators:
        for key, color, label in (
            ("sma20", "#42a5f5", "SMA 20"),
            ("sma50", "#ab47bc", "SMA 50"),
            ("sma200", "#ffa726", "SMA 200"),
        ):
            series = indicators.get(key)
            if series is not None and not series.dropna().empty:
                fig.add_trace(
                    go.Scatter(
                        x=hist.index, y=series, mode="lines",
                        line=dict(color=color, width=1), name=label,
                    ),
                    row=1,
                    col=1,
                )

        rsi = indicators.get("rsi")
        if rsi is not None and not rsi.dropna().empty:
            fig.add_trace(
                go.Scatter(
                    x=hist.index, y=rsi, mode="lines",
                    line=dict(color="#66bb6a", width=1.2), name="RSI (14)",
                ),
                row=rsi_row,
                col=1,
            )
            fig.add_hline(y=70, line_dash="dot", line_color="#ef5350", row=rsi_row, col=1)
            fig.add_hline(y=30, line_dash="dot", line_color="#26a69a", row=rsi_row, col=1)
            fig.update_yaxes(range=[0, 100], row=rsi_row, col=1, title_text="RSI")

        macd, macd_signal, macd_hist = (
            indicators.get("macd"), indicators.get("macd_signal"), indicators.get("macd_hist"),
        )
        if macd is not None and not macd.dropna().empty:
            fig.add_trace(
                go.Bar(x=hist.index, y=macd_hist, marker_color="#4a5568", name="MACD Hist"),
                row=macd_row,
                col=1,
            )
            fig.add_trace(
                go.Scatter(x=hist.index, y=macd, mode="lines",
                            line=dict(color="#42a5f5", width=1.2), name="MACD"),
                row=macd_row,
                col=1,
            )
            fig.add_trace(
                go.Scatter(x=hist.index, y=macd_signal, mode="lines",
                            line=dict(color="#f0b429", width=1.2), name="Signal"),
                row=macd_row,
                col=1,
            )
            fig.update_yaxes(title_text="MACD", row=macd_row, col=1)

    fig.update_layout(
        height=480 if not show_indicators else 680,
        template="plotly_dark",
        paper_bgcolor="#0a0e14",
        plot_bgcolor="#0a0e14",
        showlegend=show_indicators,
        legend=dict(orientation="h", y=1.02, x=0) if show_indicators else None,
        margin=dict(l=10, r=10, t=30 if show_indicators else 10, b=10),
        xaxis_rangeslider_visible=False,
        dragmode="pan",
    )
    # TradingView-style navigation: drag to scroll/pan through history,
    # mouse wheel (or pinch, on a trackpad/touchscreen) to zoom in and out.
    st.plotly_chart(
        fig,
        width="stretch",
        config={"scrollZoom": True, "displaylogo": False},
    )


# ---------------------------------------------------------------------------
# Exchange deep-dive panel
# ---------------------------------------------------------------------------

def render_watchlist_group(title: str, symbol_keys: list, context: str):
    """A short, clickable watchlist: Name | Last | Chg%, TradingView-sidebar
    style. Clicking a row loads that symbol into the one shared chart
    (render_active_chart) via st.session_state — there's no second chart
    hiding behind each list, just different ways to pick a symbol for the
    same one."""
    st.markdown(f"**{title}**")
    symbol_keys = [s for s in dict.fromkeys(symbol_keys) if s in SYMBOL_UNIVERSE]
    if not symbol_keys:
        st.caption("No symbols configured.")
        return

    quotes = fetch_quotes(tuple(symbol_keys)).set_index("Symbol").to_dict("index")
    hcols = st.columns([3, 2, 2, 1.3])
    hcols[1].caption("Last")
    hcols[2].caption("Chg %")
    hcols[3].caption("Signal")
    for key in symbol_keys:
        meta = SYMBOL_UNIVERSE[key]
        q = quotes.get(key, {})
        row = st.columns([3, 2, 2, 1.3])
        active = key == st.session_state.get("active_symbol")
        label = f"● {meta['name']}" if active else meta["name"]
        if row[0].button(label, key=f"wl_{context}_{key}"):
            st.session_state.active_symbol = key
            st.rerun()
        row[1].write(_fmt_num(q.get("Level")))
        chg = q.get("Change %")
        chg_str = _fmt_signed_pct(chg)
        if isinstance(chg, (int, float)) and pd.notna(chg):
            row[2].markdown(f":{'green' if chg >= 0 else 'red'}[{chg_str}]")
        else:
            row[2].markdown(chg_str)

        signal = compute_signal(q.get("Level"), compute_indicators(fetch_history(key, "1y", "1d")))
        sig_color = {"Buy": "green", "Sell": "red", "Hold": "gray"}.get(signal, "gray")
        row[3].markdown(f":{sig_color}[**{signal}**]")


def render_selectable_table(view: pd.DataFrame, key: str):
    """Same idea as render_watchlist_group, for the big exchange tables:
    clicking any row loads that exchange's benchmark into the shared chart
    above. This is what makes every row in these tables — every exchange
    'marker' — clickable, without a custom HTML table or the pandas
    .style.map() coloring that caused a blank-dataframe bug here before."""
    event = st.dataframe(
        view,
        width="stretch",
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key=key,
    )
    try:
        rows = event.selection["rows"]
    except Exception:
        rows = []
    if rows:
        picked = view.iloc[rows[0]]["Symbol"]
        if picked in SYMBOL_UNIVERSE and picked != st.session_state.get("active_symbol"):
            st.session_state.active_symbol = picked
            st.rerun()


def render_active_chart():
    """The one big chart the whole app shares. Whatever symbol is active —
    picked from the search box, a watchlist row, or an exchange's company
    list — renders here: price header, chart with optional SMA/RSI/MACD,
    a plain-English key-facts readout, and (for a company) valuation
    ratios from one .info() lookup."""
    symbol = st.session_state.get("active_symbol", DEFAULT_ACTIVE_SYMBOL)
    meta = SYMBOL_UNIVERSE.get(symbol)
    if meta is None:
        symbol, meta = DEFAULT_ACTIVE_SYMBOL, SYMBOL_UNIVERSE[DEFAULT_ACTIVE_SYMBOL]
        st.session_state.active_symbol = symbol
    is_company = meta["kind"] == "Stock"

    fundamentals = fetch_company_fundamentals(symbol) if is_company else {}
    stats = fetch_exchange_stats(symbol)
    hist = fetch_history(symbol, "1y", "1d")
    indicators = compute_indicators(hist)

    change_pct = None
    if stats["price"] and stats["prev_close"]:
        change_pct = (stats["price"] - stats["prev_close"]) / stats["prev_close"] * 100

    st.markdown(f"#### {meta['name']}  ·  `{symbol}`  ·  {meta['exchange']}")

    if not hist.empty:
        last_bar = hist.dropna(subset=["Close"]).iloc[-1] if "Close" in hist.columns else None
        if last_bar is not None:
            change_str = f"{change_pct:+.2f}%" if change_pct is not None else "—"
            color = "green" if (change_pct or 0) >= 0 else "red"
            st.markdown(
                f"O **{_fmt_num(last_bar.get('Open'))}**  "
                f"H **{_fmt_num(last_bar.get('High'))}**  "
                f"L **{_fmt_num(last_bar.get('Low'))}**  "
                f"C **{_fmt_num(last_bar.get('Close'))}**  "
                f"&nbsp;&nbsp;:{color}[{change_str}]",
                unsafe_allow_html=True,
            )

    price_str = _fmt_num(stats["price"])
    st.markdown(
        f"""
        <div class="price-pill-row">
            <div class="price-pill sell"><div class="pill-label">SELL</div><div class="pill-price">{price_str}</div></div>
            <div class="price-pill buy"><div class="pill-label">BUY</div><div class="pill-price">{price_str}</div></div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.caption(
        "Display only — shows the latest price, not a live order book. "
        "Tradeloop isn't connected to a broker, so these aren't clickable "
        "trade buttons."
    )

    h1, h2, h3 = st.columns(3)
    h1.metric("Price", _fmt_num(stats["price"]), f"{change_pct:+.2f}%" if change_pct is not None else None)
    avg_vol = None
    if not hist.empty and "Volume" in hist.columns:
        recent_vol = hist["Volume"].dropna().tail(30)
        avg_vol = recent_vol.mean() if not recent_vol.empty else None
    h2.metric("Volume", f"{int(stats['volume']):,}" if stats["volume"] else "—")
    h3.metric("Avg Volume (30D)", f"{int(avg_vol):,}" if avg_vol else "—")

    c1, c2, c3 = st.columns([2, 1, 1])
    with c1:
        range_choice = st.radio(
            "Range", list(CHART_RANGES.keys()), horizontal=True, index=3, key="range_active"
        )
    with c2:
        chart_type = st.radio("Chart", ["Candlestick", "Line"], horizontal=True, key="type_active")
    with c3:
        show_indicators = st.checkbox("Indicators", value=True, key="ind_active")

    period, interval = CHART_RANGES[range_choice]
    render_price_chart(symbol, period, interval, chart_type, show_indicators)

    st.markdown("**Key Facts**")
    signal = compute_signal(stats["price"], indicators)
    sig_color = {"Buy": "green", "Sell": "red", "Hold": "gray"}.get(signal, "gray")
    st.markdown(f"Technical rating: :{sig_color}[**{signal}**]")
    st.info(generate_snapshot(meta["name"], stats, indicators, fundamentals if is_company else None))

    if is_company:
        currency = fundamentals.get("currency") or ""
        f1, f2, f3, f4 = st.columns(4)
        f1.metric("Market Cap", _fmt_large(fundamentals.get("marketCap"), currency))
        f1.metric("P/E (TTM)", _fmt_num(fundamentals.get("trailingPE")))
        f2.metric("Forward P/E", _fmt_num(fundamentals.get("forwardPE")))
        f2.metric("EPS (TTM)", _fmt_num(fundamentals.get("trailingEps")))
        f3.metric("Dividend Yield", _fmt_frac_pct(fundamentals.get("dividendYield")))
        f3.metric("Beta", _fmt_num(fundamentals.get("beta")))
        f4.metric("Profit Margin", _fmt_frac_pct(fundamentals.get("profitMargins")))
        f4.metric("Sector", fundamentals.get("sector") or "—")
        if all(fundamentals.get(f) is None for f in ("trailingPE", "marketCap", "trailingEps")):
            st.caption(
                "Yahoo Finance has little or no fundamentals data for this "
                "ticker — common for smaller/less-covered exchanges."
            )
    else:
        st.caption(f"{meta['exchange']} benchmark index — an index level, not one company's price.")

    st.caption(
        "Key Facts, the technical rating, and the indicators are all "
        "computed directly from the price/fundamentals data above — a "
        "plain-English readout, not investment advice or an AI opinion. "
        "The rating can't see news, earnings, or anything outside the "
        "price history."
    )


def render_chart_workspace():
    """Top-of-app workspace: search box + big chart on the left, a short
    clickable watchlist on the right — the TradingView-style layout, built
    from Streamlit's own widgets rather than copying TradingView's exact
    look."""
    st.session_state.setdefault("active_symbol", DEFAULT_ACTIVE_SYMBOL)

    symbol_options = sorted(SYMBOL_UNIVERSE, key=lambda s: SYMBOL_UNIVERSE[s]["name"])
    labels = {
        s: f"{SYMBOL_UNIVERSE[s]['name']} ({s}) · {SYMBOL_UNIVERSE[s]['exchange']}"
        for s in symbol_options
    }
    current = st.session_state.active_symbol
    if current not in SYMBOL_UNIVERSE:
        current = DEFAULT_ACTIVE_SYMBOL
    chosen = st.selectbox(
        "🔍 Search a symbol (index or company)",
        symbol_options,
        index=symbol_options.index(current),
        format_func=lambda s: labels[s],
    )
    if chosen != st.session_state.active_symbol:
        st.session_state.active_symbol = chosen

    main_col, side_col = st.columns([3, 1])
    with main_col:
        render_active_chart()
    with side_col:
        render_watchlist_group("INDICES", CURATED_INDEX_WATCHLIST, "idx")
        st.markdown("")
        active_meta = SYMBOL_UNIVERSE[st.session_state.active_symbol]
        if active_meta["kind"] == "Stock":
            peers = [
                sym for _, sym in LISTED_COMPANIES.get(active_meta["exchange"], [])
                if sym != st.session_state.active_symbol
            ][:7]
            stock_list = [st.session_state.active_symbol] + peers
        else:
            stock_list = DEFAULT_STOCK_WATCHLIST
        render_watchlist_group("STOCKS", stock_list, "stk")


def render_exchange_detail(name: str, table: pd.DataFrame, hide_empty: bool):
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

    r1, r2, r3, r4 = st.columns(4)
    r1.metric("1 Month", _fmt_signed_pct(s["ret_1m"]))
    r2.metric("3 Months", _fmt_signed_pct(s["ret_3m"]))
    r3.metric("6 Months", _fmt_signed_pct(s["ret_6m"]))
    r4.metric("1 Year", _fmt_signed_pct(s["ret_1y"]))

    if st.button(f"📈 View {meta['Benchmark']} on the main chart above", key=f"viewidx_{symbol}"):
        st.session_state.active_symbol = symbol
        st.rerun()

    companies = LISTED_COMPANIES.get(name, [])
    if companies:
        render_watchlist_group(
            f"Companies listed on {name}", [sym for _, sym in companies], f"exch_{symbol}"
        )
        st.caption("Click a company to load its full research panel into the chart above.")
    else:
        st.caption(
            "No companies added for this exchange yet. Add them to "
            "LISTED_COMPANIES at the top of this file."
        )

    st.markdown("**Peer Comparison**")
    peers = table[(table["Region"] == meta["Region"]) & (table["Exchange"] != name)]
    peers = peers.dropna(subset=["Level"])
    if peers.empty:
        st.caption("No other exchanges in this region are reporting data right now.")
    else:
        render_selectable_table(
            peers[["Exchange", "Benchmark", "Symbol", "Level", "Change %"]],
            key=f"peers_{symbol}",
        )
    st.caption(
        f"Peers are the other exchanges in {meta['Region']}, not a statistical "
        "peer set."
    )


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

with st.sidebar:
    st.caption(f"Signed in as **{st.session_state.get('user_email', '')}**")
    if st.button("Log out"):
        client = get_supabase_client()
        if client is not None:
            try:
                client.auth.sign_out()
            except Exception:
                pass
        for key in ("user_email", "user_id"):
            st.session_state.pop(key, None)
        st.rerun()

@st.fragment(run_every=60)
def render_dashboard():
    """Everything below the sidebar, on its own 60-second auto-refresh.
    st.fragment(run_every=...) is a native Streamlit feature — it reruns
    just this function on a timer, with no page reload and no third-party
    component, which is what streamlit-autorefresh was for. That component
    loads its own frontend JS from a separate asset bundle, which Streamlit
    Cloud's proxy/network setup sometimes can't fetch ("having trouble
    loading the streamlit_autorefresh.st_autorefresh component"); this way
    there's no extra component to fail."""
    st.title("📊 Tradeloop")
    st.caption("Global Stock Exchange Terminal")
    st.caption(
        f"Last refreshed: {datetime.now().strftime('%d %b %Y, %I:%M:%S %p')}"
        "  ·  auto-updates every 60s"
    )

    # --- Live chart workspace (search box, big chart, clickable watchlist) ---
    render_chart_workspace()

    st.divider()

    col_exchanges, col_feed = st.columns([2, 1])

    with col_exchanges:
        st.subheader("Browse by Exchange")

        quotes = fetch_quotes(tuple(EXCHANGE_TABLE["Symbol"]))
        all_exchanges = EXCHANGE_TABLE.merge(quotes, on="Symbol", how="left")
        live_count = int(all_exchanges["Level"].notna().sum())

        hide_empty = st.checkbox("Hide rows with no data", value=True)
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
                render_selectable_table(view, key=f"table_{label}")

        st.caption(
            "Click any row to load that exchange's benchmark into the chart "
            "above. Source: Yahoo Finance via yfinance — delayed quotes, fine "
            "for a dashboard, not for execution. Each exchange is shown "
            "through its benchmark index, so levels are index points, not "
            "prices."
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
            # A fixed-height, internally-scrolling box. Without this, the news
            # list (often much taller than the exchange table next to it) makes
            # the whole row that tall, leaving a big blank gap under the
            # shorter "Browse by Exchange" column once its own content ends.
            with st.container(height=600):
                for item in items:
                    st.markdown(f"**[{item['title']}]({item['link']})**")
                    if item["published"]:
                        st.caption(item["published"])
                    st.divider()

    st.divider()

    # --- Exchange deep-dive ---
    st.subheader("🔍 Exchange Deep Dive")
    by_country = sorted(EXCHANGE_BY_NAME, key=lambda n: (EXCHANGE_BY_NAME[n]["Country"], n))
    selected_exchange = st.selectbox(
        "Select an exchange",
        by_country,
        index=by_country.index(DEFAULT_EXCHANGE),
        format_func=lambda n: f"{EXCHANGE_BY_NAME[n]['Country']}  ·  {n}",
        key="deep_dive_select",
    )
    if selected_exchange:
        render_exchange_detail(selected_exchange, all_exchanges, hide_empty)


render_dashboard()
