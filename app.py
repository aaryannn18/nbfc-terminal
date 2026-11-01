"""
Tradeloop — v2
--------------
A dark, data-dense dashboard covering stock exchanges around the world, two
headline indices, RBI regulatory updates plus worldwide market news, and a
per-exchange deep-dive view (price history in multiple timeframes and chart
types, trailing returns, the companies listed on each exchange, and a regional peer table).

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
    )
    st.plotly_chart(fig, width="stretch")


# ---------------------------------------------------------------------------
# Exchange deep-dive panel
# ---------------------------------------------------------------------------

def render_companies(name: str, hide_empty: bool):
    """Companies listed on one exchange. Only fetched when that exchange is
    selected, so the page never looks up every company at once."""
    st.markdown(f"**Companies listed on {name}**")
    rows = LISTED_COMPANIES.get(name, [])
    if not rows:
        st.caption(
            "No companies added for this exchange yet. Add them to "
            "LISTED_COMPANIES at the top of this file."
        )
        return

    companies = pd.DataFrame(rows, columns=["Company", "Symbol"])
    quotes = fetch_quotes(tuple(companies["Symbol"]))
    table = companies.merge(quotes, on="Symbol", how="left")
    table = table.rename(columns={"Level": "Price"})

    reporting = int(table["Price"].notna().sum())
    st.caption(
        f"{reporting} of {len(table)} reporting  ·  prices are in each company's "
        "local currency (London quotes are in pence)"
    )
    if hide_empty:
        table = table[table["Price"].notna()]
    if table.empty:
        st.caption("Yahoo returned no quotes for these companies just now.")
        return

    st.dataframe(
        table[["Company", "Symbol", "Price", "Change %", "Day High", "Day Low", "As of"]],
        width="stretch",
        hide_index=True,
    )


def render_company_insights(company_name: str, symbol: str):
    """Full research panel for one company: snapshot sentence, valuation
    ratios, and a price chart with moving averages / RSI / MACD. Only runs
    for the single company the person picks, so it stays to one .info()
    call rather than looping every listed company."""
    fundamentals = fetch_company_fundamentals(symbol)
    stats = fetch_exchange_stats(symbol)
    hist = fetch_history(symbol, "1y", "1d")
    indicators = compute_indicators(hist)

    st.markdown(f"#### {company_name} ({symbol})")
    if fundamentals.get("sector"):
        st.caption(f"{fundamentals.get('sector', '—')} · {fundamentals.get('industry', '—')}")

    st.info(generate_snapshot(company_name, stats, indicators, fundamentals))

    currency = fundamentals.get("currency") or ""
    f1, f2, f3, f4 = st.columns(4)
    f1.metric("Market Cap", _fmt_large(fundamentals.get("marketCap"), currency))
    f1.metric("P/E (TTM)", _fmt_num(fundamentals.get("trailingPE")))
    f2.metric("Forward P/E", _fmt_num(fundamentals.get("forwardPE")))
    f2.metric("EPS (TTM)", _fmt_num(fundamentals.get("trailingEps")))
    f3.metric("Dividend Yield", _fmt_frac_pct(fundamentals.get("dividendYield")))
    f3.metric("Beta", _fmt_num(fundamentals.get("beta")))
    f4.metric("Profit Margin", _fmt_frac_pct(fundamentals.get("profitMargins")))
    f4.metric("Return on Equity", _fmt_frac_pct(fundamentals.get("returnOnEquity")))

    g1, g2, g3 = st.columns(3)
    g1.metric("Debt / Equity", _fmt_num(fundamentals.get("debtToEquity")))
    g2.metric("52-Wk High", _fmt_num(fundamentals.get("fiftyTwoWeekHigh")))
    g3.metric("52-Wk Low", _fmt_num(fundamentals.get("fiftyTwoWeekLow")))

    if all(fundamentals.get(f) is None for f in ("trailingPE", "marketCap", "trailingEps")):
        st.caption(
            "Yahoo Finance has little or no fundamentals data for this "
            "ticker — common for smaller/less-covered exchanges."
        )

    c1, c2, c3 = st.columns([2, 1, 1])
    with c1:
        range_choice = st.radio(
            "Range", list(CHART_RANGES.keys()), horizontal=True, index=3,
            key=f"range_company_{symbol}",
        )
    with c2:
        chart_type = st.radio(
            "Chart", ["Candlestick", "Line"], horizontal=True, key=f"type_company_{symbol}"
        )
    with c3:
        show_indicators = st.checkbox(
            "Indicators (SMA/RSI/MACD)", value=True, key=f"ind_company_{symbol}"
        )

    period, interval = CHART_RANGES[range_choice]
    render_price_chart(symbol, period, interval, chart_type, show_indicators)
    st.caption(
        "Snapshot and indicators are computed directly from the price/"
        "fundamentals data above — a plain-English readout, not investment "
        "advice or an AI opinion."
    )


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

    render_companies(name, hide_empty)

    c1, c2, c3 = st.columns([2, 1, 1])
    with c1:
        range_choice = st.radio(
            "Range", list(CHART_RANGES.keys()), horizontal=True, index=3, key=f"range_{symbol}"
        )
    with c2:
        chart_type = st.radio(
            "Chart", ["Candlestick", "Line"], horizontal=True, key=f"type_{symbol}"
        )
    with c3:
        show_indicators = st.checkbox(
            "Indicators (SMA/RSI/MACD)", value=False, key=f"ind_{symbol}"
        )

    period, interval = CHART_RANGES[range_choice]
    render_price_chart(symbol, period, interval, chart_type, show_indicators)

    st.info(generate_snapshot(name, s, compute_indicators(fetch_history(symbol, "1y", "1d"))))

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

    companies = LISTED_COMPANIES.get(name, [])
    if companies:
        st.markdown("**🔎 Company Insights**")
        company_names = [c[0] for c in companies]
        picked = st.selectbox(
            "Full research panel for", company_names, key=f"company_pick_{symbol}"
        )
        picked_symbol = dict(companies)[picked]
        render_company_insights(picked, picked_symbol)


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
