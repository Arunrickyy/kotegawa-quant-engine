"""
Kotegawa Quant Engine - AI Sniper Terminal v16 (hardened)
PAPER TRADING ONLY. No real orders are ever sent. Not investment advice.
"""
import datetime
import hmac
import json
import os
import time
import urllib.parse as urlparse

import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf
from kiteconnect import KiteConnect
from kiteconnect.exceptions import TokenException
from scipy.stats import norm
from streamlit_autorefresh import st_autorefresh

# =====================================================================
# 0. CONSTANTS & HELPERS
# =====================================================================
st.set_page_config(
    page_title="Kotegawa Quant Engine - AI Sniper v16",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
T_ENTRY_START = datetime.time(9, 30)
T_ENTRY_END = datetime.time(15, 0)
T_SQUARE_OFF = datetime.time(15, 20)
T_MARKET_CLOSE = datetime.time(15, 30)
COST_PER_TRADE = 40.0          # flat brokerage + charges per round trip (Rs)
WAIT_EXPIRY_MIN = 30           # unfilled limit orders are cancelled after this
RISK_FREE = 0.06

INDEX_CFG = {
    "Nifty": {"spot": "NSE:NIFTY 50", "nse": "NIFTY 50", "nfo": "NIFTY", "yf": "^NSEI", "step": 50},
    "BankNifty": {"spot": "NSE:NIFTY BANK", "nse": "NIFTY BANK", "nfo": "BANKNIFTY", "yf": "^NSEBANK", "step": 100},
    "FinNifty": {"spot": "NSE:NIFTY FIN SERVICE", "nse": "NIFTY FIN SERVICE", "nfo": "FINNIFTY", "yf": "NIFTY_FIN_SERVICE.NS", "step": 50},
    "NiftyIT": {"spot": "NSE:NIFTY IT", "nse": "NIFTY IT", "nfo": "NIFTYIT", "yf": "^CNXIT", "step": 100},
}


def now_ist():
    return datetime.datetime.now(IST)


def get_secret(name, default=""):
    try:
        return st.secrets[name]
    except Exception:
        return os.environ.get(name, default)


def show_df(df, **kwargs):
    """Works on old and new Streamlit versions (use_container_width was deprecated)."""
    try:
        st.dataframe(df, width="stretch", **kwargs)
    except TypeError:
        st.dataframe(df, use_container_width=True, **kwargs)


# =====================================================================
# 1. DEVICE SECURITY LOCK (fails closed, key lives in secrets, not code)
# =====================================================================
AUTH_KEY = str(get_secret("DEVICE_KEY", ""))
if not AUTH_KEY:
    st.error(
        "🔒 No DEVICE_KEY configured. Add DEVICE_KEY = \"your-long-random-string\" "
        "to Streamlit secrets, then open the app with ?device=your-long-random-string"
    )
    st.stop()

_device_key = str(st.query_params.get("device", ""))
if not hmac.compare_digest(_device_key.encode(), AUTH_KEY.encode()):
    st.error("🚫 Access Denied: This trading engine is restricted to authorized devices only.")
    st.stop()

# =====================================================================
# 2. PERSISTENT DAILY STATE (atomic writes, IST date)
# =====================================================================
STATE_FILE = "daily_state.json"


def today_str():
    return now_ist().date().isoformat()


def default_state():
    return {
        "date": today_str(),
        "access_token": None,
        "request_token": None,
        "paper_ledger": [],
        "audit_logs": [],
        "auto_sniper_active": False,
        "runner_mode_active": True,
    }


def load_daily_state():
    state = default_state()
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get("date") == today_str():
                state.update(data)
        except Exception:
            pass
    return state


def save_daily_state(state):
    state["date"] = today_str()
    tmp = STATE_FILE + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(state, f, indent=2, default=str)
        os.replace(tmp, STATE_FILE)  # atomic: no half-written file
    except OSError as e:
        st.warning(f"Could not save state to disk: {e}")


daily_state = load_daily_state()
ledger = daily_state["paper_ledger"]

st_autorefresh(interval=30000, key="quant_engine_live_refresh")

# =====================================================================
# 3. CSS
# =====================================================================
st.markdown(
    """
    <style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    .stDeployButton {display: none !important;}
    header[data-testid="stHeader"] {background-color: transparent !important; visibility: visible !important; height: 0px !important;}
    .stApp {background-color: #0b0f19; color: #f0f6fc;}
    .stTabs [data-baseweb="tab-list"] button [data-testid="stMarkdownContainer"] p {color: #f0f6fc !important; font-weight: 700 !important; font-size: 1.05rem !important;}
    .stTabs [data-baseweb="tab-list"] button[aria-selected="true"] [data-testid="stMarkdownContainer"] p {color: #58a6ff !important; font-weight: 800 !important;}
    label[data-testid="stWidgetLabel"] p, label[data-testid="stWidgetLabel"] span {color: #ffffff !important; font-weight: 700 !important; font-size: 0.95rem !important;}
    .terminal-banner {background: linear-gradient(135deg, #161b22 0%, #0d1117 100%); border: 1px solid #30363d; padding: 25px; border-radius: 12px; text-align: center; margin-bottom: 25px; box-shadow: 0 4px 15px rgba(0,0,0,0.6);}
    .terminal-title {color: #ffffff; font-size: 2.2rem; font-weight: 800; letter-spacing: 1px; margin-bottom: 5px;}
    .terminal-subtitle {color: #8b949e; font-size: 1.05rem; font-weight: 500;}
    .metric-card {background-color: #161b22; border: 1px solid #30363d; padding: 18px; border-radius: 10px; box-shadow: 0 2px 8px rgba(0,0,0,0.4);}
    .metric-label {color: #8b949e; font-size: 0.9rem; font-weight: 600; margin-bottom: 6px;}
    .metric-value {color: #f0f6fc; font-size: 1.25rem; font-weight: 700;}
    .stButton > button {background: linear-gradient(135deg, #238636 0%, #2ea043 100%); color: white; border: 1px solid #3fb950; font-weight: 700; border-radius: 8px; padding: 0.6rem 1rem; box-shadow: 0 3px 6px rgba(0,0,0,0.3); transition: 0.2s ease-in-out;}
    .stButton > button:hover {background: linear-gradient(135deg, #2ea043 0%, #3fb950 100%); border-color: #56d364;}
    section[data-testid="stSidebar"] {background-color: #0d1117; border-right: 1px solid #30363d;}
    section[data-testid="stSidebar"] label, section[data-testid="stSidebar"] span {color: #ffffff !important;}
    </style>
    """,
    unsafe_allow_html=True,
)

_now = now_ist()
current_date_str = _now.strftime("%d %b %Y")
current_day_str = _now.strftime("%A")

st.markdown(
    f"""
    <div class="terminal-banner">
        <div class="terminal-title">⚡ KOTEGAWA QUANT ENGINE (AI SNIPER v16)</div>
        <div class="terminal-subtitle">10-Layer Confluence Vault & Gautam Jha Confirmation Retest Engine — PAPER TRADING ONLY</div>
        <div style="color: #58a6ff; font-size: 0.95rem; margin-top: 10px;">
            📅 Live IST Session: <b>{current_day_str}, {current_date_str}</b> &nbsp;|&nbsp; Developed by <b>Arun Kummar</b>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# =====================================================================
# 4. KITE AUTH (sidebar)
# =====================================================================
st.sidebar.header("🔌 Zerodha Kite Cloud Bridge")
api_key = str(get_secret("KITE_API_KEY", ""))
api_secret = str(get_secret("KITE_API_SECRET", ""))
if api_key and api_secret:
    st.sidebar.success("🔒 Secure API Keys Loaded from Secrets")
else:
    api_key = st.sidebar.text_input("Kite API Key", type="password")
    api_secret = st.sidebar.text_input("Kite API Secret", type="password")

kite_client = None
access_token = daily_state.get("access_token")
if api_key and api_secret:
    kite = KiteConnect(api_key=api_key)
    if not access_token:
        st.sidebar.markdown(f"[🔗 Authorize Daily Kite Session]({kite.login_url()})")
        token_or_url = st.sidebar.text_input(
            "Paste Request Token or Full Redirect URL:", type="password"
        )
        if token_or_url:
            auth_ok = False
            try:
                parsed = urlparse.urlparse(token_or_url.strip())
                extracted = urlparse.parse_qs(parsed.query).get("request_token", [None])[0]
                req_token = extracted if extracted else token_or_url.strip()
                sess = kite.generate_session(req_token, api_secret=api_secret)
                daily_state["access_token"] = sess["access_token"]
                daily_state["request_token"] = req_token
                save_daily_state(daily_state)
                auth_ok = True
            except Exception as e:
                st.sidebar.error(f"Auth Error: {e}")
            if auth_ok:
                st.rerun()
    else:
        kite.set_access_token(access_token)
        kite_client = kite
        st.sidebar.success("🟢 Cloud Feed Active")
        if st.sidebar.button("🔄 Reset Kite Session"):
            daily_state["access_token"] = None
            save_daily_state(daily_state)
            st.rerun()
else:
    st.sidebar.info("💡 Configure API keys in Streamlit Secrets or the sidebar.")

st.sidebar.markdown("---")
selected_index = st.sidebar.selectbox("📊 Select Target Index", list(INDEX_CFG.keys()))
cfg = INDEX_CFG[selected_index]

fii_sentiment = st.sidebar.selectbox(
    "🏛️ FII Index Future Bias",
    ["Net Bullish (Longs)", "Net Bearish (Shorts)", "Neutral / Mixed", "Dynamic Multi-Timeframe Blend"],
    index=3,
)
min_rr = st.sidebar.slider("Min Risk-Reward Ratio", 1.0, 5.0, 4.0, 0.5)
sl_pct = st.sidebar.slider("Stop Loss Drop (%)", 10.0, 40.0, 25.0, 5.0) / 100.0
min_score = st.sidebar.slider("Min Confluence Score (%)", 60, 100, 85, 5) / 100.0
ext_ratio = st.sidebar.selectbox("Fib extension target", [1.272, 1.618, 2.618, 3.18, 3.618], index=3)
sl_buffer = st.sidebar.number_input("SL buffer beyond hammer (index pts)", 1.0, 10.0, 2.5, 0.5)

st.sidebar.markdown("---")
st.sidebar.header("🛡️ Risk Controls (Paper)")
max_trades_day = st.sidebar.number_input("Max trades per day", 1, 20, 3)
daily_loss_cap = st.sidebar.number_input("Daily loss cap (₹)", 500, 500000, 5000, 500)
use_blackout = st.sidebar.toggle("Block fixed windows 10:00-10:15 & 13:30-13:45", value=False)

st.sidebar.markdown("---")
st.sidebar.header("🤖 Automation & Runner Controls")
auto_forward_test = st.sidebar.toggle(
    "Enable Auto-Capture & Test Mode",
    value=bool(daily_state.get("auto_sniper_active", False)),
    key="auto_toggle",
)
runner_mode = st.sidebar.toggle(
    "Enable Trailing Runner Mode (SL→cost at +1R, →+1R at +2R)",
    value=bool(daily_state.get("runner_mode_active", True)),
    key="runner_toggle",
)
if (daily_state.get("auto_sniper_active") != auto_forward_test
        or daily_state.get("runner_mode_active") != runner_mode):
    daily_state["auto_sniper_active"] = auto_forward_test
    daily_state["runner_mode_active"] = runner_mode
    save_daily_state(daily_state)


# =====================================================================
# 5. DATA LAYER (cached; no fake numbers — missing data stays None)
# =====================================================================
@st.cache_data(ttl=3600, show_spinner=False)
def load_instruments(_api_key, _token, exchange):
    k = KiteConnect(api_key=_api_key)
    k.set_access_token(_token)
    return pd.DataFrame(k.instruments(exchange))


def get_option_chain(_api_key, _token, nfo_name):
    """Nearest live expiry chain (CE+PE) for the index, or None."""
    try:
        df = load_instruments(_api_key, _token, "NFO")
    except Exception:
        return None
    df = df[(df["name"] == nfo_name) & (df["instrument_type"].isin(["CE", "PE"]))].copy()
    if df.empty:
        return None
    df["expiry"] = pd.to_datetime(df["expiry"])
    cutoff = pd.Timestamp(now_ist().date())
    if now_ist().time() >= T_MARKET_CLOSE:
        cutoff += pd.Timedelta(days=1)
    df = df[df["expiry"] >= cutoff]
    if df.empty:
        return None
    nearest = df["expiry"].min()
    return df[df["expiry"] == nearest].reset_index(drop=True)


def fetch_spot(kite_obj, c):
    """Returns (price, source). Raises TokenException if the Kite token is dead."""
    if kite_obj:
        try:
            q = kite_obj.quote(c["spot"])
            return float(q[c["spot"]]["last_price"]), "Kite live"
        except TokenException:
            raise
        except Exception:
            pass
    try:
        h = yf.Ticker(c["yf"]).history(period="5d")
        if not h.empty:
            return float(h["Close"].iloc[-1]), "Yahoo (delayed)"
    except Exception:
        pass
    return None, "unavailable"


def fetch_vix(kite_obj):
    try:
        if kite_obj:
            q = kite_obj.quote("NSE:INDIA VIX")
            return float(q["NSE:INDIA VIX"]["last_price"])
    except Exception:
        pass
    try:
        h = yf.Ticker("^INDIAVIX").history(period="5d")
        if not h.empty:
            return float(h["Close"].iloc[-1])
    except Exception:
        pass
    return None


@st.cache_data(ttl=25, show_spinner=False)
def fetch_mtf(_api_key, _token, nse_symbol):
    try:
        k = KiteConnect(api_key=_api_key)
        k.set_access_token(_token)
        inst = load_instruments(_api_key, _token, "NSE")
        m = inst[inst["tradingsymbol"] == nse_symbol]
        if m.empty:
            return {"error": f"{nse_symbol} not found in instruments"}
        tok = int(m.iloc[0]["instrument_token"])
        to_dt = now_ist().replace(tzinfo=None)
        from_dt = to_dt - datetime.timedelta(days=6)
        out = {}
        for key, interval in [("1m", "minute"), ("3m", "3minute"), ("5m", "5minute"),
                              ("15m", "15minute"), ("60m", "60minute")]:
            out[key] = k.historical_data(tok, from_dt, to_dt, interval)
            time.sleep(0.35)  # Kite historical API: ~3 req/sec limit
        return out
    except Exception as e:
        return {"error": str(e)}


@st.cache_data(ttl=25, show_spinner=False)
def fetch_fut_candles(_api_key, _token, nfo_name):
    """5-min candles (with volume) of the nearest index future. Used for POC and the volume layer."""
    try:
        k = KiteConnect(api_key=_api_key)
        k.set_access_token(_token)
        df = load_instruments(_api_key, _token, "NFO")
        f = df[(df["name"] == nfo_name) & (df["instrument_type"] == "FUT")].copy()
        if f.empty:
            return {"error": "no futures contract found"}
        f["expiry"] = pd.to_datetime(f["expiry"])
        f = f[f["expiry"] >= pd.Timestamp(now_ist().date())]
        if f.empty:
            return {"error": "no live futures expiry"}
        row = f.sort_values("expiry").iloc[0]
        to_dt = now_ist().replace(tzinfo=None)
        time.sleep(0.4)
        cand = k.historical_data(int(row["instrument_token"]), to_dt - datetime.timedelta(days=6), to_dt, "5minute")
        return {"symbol": row["tradingsymbol"], "candles": cand}
    except Exception as e:
        return {"error": str(e)}


def compute_pcr(kite_obj, chain, spot, step):
    """Returns (overall PCR over ATM±10 strikes, near-ATM PCR over ±3 strikes) from live OI."""
    try:
        atm = round(spot / step) * step
        sel = chain[(chain["strike"] >= atm - 10 * step) & (chain["strike"] <= atm + 10 * step)].copy()
        if sel.empty:
            return None, None
        q = kite_obj.quote([f"NFO:{s}" for s in sel["tradingsymbol"]])
        sel["oi"] = [q.get(f"NFO:{s}", {}).get("oi", 0) for s in sel["tradingsymbol"]]

        def ratio(d):
            ce = d[d["instrument_type"] == "CE"]["oi"].sum()
            pe = d[d["instrument_type"] == "PE"]["oi"].sum()
            return round(pe / ce, 2) if ce > 0 else None

        near = sel[(sel["strike"] - atm).abs() <= 3 * step]
        return ratio(sel), ratio(near)
    except Exception:
        return None, None


# ---------- candle helpers ----------
def to_df(candles):
    if not candles:
        return pd.DataFrame()
    df = pd.DataFrame(candles)
    df["date"] = pd.to_datetime(df["date"])
    return df


def session_df(candles):
    df = to_df(candles)
    if df.empty:
        return df
    return df[df["date"].dt.date == now_ist().date()]


def compute_levels(candles_5m):
    """Today's swing high/low, TWAP proxy, and previous-day pivots."""
    out = {"swing_high": None, "swing_low": None, "twap": None, "prev": None}
    df = to_df(candles_5m)
    if df.empty:
        return out
    today = now_ist().date()
    td = df[df["date"].dt.date == today]
    if len(td) >= 6:
        out["swing_high"] = float(td["high"].max())
        out["swing_low"] = float(td["low"].min())
        out["twap"] = float(((td["high"] + td["low"] + td["close"]) / 3).mean())
    prev_days = sorted(d for d in df["date"].dt.date.unique() if d < today)
    if prev_days:
        pdx = df[df["date"].dt.date == prev_days[-1]]
        out["prev"] = {
            "high": float(pdx["high"].max()),
            "low": float(pdx["low"].min()),
            "close": float(pdx["close"].iloc[-1]),
            "date": str(prev_days[-1]),
        }
    return out


def detect_candlestick_patterns(candles):
    """Takes CLOSED candles (list of dicts). Returns (pattern, bias) bias in bull/bear/neutral."""
    if not candles or len(candles) < 3:
        return "Insufficient data", "neutral"
    c2, c3 = candles[-2], candles[-1]
    body = abs(c3["close"] - c3["open"])
    rng = c3["high"] - c3["low"]
    lower = min(c3["open"], c3["close"]) - c3["low"]
    upper = c3["high"] - max(c3["open"], c3["close"])

    if rng > 0 and lower >= 2.0 * body and upper < body:
        return "Bullish Hammer / Rejection Pin Bar", "bull"
    if rng > 0 and upper >= 2.0 * body and lower < body:
        return "Bearish Shooting Star / Rejection Wick", "bear"
    if (c3["close"] > c3["open"] and c2["close"] < c2["open"]
            and c3["close"] >= c2["open"] and c3["open"] <= c2["close"]):
        return "Bullish Engulfing Pattern", "bull"
    if (c3["close"] < c3["open"] and c2["close"] > c2["open"]
            and c3["close"] <= c2["open"] and c3["open"] >= c2["close"]):
        return "Bearish Engulfing Pattern", "bear"
    if c3["high"] <= c2["high"] and c3["low"] >= c2["low"]:
        return "Inside Bar Consolidation (Breakout Pending)", "neutral"
    return "Standard Price Action (No Signal)", "neutral"


def check_gautam_jha_retest_confirmation(candles, zone, trade_type, tol=0.0015):
    """STRICT: True only if previous candle tested the zone, closed in trade direction,
    and the live candle is breaking its high (CE) / low (PE)."""
    if not candles or len(candles) < 3 or not zone:
        return False, "No data / no retest zone"
    prev, cur = candles[-2], candles[-1]
    lo, hi = zone * (1 - tol), zone * (1 + tol)
    if trade_type == "CE":
        touched = lo <= prev["low"] <= hi
        confirm = prev["close"] > prev["open"]
        brk = cur["close"] > prev["high"]
    else:
        touched = lo <= prev["high"] <= hi
        confirm = prev["close"] < prev["open"]
        brk = cur["close"] < prev["low"]
    if touched and confirm and brk:
        return True, f"✅ GAUTAM JHA CONFIRMED: {'Green' if trade_type == 'CE' else 'Red'} retest breakout"
    missing = [n for n, ok in [("zone touch", touched), ("confirmation candle", confirm), ("breakout", brk)] if not ok]
    return False, "⏳ Waiting for: " + ", ".join(missing)


def fib_golden_ok(candles, hi, lo, ce, lookback=12):
    """True if any of the last `lookback` 5-min candles today touched the 50%-78.6% pocket."""
    if hi is None or lo is None or hi <= lo or not candles:
        return None
    d = hi - lo
    zl, zh = (hi - 0.786 * d, hi - 0.5 * d) if ce else (lo + 0.5 * d, lo + 0.786 * d)
    today = now_ist().date()
    recent = [c for c in candles if c["date"].date() == today][-lookback:]
    if not recent:
        return None
    return any(c["low"] <= zh and c["high"] >= zl for c in recent)


def determine_dynamic_trade_bias(mtf, pcr_val, fii_bias):
    """Direction comes from the HIGHER timeframes (15m + 60m must agree).
    Returns 'CE', 'PE' or None (stand aside)."""
    if not mtf or "error" in mtf:
        return None

    def tf_dir(key):
        c = (mtf.get(key) or [])[:-1]  # closed candles only
        if len(c) < 3:
            return 0
        d = c[-1]["close"] - c[-3]["close"]
        return 1 if d > 0 else -1 if d < 0 else 0

    total = tf_dir("15m") + tf_dir("60m")
    dirn = "CE" if total == 2 else "PE" if total == -2 else None
    if fii_bias == "Net Bullish (Longs)" and dirn == "PE":
        return None
    if fii_bias == "Net Bearish (Shorts)" and dirn == "CE":
        return None
    return dirn


def smc_analyze(candles, n=2, min_gap_pct=0.0003):
    """View-only Smart Money Concepts read on CLOSED candles (list of dicts).
    Finds confirmed swings, BOS / CHoCH (close beyond last swing), liquidity sweeps
    (wick beyond swing, close back inside) and unfilled fair value gaps (3-candle gaps)."""
    m = len(candles)
    res = {"events": [], "fvgs": [], "trend": 0, "m": m}
    if m < 12:
        return res

    def tstr(k):
        return candles[k]["date"].strftime("%d %b %H:%M")

    swing_at = {}  # confirmation index -> [(kind, price)]
    for i in range(n, m - n):
        h, l = candles[i]["high"], candles[i]["low"]
        if (h > max(c["high"] for c in candles[i - n:i])
                and h > max(c["high"] for c in candles[i + 1:i + n + 1])):
            swing_at.setdefault(i + n, []).append(("H", h))
        if (l < min(c["low"] for c in candles[i - n:i])
                and l < min(c["low"] for c in candles[i + 1:i + n + 1])):
            swing_at.setdefault(i + n, []).append(("L", l))

    last_h = last_l = None
    trend = 0
    for k in range(m):
        for kind, price in swing_at.get(k, []):
            if kind == "H":
                last_h = {"p": price, "done": False, "swept": False}
            else:
                last_l = {"p": price, "done": False, "swept": False}
        c = candles[k]
        if last_h and not last_h["done"]:
            if c["close"] > last_h["p"]:
                res["events"].append({"k": k, "kind": "CHoCH" if trend == -1 else "BOS", "dir": "bull",
                                      "level": last_h["p"], "time": tstr(k)})
                trend, last_h["done"] = 1, True
            elif c["high"] > last_h["p"] and not last_h["swept"]:
                res["events"].append({"k": k, "kind": "Sweep", "dir": "bear",
                                      "level": last_h["p"], "time": tstr(k)})
                last_h["swept"] = True
        if last_l and not last_l["done"]:
            if c["close"] < last_l["p"]:
                res["events"].append({"k": k, "kind": "CHoCH" if trend == 1 else "BOS", "dir": "bear",
                                      "level": last_l["p"], "time": tstr(k)})
                trend, last_l["done"] = -1, True
            elif c["low"] < last_l["p"] and not last_l["swept"]:
                res["events"].append({"k": k, "kind": "Sweep", "dir": "bull",
                                      "level": last_l["p"], "time": tstr(k)})
                last_l["swept"] = True
    res["trend"] = trend

    for i in range(2, m):
        a, c3 = candles[i - 2], candles[i]
        if a["date"].date() != c3["date"].date():
            continue  # ignore overnight gaps
        gap_min = min_gap_pct * c3["close"]
        if c3["low"] > a["high"] and (c3["low"] - a["high"]) >= gap_min:
            bottom, top, d = a["high"], c3["low"], "bull"
            filled = any(x["low"] <= bottom for x in candles[i + 1:])
        elif c3["high"] < a["low"] and (a["low"] - c3["high"]) >= gap_min:
            bottom, top, d = c3["high"], a["low"], "bear"
            filled = any(x["high"] >= top for x in candles[i + 1:])
        else:
            continue
        if not filled:
            res["fvgs"].append({"i": i, "dir": d, "bottom": bottom, "top": top, "time": tstr(i)})
    return res


def smc_sequence(res, direction, spot, lookback=24):
    """Checks the entry sequence for CE (bull) or PE (bear): sweep -> structure break -> FVG -> price back in FVG."""
    want = "bull" if direction == "CE" else "bear"
    steps = {"1 Liquidity sweep (last 2h)": False, "2 Structure break after sweep": False,
             "3 Unfilled fair value gap left": False, "4 Price back inside the gap": False}
    sweeps = [e for e in res["events"] if e["kind"] == "Sweep" and e["dir"] == want
              and res["m"] - 1 - e["k"] <= lookback]
    if not sweeps:
        return steps
    ks = sweeps[-1]["k"]
    keys = list(steps)
    steps[keys[0]] = True
    steps[keys[1]] = any(e["kind"] in ("BOS", "CHoCH") and e["dir"] == want and e["k"] > ks for e in res["events"])
    fv = [f for f in res["fvgs"] if f["dir"] == want and f["i"] >= ks]
    steps[keys[2]] = bool(fv)
    steps[keys[3]] = any(f["bottom"] <= spot <= f["top"] for f in fv)
    return steps


# ---------- GTI / Gautam Jha spec helpers ----------
def zone_touched(candles, zl, zh, lookback=12):
    """True if any of the last `lookback` 5-min candles today overlapped the 0.618 zone."""
    if zl is None or not candles:
        return None
    today = now_ist().date()
    recent = [c for c in candles if c["date"].date() == today][-lookback:]
    if not recent:
        return None
    return any(c["low"] <= zh and c["high"] >= zl for c in recent)


def compute_poc(candles, bin_size):
    """Today's session Point of Control (price bin with the most traded volume), futures price terms."""
    today = now_ist().date()
    td = [c for c in candles if c["date"].date() == today and c.get("volume", 0) > 0]
    if len(td) < 6:
        return None
    vol = {}
    for c in td:
        lo_b, hi_b = int(c["low"] // bin_size), int(c["high"] // bin_size)
        nb = hi_b - lo_b + 1
        for b in range(lo_b, hi_b + 1):
            vol[b] = vol.get(b, 0) + c["volume"] / nb
    best = max(vol, key=vol.get)
    return (best + 0.5) * bin_size


def volume_ratio(fut_candles, ts):
    """Volume of the candle at timestamp `ts` divided by the 20-candle average volume before it."""
    if not fut_candles:
        return None
    idx = next((i for i, c in enumerate(fut_candles) if c["date"] == ts), None)
    if idx is None or idx < 20:
        return None
    sma = sum(c["volume"] for c in fut_candles[idx - 20: idx]) / 20
    return fut_candles[idx]["volume"] / sma if sma > 0 else None


def check_weak_hammer_trigger(candles, zl, zh, trade_type):
    """Layer 6. Last CLOSED candle must be a small-body rejection candle (wick >= 2x body) whose
    wick-extreme sits inside the 0.618 zone; the live candle must then break its high (CE) / low (PE).
    Returns (ok, message, hammer_info_or_None)."""
    if not candles or len(candles) < 3 or zl is None:
        return False, "No data / no 0.618 zone yet", None
    h, cur = candles[-2], candles[-1]
    rng = h["high"] - h["low"]
    if rng <= 0:
        return False, "⏳ Waiting for: hammer candle", None
    body = abs(h["close"] - h["open"])
    lower = min(h["open"], h["close"]) - h["low"]
    upper = h["high"] - max(h["open"], h["close"])
    small = body <= 0.35 * rng
    if trade_type == "CE":
        wick_ok, in_zone, brk = lower >= 2 * body and lower > upper, zl <= h["low"] <= zh, cur["close"] > h["high"]
    else:
        wick_ok, in_zone, brk = upper >= 2 * body and upper > lower, zl <= h["high"] <= zh, cur["close"] < h["low"]
    info = {"low": h["low"], "high": h["high"], "date": h["date"]} if (small and wick_ok and in_zone) else None
    if info and brk:
        return True, f"✅ WEAK HAMMER in 0.618 zone + live candle broke its {'high' if trade_type == 'CE' else 'low'}", info
    missing = [n for n, ok in [("small body", small), ("2x rejection wick", wick_ok),
                               ("hammer inside 0.618 zone", in_zone), ("live break of hammer", brk)] if not ok]
    return False, "⏳ Waiting for: " + ", ".join(missing), info


def compute_fib_trade_params(spot, ttype, hammer, hi, lo, ratio, buffer, K, T, iv, ltp):
    """SL = hammer extreme +/- buffer; target = Fibonacci extension of today's swing projected from the hammer.
    Index levels are converted to option prices by re-pricing with Black-Scholes at the solved IV."""
    if not hammer or hi is None or lo is None or hi <= lo or iv is None:
        return None
    d = hi - lo
    if ttype == "CE":
        sl_idx, tgt_idx = hammer["low"] - buffer, hammer["low"] + ratio * d
        risk, reward = spot - sl_idx, tgt_idx - spot
    else:
        sl_idx, tgt_idx = hammer["high"] + buffer, hammer["high"] - ratio * d
        risk, reward = sl_idx - spot, spot - tgt_idx
    if risk <= 0 or reward <= 0 or tgt_idx <= 0:
        return None
    sig = iv / 100
    base = bs_price(spot, K, T, RISK_FREE, sig, ttype)
    sl_opt = max(0.05, ltp + bs_price(sl_idx, K, T, RISK_FREE, sig, ttype) - base)
    tgt_opt = ltp + bs_price(tgt_idx, K, T, RISK_FREE, sig, ttype) - base
    entry = round(ltp * 0.98, 2)
    rr_opt = (tgt_opt - entry) / (entry - sl_opt) if entry > sl_opt else None
    return {"sl_idx": sl_idx, "tgt_idx": tgt_idx, "rr_idx": reward / risk, "entry": entry,
            "sl": round(sl_opt, 2), "tgt": round(tgt_opt, 2), "rr_opt": rr_opt}


# ---------- Smart Money Concepts (VIEW-ONLY, not used in trade score) ----------
def find_swings(c, left=2, right=2):
    """Fractal swing highs/lows: list of (index, 'H'|'L', price). Confirmed only `right` candles later."""
    sw = []
    for i in range(left, len(c) - right):
        if (all(c[i]["high"] > c[j]["high"] for j in range(i - left, i))
                and all(c[i]["high"] >= c[j]["high"] for j in range(i + 1, i + right + 1))):
            sw.append((i, "H", c[i]["high"]))
        if (all(c[i]["low"] < c[j]["low"] for j in range(i - left, i))
                and all(c[i]["low"] <= c[j]["low"] for j in range(i + 1, i + right + 1))):
            sw.append((i, "L", c[i]["low"]))
    return sw


def _structure(sw):
    H = [x for x in sw if x[1] == "H"]
    L = [x for x in sw if x[1] == "L"]
    if len(H) < 2 or len(L) < 2:
        return "range"
    hh, hl = H[-1][2] > H[-2][2], L[-1][2] > L[-2][2]
    if hh and hl:
        return "bull"
    if (not hh) and (not hl):
        return "bear"
    return "range"


def analyze_smc(c, spot, hi, lo, right=2, lookback=12):
    """c = CLOSED 5-min candles. Detects structure, BOS/CHoCH, liquidity sweep, unfilled FVG, premium/discount."""
    out = {"trend": "NA", "break": None, "sweep": None, "fvg_bull": None, "fvg_bear": None, "zone": None}
    if not c or len(c) < 20:
        return out
    c = c[-80:]
    n = len(c)
    sw = find_swings(c, 2, right)
    out["trend"] = _structure(sw)
    for k in range(max(n - lookback, 3), n):
        vis = [x for x in sw if x[0] + right <= k - 1]
        SH = [x for x in vis if x[1] == "H"]
        SL = [x for x in vis if x[1] == "L"]
        if not SH or not SL:
            continue
        sh, sl, ago, before = SH[-1][2], SL[-1][2], n - 1 - k, _structure(vis)
        if c[k]["close"] > sh and c[k - 1]["close"] <= sh:
            out["break"] = {"type": "CHoCH" if before == "bear" else "BOS", "dir": "bull", "ago": ago, "level": sh}
        elif c[k]["close"] < sl and c[k - 1]["close"] >= sl:
            out["break"] = {"type": "CHoCH" if before == "bull" else "BOS", "dir": "bear", "ago": ago, "level": sl}
        if c[k]["low"] < sl and c[k]["close"] > sl:
            out["sweep"] = {"dir": "bull", "ago": ago, "level": sl}   # sell-side liquidity taken
        elif c[k]["high"] > sh and c[k]["close"] < sh:
            out["sweep"] = {"dir": "bear", "ago": ago, "level": sh}   # buy-side liquidity taken
    min_gap = (spot or 0) * 0.0002
    for i in range(max(0, n - 40), n - 2):
        a, d = c[i], c[i + 2]
        if d["low"] - a["high"] > min_gap:
            zl, zh = a["high"], d["low"]
            if not any(c[j]["low"] <= zl for j in range(i + 3, n)):
                out["fvg_bull"] = (zl, zh, n - 1 - (i + 2))
        if a["low"] - d["high"] > min_gap:
            zl, zh = d["high"], a["low"]
            if not any(c[j]["high"] >= zh for j in range(i + 3, n)):
                out["fvg_bear"] = (zl, zh, n - 1 - (i + 2))
    if hi and lo and hi > lo and spot:
        out["zone"] = "discount" if spot < (hi + lo) / 2 else "premium"
    return out


def smc_checklist(smc, spot, trade_type):
    want = "bull" if trade_type == "CE" else "bear"
    fvg = smc["fvg_bull"] if trade_type == "CE" else smc["fvg_bear"]
    in_fvg = bool(fvg and spot and fvg[0] * 0.999 <= spot <= fvg[1] * 1.001)
    return {
        "sweep": bool(smc["sweep"] and smc["sweep"]["dir"] == want),
        "break": bool(smc["break"] and smc["break"]["dir"] == want),
        "fvg": in_fvg,
        "zone": smc["zone"] == ("discount" if trade_type == "CE" else "premium"),
    }


def tf_trend(mtf, key):
    """+1 bullish, -1 bearish, 0 flat/no data (last 3 closed candles of that timeframe)."""
    if not mtf or "error" in mtf:
        return 0
    c = (mtf.get(key) or [])[:-1]
    if len(c) < 3:
        return 0
    d = c[-1]["close"] - c[-3]["close"]
    return 1 if d > 0 else -1 if d < 0 else 0


def display_bias(mtf, pcr_val):
    """View-only direction (all 5 timeframes, majority vote). Never used to place trades."""
    if not mtf or "error" in mtf:
        return None
    bull = bear = 0
    for k in ["1m", "3m", "5m", "15m", "60m"]:
        c = (mtf.get(k) or [])[:-1]
        if len(c) >= 3:
            if c[-1]["close"] - c[-3]["close"] > 0:
                bull += 1
            else:
                bear += 1
    if bull != bear:
        return "CE" if bull > bear else "PE"
    if pcr_val is not None:
        return "CE" if pcr_val >= 1.0 else "PE"
    return None


# ---------- Black-Scholes ----------
def bs_price(S, K, T, r, sigma, opt):
    T = max(T, 1e-6)
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    if opt == "CE":
        return S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
    return K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)


def implied_vol(price, S, K, T, r, opt):
    """Bisection (robust). Returns IV in % or None if price is outside model bounds."""
    if price is None or price <= 0 or T <= 0:
        return None
    lo, hi = 0.01, 3.0
    if price < bs_price(S, K, T, r, lo, opt) or price > bs_price(S, K, T, r, hi, opt):
        return None
    for _ in range(60):
        mid = (lo + hi) / 2
        if bs_price(S, K, T, r, mid, opt) > price:
            hi = mid
        else:
            lo = mid
    return round(((lo + hi) / 2) * 100, 2)


def calculate_greeks(S, K, T, r, sigma, opt):
    T = max(T, 1e-4)
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    base = -(S * norm.pdf(d1) * sigma) / (2 * np.sqrt(T))
    if opt == "CE":
        delta = norm.cdf(d1)
        theta = (base - r * K * np.exp(-r * T) * norm.cdf(d2)) / 365
    else:
        delta = norm.cdf(d1) - 1
        theta = (base + r * K * np.exp(-r * T) * norm.cdf(-d2)) / 365
    gamma = norm.pdf(d1) / (S * sigma * np.sqrt(T))
    vega = (S * norm.pdf(d1) * np.sqrt(T)) / 100
    return round(delta, 2), round(theta, 2), round(gamma, 4), round(vega, 2)


def calculate_sniper_parameters(ltp, sl_drop_pct, rr):
    entry = round(float(ltp) * 0.98, 2)
    sl = round(entry * (1.0 - sl_drop_pct), 2)
    target = round(entry + (entry - sl) * rr, 2)
    return entry, sl, target


def check_economic_calendar_blackout(t):
    return (t.hour == 10 and t.minute <= 15) or (t.hour == 13 and 30 <= t.minute <= 45)


# =====================================================================
# 6. CONFLUENCE ENGINE (real checks; unavailable layers are N/A, not auto-PASS)
# =====================================================================
LAYER_WEIGHTS = {
    "Layer 2 (Macro Trend & PCR)": 0.11,
    "Layer 3 (VWAP/TWAP Anchor)": 0.11,
    "Layer 4 (POC / FVG Confluence)": 0.11,
    "Layer 5 (0.618 Golden Pocket Touch)": 0.11,
    "Layer 6 (Weak Hammer + Gautam Jha Break)": 0.11,
    "Layer 7 (GTI Volume ≥ 1.5x SMA20)": 0.10,
    "Layer 8 (Near-ATM OI Imbalance)": 0.10,
    "Layer 9 (Greeks: Delta & IV)": 0.11,
    "Layer 10 (Sector Harmony & Momentum)": 0.14,
}


def evaluate_confluence(md, trade_type, threshold):
    audit = {}
    n = now_ist()
    in_window = n.weekday() < 5 and T_ENTRY_START <= n.time() <= T_ENTRY_END
    audit["Gate 1 (Trading Window 09:30-15:00 IST)"] = "PASS" if in_window else f"FAIL ({n.strftime('%a %H:%M')})"
    if not in_window:
        return 0.0, audit, "HOLD: Outside entry window (Mon–Fri 09:30–15:00 IST)"
    audit["Gate 2 (Stop-Loss Shield)"] = "PASS" if md["sl_valid"] else "FAIL"
    if not md["sl_valid"]:
        return 0.0, audit, "HOLD: Invalid Stop-Loss"
    rr = md.get("rr")
    if rr is not None:
        rr_ok = rr >= md["min_rr"]
        audit[f"Gate 3 (Fib R:R >= 1:{md['min_rr']})"] = f"{'PASS' if rr_ok else 'FAIL'} (1:{rr:.1f})"
        if not rr_ok:
            return 0.0, audit, f"HOLD: Fib target gives R:R 1:{rr:.1f}, below 1:{md['min_rr']}"

    ce = trade_type == "CE"
    pcr, near = md.get("pcr"), md.get("pcr_near")
    layers = {
        "Layer 2 (Macro Trend & PCR)": None if pcr is None else (pcr >= 0.90 if ce else pcr <= 1.25),
        "Layer 3 (VWAP/TWAP Anchor)": None if md.get("twap") is None else ((md["spot"] > md["twap"]) if ce else (md["spot"] < md["twap"])),
        "Layer 4 (POC / FVG Confluence)": (md["fvg_in"] if md.get("poc_aligned") is None
                                           else (md["poc_aligned"] or md["fvg_in"])),
        "Layer 5 (0.618 Golden Pocket Touch)": md.get("fib_ok"),
        "Layer 6 (Weak Hammer + Gautam Jha Break)": md["retest_ok"],
        "Layer 7 (GTI Volume ≥ 1.5x SMA20)": None if md.get("vol_ratio") is None else md["vol_ratio"] >= 1.5,
        "Layer 8 (Near-ATM OI Imbalance)": None if near is None else (near >= 0.92 if ce else near <= 1.28),
        "Layer 9 (Greeks: Delta & IV)": md.get("greeks_ok"),
        "Layer 10 (Sector Harmony & Momentum)": None,  # not implemented -> not evaluable
    }
    total_w = sum(LAYER_WEIGHTS.values())
    eval_w = passed_w = 0.0
    for name, status in layers.items():
        if status is None:
            audit[name] = "N/A (no data)"
            continue
        eval_w += LAYER_WEIGHTS[name]
        if status:
            passed_w += LAYER_WEIGHTS[name]
        audit[name] = "PASS" if status else "FAIL"

    if eval_w / total_w < 0.60:
        return 0.0, audit, "HOLD: Not enough live data to evaluate layers"
    score = passed_w / eval_w
    if not layers["Layer 6 (Weak Hammer + Gautam Jha Break)"]:
        return score, audit, "HOLD: Weak hammer + Gautam Jha break not met (mandatory)"
    if score >= threshold:
        return score, audit, f"EXECUTE_{trade_type}_SNIPER (Score {score * 100:.1f}%)"
    return score, audit, f"HOLD: Score {score * 100:.1f}% below {threshold * 100:.0f}% threshold"


# =====================================================================
# 7. PAPER LEDGER LOGIC
# =====================================================================
CLOSED_STATES = {"TARGET", "SL", "RUNNER", "EOD", "EXPIRED"}


def normalize_trade(t):
    """Upgrade old-schema rows."""
    if "State" not in t:
        s = t.get("Status", "")
        t["State"] = ("TARGET" if "TARGET" in s else "SL" if "STOP" in s
                      else "RUNNER" if "RUNNER" in s else "OPEN" if "FILLED" in s else "WAITING")
    t.setdefault("Exit Price", None)
    t.setdefault("Lot", None)
    t.setdefault("CreatedEpoch", time.time())
    return t


STATUS_TEXT = {
    "WAITING": "⏳ WAITING FOR RETEST",
    "OPEN": "🟢 FILLED / OPEN",
    "TARGET": "🎯 TARGET ACHIEVED",
    "SL": "🛑 STOP LOSS HIT",
    "RUNNER": "🏃 RUNNER EXIT (Trailed SL)",
    "EOD": "🔔 EOD SQUARE-OFF",
    "EXPIRED": "⌛ EXPIRED (Not Filled)",
}


def process_ledger(trades, kite_obj, default_lot, runner):
    """Updates every trade once per run. Closed trades are frozen (exit price locked)."""
    for t in trades:
        normalize_trade(t)
    live = {}
    active_syms = [t["Symbol"] for t in trades if t["State"] in ("WAITING", "OPEN")]
    if kite_obj and active_syms:
        try:
            q = kite_obj.quote([f"NFO:{s}" for s in set(active_syms)])
            for s in set(active_syms):
                if f"NFO:{s}" in q:
                    live[s] = float(q[f"NFO:{s}"]["last_price"])
        except Exception:
            pass

    changed = False
    n = now_ist()
    for t in trades:
        if t["State"] in CLOSED_STATES:
            continue
        ltp = live.get(t["Symbol"], t.get("Current LTP"))
        if ltp is None:
            continue
        if ltp != t.get("Current LTP"):
            t["Current LTP"] = ltp
            changed = True

        if t["State"] == "WAITING":
            stale = (time.time() - t["CreatedEpoch"]) > WAIT_EXPIRY_MIN * 60
            if stale or n.time() >= T_SQUARE_OFF:
                t["State"], changed = "EXPIRED", True
            elif ltp <= t["Entry Limit"]:
                t["State"], t["Fill Time"], changed = "OPEN", n.strftime("%H:%M:%S"), True
            else:
                continue

        if t["State"] == "OPEN":
            risk = t["Entry Limit"] - t["Original SL"]
            if runner and risk > 0:
                if ltp >= t["Entry Limit"] + 2 * risk:
                    new_sl = max(t["Stop Loss"], round(t["Entry Limit"] + risk, 2))
                elif ltp >= t["Entry Limit"] + risk:
                    new_sl = max(t["Stop Loss"], t["Entry Limit"])
                else:
                    new_sl = t["Stop Loss"]
                if new_sl != t["Stop Loss"]:
                    t["Stop Loss"], changed = new_sl, True
            if ltp >= t["Target"]:
                t["State"], t["Exit Price"], changed = "TARGET", ltp, True
            elif ltp <= t["Stop Loss"]:
                trailed = t["Stop Loss"] >= t["Entry Limit"]
                t["State"], t["Exit Price"], changed = ("RUNNER" if trailed else "SL"), ltp, True
            elif n.time() >= T_SQUARE_OFF:
                t["State"], t["Exit Price"], changed = "EOD", ltp, True
    for t in trades:
        t["Status"] = STATUS_TEXT.get(t["State"], t["State"])

    rows, wins, losses, total_pnl = [], 0, 0, 0.0
    for t in trades:
        lot = t.get("Lot") or default_lot or 1
        px = t["Exit Price"] if t["Exit Price"] is not None else t.get("Current LTP")
        if t["State"] == "WAITING":
            pnl_txt = "—"
        elif t["State"] == "EXPIRED":
            pnl_txt = "₹0.00 (cancelled)"
        else:
            pnl = round((px - t["Entry Limit"]) * lot - COST_PER_TRADE, 2)
            pnl_txt = f"₹{pnl:,.2f}"
            if t["State"] in CLOSED_STATES:
                total_pnl += pnl
                wins += pnl > 0
                losses += pnl <= 0
        rows.append({
            "Time": t["Timestamp"], "Symbol": t["Symbol"], "Type": t["Type"],
            "Entry Limit (₹)": t["Entry Limit"], "Live/Exit LTP (₹)": px,
            "Stop Loss (₹)": t["Stop Loss"], f"Target (1:{min_rr})": t["Target"],
            "Net P&L (₹)": pnl_txt, "Status": t["Status"],
        })
    stats = {"wins": wins, "losses": losses, "total": wins + losses, "pnl": total_pnl}
    return rows, stats, changed


def can_open_new_trade(trades, stats):
    if any(t["State"] in ("WAITING", "OPEN") for t in trades):
        return False, "A trade is already active (one at a time)."
    if len([t for t in trades if t["State"] != "EXPIRED"]) >= max_trades_day:
        return False, f"Max trades per day ({max_trades_day}) reached."
    if stats["pnl"] <= -abs(daily_loss_cap):
        return False, f"Daily loss cap ₹{daily_loss_cap:,} reached."
    return True, ""


def add_paper_trade(sym, ttype, entry, sl, tgt, ltp, lot, audit, score):
    n = now_ist()
    ledger.append({
        "Timestamp": n.strftime("%H:%M:%S"), "CreatedEpoch": time.time(),
        "Symbol": sym, "Type": ttype, "Entry Limit": entry, "Current LTP": ltp,
        "Stop Loss": sl, "Original SL": sl, "Target": tgt, "Lot": lot,
        "State": "WAITING", "Status": STATUS_TEXT["WAITING"], "Exit Price": None,
    })
    daily_state["audit_logs"].append({
        "Timestamp": n.strftime("%Y-%m-%d %H:%M:%S"), "Symbol": sym, "Type": ttype,
        "Entry": entry, "StopLoss": sl, "Target": tgt,
        "ConfluenceScore": f"{score * 100:.1f}%", "Checklist": audit,
    })
    save_daily_state(daily_state)


def behavioral_insight(stats):
    n = stats["total"]
    if n == 0:
        return "Awaiting closed paper trades. No statistics yet."
    wr = stats["wins"] / n
    if n < 10:
        return f"Only {n} closed trade(s) today — sample too small to draw conclusions ({wr * 100:.0f}% win rate so far)."
    if wr < 0.25:
        return f"⚠️ Win rate {wr * 100:.0f}% over {n} trades is below break-even for 1:4 R:R (~20% + costs). Review setups manually."
    return f"🟢 Win rate {wr * 100:.0f}% over {n} trades. Display only — no filters are auto-changed."


# =====================================================================
# 8. LIVE DATA PREP (runs once per refresh, before the tabs)
# =====================================================================
token_dead = False
try:
    spot_price, spot_src = fetch_spot(kite_client, cfg)
except TokenException:
    token_dead, spot_price, spot_src = True, None, "unavailable"
if token_dead:
    daily_state["access_token"] = None
    save_daily_state(daily_state)
    st.warning("Kite session expired — please re-authorize in the sidebar.")
    st.rerun()

india_vix = fetch_vix(kite_client)

chain = get_option_chain(api_key, access_token, cfg["nfo"]) if kite_client else None
lot_size = int(chain["lot_size"].iloc[0]) if chain is not None else None
expiry_date = chain["expiry"].iloc[0].date() if chain is not None else None
days_to_expiry = (expiry_date - now_ist().date()).days if expiry_date else None
strike_step = cfg["step"]
if chain is not None:
    _s = sorted(chain["strike"].unique())
    if len(_s) > 1:
        strike_step = int(min(np.diff(_s)))
T_years = None
if expiry_date:
    exp_dt = datetime.datetime.combine(expiry_date, T_MARKET_CLOSE, tzinfo=IST)
    T_years = max((exp_dt - now_ist()).total_seconds(), 0) / (365 * 24 * 3600)
    T_years = max(T_years, 0.0003)

mtf = None
if kite_client:
    mtf = fetch_mtf(api_key, access_token, cfg["nse"])
mtf_ok = bool(mtf) and "error" not in mtf
fut = fetch_fut_candles(api_key, access_token, cfg["nfo"]) if kite_client else None

overall_pcr = pcr_near = None
if kite_client and chain is not None and spot_price:
    overall_pcr, pcr_near = compute_pcr(kite_client, chain, spot_price, strike_step)

trade_bias = determine_dynamic_trade_bias(mtf if mtf_ok else None, overall_pcr, fii_sentiment)  # None = no trading
trend_15 = tf_trend(mtf if mtf_ok else None, "15m")
trend_60 = tf_trend(mtf if mtf_ok else None, "60m")
active_trade_type = trade_bias or display_bias(mtf if mtf_ok else None, overall_pcr)           # what we display
levels = compute_levels(mtf["5m"]) if mtf_ok else compute_levels(None)

ledger_rows, ledger_stats, ledger_changed = process_ledger(ledger, kite_client, lot_size, runner_mode)
if ledger_changed:
    save_daily_state(daily_state)

# =====================================================================
# 9. TOP METRICS
# =====================================================================
def fmt(v, f="{:,.2f}"):
    return f.format(v) if v is not None else "N/A"


c1, c2, c3, c4, c5 = st.columns(5)
c1.markdown(
    f'<div class="metric-card"><div class="metric-label">📈 Spot ({selected_index}) · {spot_src}</div>'
    f'<div class="metric-value">₹{fmt(spot_price, "{:,.1f}")}</div></div>', unsafe_allow_html=True)
c2.markdown(
    f'<div class="metric-card"><div class="metric-label">⚡ India VIX</div>'
    f'<div class="metric-value" style="color:#ff7b72;">{fmt(india_vix)}</div></div>', unsafe_allow_html=True)
c3.markdown(
    f'<div class="metric-card"><div class="metric-label">📊 PCR (live OI, ATM±10)</div>'
    f'<div class="metric-value" style="color:#3fb950;">{fmt(overall_pcr)}</div></div>', unsafe_allow_html=True)
exp_color = "#ff7b72" if days_to_expiry == 0 else "#58a6ff"
exp_txt = f"{expiry_date.strftime('%a %d %b')} ({days_to_expiry}d)" if expiry_date else "N/A"
c4.markdown(
    f'<div class="metric-card"><div class="metric-label">🗓️ Session & Expiry</div>'
    f'<div class="metric-value" style="font-size:1.05rem;">{current_day_str}, {current_date_str}</div>'
    f'<div style="font-size:0.8rem;color:{exp_color};margin-top:4px;font-weight:700;">⏳ Expiry: {exp_txt}</div></div>',
    unsafe_allow_html=True)
c5.markdown(
    f'<div class="metric-card"><div class="metric-label">📦 Lot Size (from Kite)</div>'
    f'<div class="metric-value">{lot_size if lot_size else "N/A"}</div></div>', unsafe_allow_html=True)
st.write("")

tab_dashboard, tab_backtest, tab_sr_fib, tab_strategy, tab_ledger = st.tabs([
    "📊 Institutional Quant Dashboard", "📊 Historical Backtest Engine",
    "📈 Live S&R & Fibonacci Levels", "🧬 Multi-Leg Strategy Builder",
    "📝 Paper Trading Ledger & AI Feedback",
])

# ================= TAB 1: DASHBOARD =================
with tab_dashboard:
    st.markdown(
        f"""<div style="background-color:#161b22;border:1px solid #30363d;border-left:6px solid #58a6ff;padding:16px;border-radius:8px;margin-bottom:20px;">
        <div style="color:#58a6ff;font-size:1.1rem;font-weight:800;">🧠 Session Feedback</div>
        <div style="color:#f0f6fc;font-size:0.95rem;margin-top:5px;">{behavioral_insight(ledger_stats)}</div></div>""",
        unsafe_allow_html=True)

    engine_ready = all([kite_client, chain is not None, spot_price, mtf_ok, active_trade_type])
    if not kite_client:
        st.info("💡 Authenticate Zerodha Kite in the sidebar to activate the engine.")
    elif chain is None:
        st.warning(f"No option chain found for {selected_index} (index may not have derivatives).")
    elif isinstance(mtf, dict) and "error" in mtf:
        st.warning(f"Market data unavailable: {mtf['error']}")

    if engine_ready:
        view_tag = "" if trade_bias else " · VIEW ONLY"
        gate_info = {"score": None, "audit": {}, "dup": False}
        if not trade_bias:
            st.info("👁️ VIEW-ONLY MODE: 15m and 60m trends disagree (or conflict with FII bias). "
                    "All data is shown below, but no paper trades will be captured until they agree.")
        ce = active_trade_type == "CE"
        bias_color = "#56d364" if ce else "#ff7b72"
        closed_5m = mtf["5m"][:-1]
        smc = smc_analyze(closed_5m[-150:])
        smc_steps = smc_sequence(smc, active_trade_type, spot_price)
        smc_done = sum(smc_steps.values())
        pattern_name, candle_bias = detect_candlestick_patterns(closed_5m)
        hi, lo = levels["swing_high"], levels["swing_low"]
        retest_zone = zone_w = zl = zh = None
        if hi is not None and lo is not None and hi > lo:
            retest_zone = (hi - 0.618 * (hi - lo)) if ce else (lo + 0.618 * (hi - lo))
            zone_w = max(0.04 * (hi - lo), spot_price * 0.0005)
            zl, zh = retest_zone - zone_w, retest_zone + zone_w
        retest_ok, retest_msg, hammer = check_weak_hammer_trigger(mtf["5m"], zl, zh, active_trade_type)
        zone_hit = zone_touched(mtf["5m"], zl, zh)
        fut_ok = bool(fut) and "error" not in fut
        fut_candles = fut["candles"] if fut_ok else []
        poc_idx = None
        if fut_candles:
            _poc = compute_poc(fut_candles, max(strike_step / 10, 1))
            if _poc is not None:
                poc_idx = _poc - (fut_candles[-1]["close"] - spot_price)  # futures price -> index terms
        poc_aligned = None if (poc_idx is None or zone_w is None) else bool(abs(poc_idx - retest_zone) <= 1.5 * zone_w)
        vol_ratio = volume_ratio(fut_candles, hammer["date"]) if hammer else None
        if not fut_ok:
            st.caption("Futures volume unavailable: POC alignment and the volume layer show N/A.")
        smc = analyze_smc(closed_5m, spot_price, hi, lo)
        smc_checks = smc_checklist(smc, spot_price, active_trade_type)

        st.markdown("### 📊 Gautam Jha Confirmation Retest Indicator")
        zone_txt = f"₹{retest_zone:,.1f} ± {zone_w:,.0f} pts (Fib 0.618 of today's swing)" if retest_zone else "N/A (need ≥6 candles today)"
        st.markdown(
            f"""<div style="background-color:#161b22;border:1px solid #30363d;border-left:6px solid {bias_color};padding:20px;border-radius:10px;margin-bottom:20px;">
            <div style="color:{bias_color};font-size:1.2rem;font-weight:800;">🔍 {pattern_name} ({active_trade_type} Mode{view_tag})</div>
            <div style="color:#f0f6fc;font-size:0.95rem;margin-top:8px;">
            • <b>Retest Zone:</b> {zone_txt}<br>
            • <b>Confirmation:</b> {retest_msg}<br>
            • <b>Target Ratio:</b> 1:{min_rr} R:R | <b>Active Bias:</b> <span style="color:{bias_color};font-weight:bold;">{active_trade_type}</span></div></div>""",
            unsafe_allow_html=True)

        st.markdown(f"### 🧩 Smart Money Concepts (view-only, 5-min) — for {active_trade_type}")
        want_dir = "bull" if ce else "bear"

        def al(ok):
            return "✅" if ok else "❌"

        brk, swp = smc["break"], smc["sweep"]
        fv = smc["fvg_bull"] if ce else smc["fvg_bear"]
        smc_rows = [
            {"Concept": "Market structure", "Reading": {"bull": "Bullish (HH + HL)", "bear": "Bearish (LH + LL)",
                                                       "range": "Range / mixed", "NA": "Not enough candles"}[smc["trend"]],
             "Fits trade": al(smc["trend"] == want_dir)},
            {"Concept": "Last BOS / CHoCH (12 candles)",
             "Reading": f"{brk['type']} {brk['dir']} through {brk['level']:,.1f}, {brk['ago']} candle(s) ago" if brk else "None",
             "Fits trade": al(smc_checks["break"])},
            {"Concept": "Liquidity sweep (12 candles)",
             "Reading": f"{swp['dir']} sweep of {swp['level']:,.1f}, {swp['ago']} candle(s) ago" if swp else "None",
             "Fits trade": al(smc_checks["sweep"])},
            {"Concept": "Unfilled FVG (price inside/near?)",
             "Reading": f"{fv[0]:,.1f} – {fv[1]:,.1f} ({fv[2]} candle(s) old)" if fv else "None",
             "Fits trade": al(smc_checks["fvg"])},
            {"Concept": "Premium / Discount (today's range)",
             "Reading": (smc["zone"] or "N/A").title(), "Fits trade": al(smc_checks["zone"])},
        ]
        show_df(pd.DataFrame(smc_rows), hide_index=True)
        st.caption(f"SMC checklist: {sum(smc_checks.values())}/4 for {active_trade_type}. "
                   "View-only: it does NOT affect the score or trade capture. It is saved with every paper trade "
                   "so we can test later whether it separates winners from losers.")

        st.markdown("### 🎯 Institutional Sniper 10-Layer Confluence Engine")
        atm = round(spot_price / strike_step) * strike_step
        side = chain[chain["instrument_type"] == active_trade_type]
        if side.empty:
            st.warning("No contracts for the active side.")
        else:
            strikes_avail = np.array(sorted(side["strike"].unique()))
            atm_k = float(strikes_avail[np.abs(strikes_avail - atm).argmin()])
            row = side[side["strike"] == atm_k].iloc[0]
            sym = row["tradingsymbol"]
            sniper_ltp = None
            try:
                qd = kite_client.quote([f"NFO:{sym}"])
                sniper_ltp = float(qd[f"NFO:{sym}"]["last_price"])
            except Exception:
                pass

            if not sniper_ltp or sniper_ltp <= 0:
                st.warning(f"No live price for {sym}; engine on standby.")
            elif use_blackout and check_economic_calendar_blackout(now_ist().time()):
                st.warning("⚠️ Execution halted: macro-event blackout window.")
            else:
                iv = implied_vol(sniper_ltp, spot_price, atm_k, T_years, RISK_FREE, active_trade_type)
                greeks_ok = None
                if iv is not None:
                    dlt, _, _, _ = calculate_greeks(spot_price, atm_k, T_years, RISK_FREE, iv / 100, active_trade_type)
                    greeks_ok = (iv < 45.0) and (0.35 <= abs(dlt) <= 0.65)
                fib_tp = compute_fib_trade_params(spot_price, active_trade_type, hammer, hi, lo, ext_ratio,
                                                  sl_buffer, atm_k, T_years, iv, sniper_ltp)
                if fib_tp:
                    snip_entry, snip_sl, snip_tgt = fib_tp["entry"], fib_tp["sl"], fib_tp["tgt"]
                    fib_note = (f"| <b>R:R</b> index 1:{fib_tp['rr_idx']:.1f}"
                                + (f" · option 1:{fib_tp['rr_opt']:.1f}" if fib_tp["rr_opt"] else ""))
                else:  # preview only; Layer 6 will block capture without a hammer
                    snip_entry, snip_sl, snip_tgt = calculate_sniper_parameters(sniper_ltp, sl_pct, min_rr)
                    fib_note = "| <i>(preview levels: no hammer setup yet)</i>"
                md = {
                    "spot": spot_price, "pcr": overall_pcr, "pcr_near": pcr_near,
                    "twap": levels["twap"], "candle_bias": candle_bias,
                    "fib_ok": zone_hit, "fvg_in": smc_checks["fvg"], "poc_aligned": poc_aligned,
                    "vol_ratio": vol_ratio, "rr": fib_tp["rr_idx"] if fib_tp else None, "min_rr": min_rr,
                    "retest_ok": retest_ok, "greeks_ok": greeks_ok,
                    "sl_valid": 0 < snip_sl < snip_entry,
                }
                score, audit_trail, status = evaluate_confluence(md, active_trade_type, min_score)
                _dn = {1: "Bullish", -1: "Bearish", 0: "Flat/NA"}
                audit_trail.update({  # context saved with every audit snapshot for later study
                    "Context: 15m trend": _dn[trend_15], "Context: 60m trend": _dn[trend_60],
                    "Context: SMC steps (of 4)": smc_done, "Context: SMC structure": {1: "Bullish", -1: "Bearish", 0: "Undetermined"}[smc["trend"]], "Context: VIX": india_vix, "Context: PCR": overall_pcr, "Context: Near-ATM PCR": pcr_near,
                    "Context: SMC structure": smc["trend"],
                    "Context: Fib ext ratio": ext_ratio,
                    "Context: Volume ratio": round(vol_ratio, 2) if vol_ratio is not None else None,
                    "Context: POC aligned": poc_aligned,
                    "Context: R:R index": round(fib_tp["rr_idx"], 2) if fib_tp else None,
                    "Context: SMC checklist": f"{sum(smc_checks.values())}/4",
                    **{f"Context: SMC {k}": v for k, v in smc_checks.items()},
                })
                gate_info.update(score=score, audit=audit_trail)
                approved = status.startswith("EXECUTE")
                border = "#56d364" if approved else "#ff7b72"
                st.markdown(
                    f"""<div style="background-color:#161b22;border:1px solid #30363d;border-left:6px solid {border};padding:22px;border-radius:10px;margin-bottom:20px;">
                    <div style="color:{border};font-size:1.25rem;font-weight:800;">🔥 {active_trade_type} VAULT{view_tag}: {sym} — {status}</div>
                    <div style="color:#f0f6fc;font-size:0.95rem;margin-top:8px;">
                    • <b>Limit Entry:</b> ₹{snip_entry} | <b>Stop Loss:</b> ₹{snip_sl} | <b>Target (1:{min_rr}):</b>
                    <span style="color:#56d364;font-weight:bold;">₹{snip_tgt}</span> | <b>IV:</b> {fmt(iv)}% {fib_note}</div></div>""",
                    unsafe_allow_html=True)
                with st.expander("Layer-by-layer checklist"):
                    st.json(audit_trail)
                gti_rows = [
                    {"Item": "Daily POC (index-equivalent)", "Value": f"{poc_idx:,.1f}" if poc_idx else "N/A"},
                    {"Item": "Hammer candle volume vs 20-SMA", "Value": f"{vol_ratio:.2f}x (need 1.5x)" if vol_ratio is not None else "N/A"},
                    {"Item": "Hammer candle (low – high)", "Value": f"{hammer['low']:,.1f} – {hammer['high']:,.1f}" if hammer else "None in 0.618 zone"},
                    {"Item": "SL (index)", "Value": f"{fib_tp['sl_idx']:,.1f}" if fib_tp else "—"},
                    {"Item": f"Fib {ext_ratio} target (index)", "Value": f"{fib_tp['tgt_idx']:,.1f}" if fib_tp else "—"},
                    {"Item": "Option SL / Target (₹)", "Value": f"{fib_tp['sl']} / {fib_tp['tgt']}" if fib_tp else "—"},
                ]
                show_df(pd.DataFrame(gti_rows), hide_index=True)

                ok_new, why = can_open_new_trade(ledger, ledger_stats)
                existing = [t["Symbol"] for t in ledger]
                gate_info["dup"] = sym in existing
                if approved and not trade_bias:
                    st.info("Score passed, but the 15m + 60m trend rule is not met — view only, no paper trade captured.")
                elif approved and not ok_new:
                    st.info(f"Dispatch blocked: {why}")
                elif approved and sym in existing:
                    st.info(f"{sym} was already traded today.")
                elif approved:
                    if auto_forward_test:
                        add_paper_trade(sym, active_trade_type, snip_entry, snip_sl, snip_tgt,
                                        sniper_ltp, lot_size, audit_trail, score)
                        st.toast(f"🤖 Auto-captured {active_trade_type} setup: {sym}")
                        st.rerun()
                    elif st.button(f"🚀 Manually Dispatch {active_trade_type} Paper Setup"):
                        add_paper_trade(sym, active_trade_type, snip_entry, snip_sl, snip_tgt,
                                        sniper_ltp, lot_size, audit_trail, score)
                        st.success(f"✅ {sym} dispatched to paper ledger.")
                        st.rerun()
                else:
                    st.warning("Dispatch blocked: confluence requirements not met.")

        # ---------------- LIVE GATE TRACKER ----------------
        st.markdown("### 🚦 Live Gate Tracker")
        n_ = now_ist()
        dn = {1: "Bullish", -1: "Bearish", 0: "Flat/NA"}
        dirn_h = "CE" if trend_15 + trend_60 == 2 else "PE" if trend_15 + trend_60 == -2 else None
        fii_conflict = ((fii_sentiment == "Net Bullish (Longs)" and dirn_h == "PE")
                        or (fii_sentiment == "Net Bearish (Shorts)" and dirn_h == "CE"))
        n_trades = len([t for t in ledger if t["State"] != "EXPIRED"])
        grows = [
            ("A. Permission", "Data feed live", "Kite + chain + candles", True),
            ("A. Permission", "Trading window", n_.strftime("%a %H:%M IST"),
             n_.weekday() < 5 and T_ENTRY_START <= n_.time() <= T_ENTRY_END),
            ("A. Permission", "Fixed blackout", "10:00-10:15 / 13:30-13:45",
             (not check_economic_calendar_blackout(n_.time())) if use_blackout else None),
            ("A. Permission", "15m & 60m agree", f"15m {dn[trend_15]} | 60m {dn[trend_60]}",
             trend_15 != 0 and trend_15 == trend_60),
            ("A. Permission", "Not against FII bias", fii_sentiment, not fii_conflict),
        ]
        for name, val in gate_info["audit"].items():
            if name.startswith("Gate 1") or name.startswith("Context"):
                continue
            sv = str(val)
            grows.append(("B. Layers", name, sv, None if sv.startswith("N/A") else sv.startswith("PASS")))
        if gate_info["score"] is not None:
            grows.append(("B. Layers", "Confluence score", f"{gate_info['score'] * 100:.1f}% (need {min_score * 100:.0f}%)",
                          gate_info["score"] >= min_score))
        grows += [
            ("C. Risk", "No active trade", "one at a time",
             not any(t["State"] in ("WAITING", "OPEN") for t in ledger)),
            ("C. Risk", "Daily trade limit", f"{n_trades}/{max_trades_day}", n_trades < max_trades_day),
            ("C. Risk", "Daily loss cap", f"P&L ₹{ledger_stats['pnl']:,.0f}", ledger_stats["pnl"] > -abs(daily_loss_cap)),
            ("C. Risk", "Symbol not traded today", "", not gate_info["dup"]),
        ]
        gstats = daily_state.setdefault("gate_stats", {})
        for _, g, _, ok in grows:
            if ok is None:
                continue
            e = gstats.setdefault(g, {"pass": 0, "fail": 0})
            e["pass" if ok else "fail"] += 1
        save_daily_state(daily_state)

        blockers = [g for _, g, _, ok in grows if ok is False]
        if blockers:
            st.warning("⛔ Blocking right now: " + ", ".join(blockers))
        else:
            st.success("✅ All gates open right now.")
        gdf = pd.DataFrame([{
            "Group": grp, "Gate": g, "Now": ("✅" if ok else "❌" if ok is False else "➖"), "Detail": d,
            "Open today": (f"{gstats[g]['pass'] * 100 // (gstats[g]['pass'] + gstats[g]['fail'])}%"
                           if ok is not None and g in gstats else "—"),
        } for grp, g, d, ok in grows])
        show_df(gdf, hide_index=True)
        st.caption("'Open today' = share of the 30-second checks today in which the gate was passing. "
                   "The gates that are most often closed are the ones that decide how many trades you get.")

        # ---------------- SMC VIEW (view-only) ----------------
        st.markdown("### 🧭 SMC View (view-only — not used in trade decisions)")
        tr_txt = {1: "Bullish (last break was up)", -1: "Bearish (last break was down)", 0: "Undetermined"}[smc["trend"]]
        pd_txt = "n/a"
        if hi is not None and lo is not None and hi > lo:
            eq = (hi + lo) / 2
            in_disc = spot_price < eq
            fav = (in_disc and ce) or ((not in_disc) and (not ce))
            pd_txt = (f"{'Discount' if in_disc else 'Premium'} (equilibrium ₹{eq:,.1f}) — "
                      f"{'✅ favorable' if fav else '⚠️ not ideal'} for {active_trade_type}")
        st.markdown(f"**Structure:** {tr_txt}  \n**Today's range:** {pd_txt}")
        st.markdown(f"**Entry sequence for {active_trade_type}: {smc_done}/4**")
        show_df(pd.DataFrame([{"Step": k, "Met": "✅" if v else "❌"} for k, v in smc_steps.items()]),
                hide_index=True)
        if smc["events"]:
            ev = smc["events"][-6:][::-1]
            show_df(pd.DataFrame([{"Time": e["time"], "Event": e["kind"],
                                   "Direction": "Bullish" if e["dir"] == "bull" else "Bearish",
                                   "Level": round(e["level"], 1)} for e in ev]), hide_index=True)
        else:
            st.caption("No structure events detected in recent candles.")
        if smc["fvgs"]:
            fv = smc["fvgs"][-4:][::-1]
            st.markdown("**Unfilled fair value gaps**")
            show_df(pd.DataFrame([{"Formed": f["time"], "Direction": "Bullish" if f["dir"] == "bull" else "Bearish",
                                   "Zone": f"{f['bottom']:,.1f} – {f['top']:,.1f}",
                                   "Price inside": "✅" if f["bottom"] <= spot_price <= f["top"] else "—"}
                                  for f in fv]), hide_index=True)
        st.caption("Rules: swing = 2 candles each side; BOS/CHoCH = a candle CLOSES beyond the last swing; "
                   "sweep = wick beyond a swing but close back inside; FVG = 3-candle gap (≥0.03% of price). "
                   "The sequence count is saved with each paper trade so we can test later if it helps.")

        st.markdown("---")
        if st.button("🚀 RUN FULL GREEKS & QUANT AUDIT"):
            st.session_state.run_audit = True
        if st.session_state.get("run_audit"):
            side_sorted = np.array(sorted(side["strike"].unique()))
            i0 = int(np.abs(side_sorted - atm).argmin())
            picks = side_sorted[max(i0 - 2, 0): i0 + 3]
            sub = side[side["strike"].isin(picks)].sort_values("strike")
            try:
                qq = kite_client.quote([f"NFO:{s}" for s in sub["tradingsymbol"]])
            except Exception as e:
                qq = {}
                st.warning(f"Quote error: {e}")
            recs = []
            for _, r_ in sub.iterrows():
                ltp = float(qq.get(f"NFO:{r_['tradingsymbol']}", {}).get("last_price", 0) or 0)
                k = float(r_["strike"])
                iv_ = implied_vol(ltp, spot_price, k, T_years, RISK_FREE, active_trade_type)
                if iv_ is None:
                    recs.append({"Strike": k, "Type": active_trade_type, "LTP (₹)": ltp,
                                 "Solved IV (%)": None, "Verdict": "❌ NO VALID IV / ILLIQUID"})
                    continue
                d, th, g, v = calculate_greeks(spot_price, k, T_years, RISK_FREE, iv_ / 100, active_trade_type)
                recs.append({
                    "Strike": k, "Type": active_trade_type, "LTP (₹)": ltp, "Solved IV (%)": iv_,
                    "Delta": d, "Theta": th, "Gamma": g, "Vega": v,
                    "Verdict": f"✅ APPROVED" if iv_ < 45 else "❌ REJECTED (High IV)",
                })
            st.markdown(f"### 📋 Black-Scholes Greeks Matrix ({selected_index})")
            show_df(pd.DataFrame(recs), height=275)

        st.markdown("---")
        st.markdown(f"### ⚡ Multi-Timeframe Matrix — Mode ({active_trade_type})")

        def eval_tf(candles, t_type):
            c = (candles or [])[:-1]
            if len(c) < 3:
                return "NEUTRAL", "#d29922"
            ret = c[-1]["close"] - c[-3]["close"]
            if t_type == "CE":
                return ("BULLISH", "#56d364") if ret > 0 else ("BEARISH", "#ff7b72")
            return ("BEARISH (Favorable)", "#56d364") if ret < 0 else ("BULLISH (Against PE)", "#ff7b72")

        tf_cols = st.columns(5)
        for col, (key, label) in zip(tf_cols, [("1m", "1-Min (Scalp)"), ("3m", "3-Min (Momentum)"),
                                              ("5m", "5-Min (Retest)"), ("15m", "15-Min (Trend)"),
                                              ("60m", "60-Min (Macro)")]):
            txt, colr = eval_tf(mtf.get(key), active_trade_type)
            col.markdown(
                f'<div class="metric-card"><div class="metric-label">{label}</div>'
                f'<div class="metric-value" style="color:{colr};font-size:1rem;">{txt}</div></div>',
                unsafe_allow_html=True)

# ================= TAB 2: BACKTEST =================
def run_backtest(kite_obj, nse_symbol, days, target_pts, rr):
    inst = load_instruments(api_key, access_token, "NSE")
    m = inst[inst["tradingsymbol"] == nse_symbol]
    if m.empty:
        return None
    tok = int(m.iloc[0]["instrument_token"])
    to_dt = now_ist().replace(tzinfo=None)
    candles = kite_obj.historical_data(tok, to_dt - datetime.timedelta(days=days), to_dt, "5minute")
    if not candles or len(candles) < 60:
        return None
    risk_pts = target_pts / rr
    wins = losses = 0
    for i in range(2, len(candles) - 12):
        ci = candles[i]
        if not (T_ENTRY_START <= ci["date"].time() <= T_ENTRY_END):
            continue
        name, bias = detect_candlestick_patterns(candles[i - 2: i + 1])
        if bias == "neutral":
            continue
        dirn = 1 if bias == "bull" else -1
        entry = ci["close"]
        tgt, stp = entry + dirn * target_pts, entry - dirn * risk_pts
        for j in range(i + 1, i + 11):
            cj = candles[j]
            if cj["date"].date() != ci["date"].date():
                break  # no overnight carry; unsettled setups are ignored
            hit_sl = cj["low"] <= stp if dirn == 1 else cj["high"] >= stp
            hit_tg = cj["high"] >= tgt if dirn == 1 else cj["low"] <= tgt
            if hit_sl:        # SL first when both inside one candle (conservative)
                losses += 1
                break
            if hit_tg:
                wins += 1
                break
    settled = wins + losses
    return {"settled": settled, "wins": wins, "losses": losses,
            "win_rate": round(wins / settled * 100, 1) if settled else 0.0,
            "expectancy": round(wins * target_pts - losses * risk_pts, 1),
            "breakeven_wr": round(100 / (1 + rr), 1)}


with tab_backtest:
    st.markdown(f"### 📊 Historical Backtest (index points, 5-min candles, 1:{min_rr} R:R)")
    st.caption("Tests the candlestick signal on the index itself (bull patterns long, bear patterns short). "
               "Ignores costs/slippage and option pricing — treat as a rough sanity check only.")
    b1, b2 = st.columns(2)
    bt_days = b1.slider("Lookback (Days)", 5, 60, 30, key="bt_days")
    bt_target = b2.slider(f"Target (index points, 1:{min_rr} R:R)", 20, 200, 60, key="bt_target")
    if st.button("Run Backtest", type="primary"):
        if not kite_client:
            st.warning("⚠️ Authorize Kite session first.")
        else:
            with st.spinner("Scanning historical candles..."):
                try:
                    res = run_backtest(kite_client, cfg["nse"], bt_days, bt_target, min_rr)
                except Exception as ex:
                    res = None
                    st.error(f"Backtest error: {ex}")
            if res is None:
                st.warning("Not enough historical data returned.")
            elif res["settled"] < 10:
                st.warning(f"Only {res['settled']} settled setups — too few to be meaningful.")
            else:
                r1, r2, r3, r4 = st.columns(4)
                r1.metric("Settled Setups", res["settled"])
                r2.metric("Win Rate", f"{res['win_rate']}%", delta=f"break-even {res['breakeven_wr']}%")
                r3.metric("W / L", f"{res['wins']} / {res['losses']}")
                r4.metric("Net Expectancy", f"{res['expectancy']:+,.1f} pts")

# ================= TAB 3: S&R & FIB =================
with tab_sr_fib:
    st.markdown(f"### 📈 Support & Resistance Pivots and Fibonacci ({selected_index})")
    hi, lo = levels["swing_high"], levels["swing_low"]
    ca, cb = st.columns(2)
    with ca:
        st.markdown("#### 🏛️ Classic Pivots (previous session)")
        pv = levels["prev"]
        if pv:
            P = (pv["high"] + pv["low"] + pv["close"]) / 3
            rng_ = pv["high"] - pv["low"]
            rows_ = {"Resistance R2": P + rng_, "Resistance R1": 2 * P - pv["low"], "Pivot (P)": P,
                     "Support S1": 2 * P - pv["high"], "Support S2": P - rng_}
            st.caption(f"Based on {pv['date']} H/L/C")
            show_df(pd.DataFrame([(k, round(v, 2)) for k, v in rows_.items()], columns=["Level", "Price (₹)"]),
                    hide_index=True)
        else:
            st.info("Connect Kite to compute pivots from previous-session data.")
    with cb:
        st.markdown("#### 🌀 Fibonacci (today's swing)")
        if hi is not None and lo is not None and hi > lo:
            d = hi - lo
            fib = {"0.0% (Swing High)": hi, "23.6%": hi - 0.236 * d, "38.2%": hi - 0.382 * d,
                   "50.0%": hi - 0.5 * d, "61.8% (Golden Pocket)": hi - 0.618 * d,
                   "78.6%": hi - 0.786 * d, "100% (Swing Low)": lo}
            show_df(pd.DataFrame([(k, round(v, 2)) for k, v in fib.items()],
                                 columns=["Fib Level", "Price (₹)"]), hide_index=True)
        else:
            st.info("Need at least 6 five-minute candles from today.")

# ================= TAB 4: STRATEGY BUILDER =================
with tab_strategy:
    st.markdown("### 🧬 Debit Spread Calculator")
    if not spot_price:
        st.info("Spot price unavailable.")
    else:
        base = round(spot_price / strike_step) * strike_step
        grid = [base + i * strike_step for i in range(-3, 6)]
        kind = st.selectbox("Spread type", ["Bull Call Spread (CE debit)", "Bear Put Spread (PE debit)"])
        is_call = kind.startswith("Bull")
        s1, s2, s3 = st.columns(3)
        long_k = s1.selectbox("Buy strike", grid, index=3)
        short_k = s2.selectbox("Sell strike", grid, index=5 if is_call else 1)
        lots = s3.number_input("Lots", 1, 50, 1)
        otype = "CE" if is_call else "PE"

        live_debit = 0.0
        if kite_client and chain is not None:
            try:
                sub = chain[(chain["instrument_type"] == otype) & (chain["strike"].isin([long_k, short_k]))]
                qs = kite_client.quote([f"NFO:{s}" for s in sub["tradingsymbol"]])
                px = {float(r_["strike"]): float(qs[f"NFO:{r_['tradingsymbol']}"]["last_price"])
                      for _, r_ in sub.iterrows() if f"NFO:{r_['tradingsymbol']}" in qs}
                if long_k in px and short_k in px:
                    live_debit = round(px[long_k] - px[short_k], 2)
            except Exception:
                pass
        debit = st.number_input("Net debit per unit (₹) — auto-filled from live quotes when available",
                                min_value=0.0, value=max(live_debit, 0.0), step=0.5,
                                key=f"debit_{otype}_{long_k}_{short_k}")
        valid = (short_k > long_k) if is_call else (short_k < long_k)
        lot_use = lot_size or st.number_input("Lot size (not auto-detected)", 1, 5000, 25)
        if not valid:
            st.warning("⚠️ Invalid legs: for a call spread sell the HIGHER strike; for a put spread sell the LOWER strike.")
        elif debit <= 0:
            st.info("Enter the net debit to see the risk profile.")
        else:
            width = abs(short_k - long_k)
            costs = COST_PER_TRADE * 2  # two legs
            if debit >= width:
                st.error("Net debit ≥ strike width: this spread can never be profitable.")
            else:
                max_loss = debit * lot_use * lots + costs
                max_profit = (width - debit) * lot_use * lots - costs
                be = long_k + debit if is_call else long_k - debit
                st.markdown(
                    f"""<div style="background-color:#161b22;border:1px solid #30363d;border-left:6px solid #56d364;padding:18px;border-radius:8px;">
                    <div style="color:#58a6ff;font-weight:700;margin-bottom:8px;">📊 Risk & P&L (after ₹{costs:.0f} costs)</div>
                    • <b>Max Loss:</b> <span style="color:#ff7b72;">₹{max_loss:,.0f}</span> &nbsp;|&nbsp;
                    <b>Max Profit:</b> <span style="color:#56d364;">₹{max_profit:,.0f}</span><br>
                    • <b>Reward:Risk:</b> 1:{max_profit / max_loss:.2f} &nbsp;|&nbsp; <b>Breakeven:</b> ₹{be:,.1f}</div>""",
                    unsafe_allow_html=True)

# ================= TAB 5: LEDGER =================
with tab_ledger:
    st.markdown(f"### 📝 Paper Trading Ledger (1:{min_rr} R:R) — simulated, no real orders")
    if ledger_rows:
        st.markdown("#### 🏆 Today's Performance")
        m1, m2, m3, m4 = st.columns(4)
        tt = ledger_stats["total"]
        m1.metric("Closed Trades", tt)
        m2.metric("Win Rate", f"{round(ledger_stats['wins'] / tt * 100, 1) if tt else 0.0}%",
                  delta=f"{ledger_stats['wins']}W / {ledger_stats['losses']}L")
        m3.metric("Net P&L", f"₹{ledger_stats['pnl']:,.2f}")
        m4.metric("Avg P&L / Trade", f"₹{round(ledger_stats['pnl'] / tt, 2) if tt else 0}")
        st.markdown("---")
        show_df(pd.DataFrame(ledger_rows))
        st.download_button("⬇️ Download Ledger CSV", pd.DataFrame(ledger_rows).to_csv(index=False),
                           file_name=f"paper_ledger_{today_str()}.csv", mime="text/csv")
        st.download_button("⬇️ Download Audit Logs (JSON)",
                           json.dumps(daily_state.get("audit_logs", []), indent=2, default=str),
                           file_name=f"audit_logs_{today_str()}.json", mime="application/json")
        st.markdown("---")
        st.markdown("#### 🔍 Confluence Audit Snapshots")
        logs = daily_state.get("audit_logs", [])
        if logs:
            for log in reversed(logs):
                with st.expander(f"{log['Symbol']} ({log['Timestamp']}) — Score {log['ConfluenceScore']}"):
                    st.json(log["Checklist"])
        else:
            st.info("No audit logs yet.")
        if st.button("🗑️ Clear Paper Ledger & Audit Logs"):
            daily_state["paper_ledger"] = []
            daily_state["audit_logs"] = []
            save_daily_state(daily_state)
            st.rerun()
    else:
        st.info("💡 Ledger is empty. Enable 'Auto-Capture' in the sidebar or dispatch a setup manually.")
