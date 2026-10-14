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
    "Layer 4 (Price Action Structure)": 0.11,
    "Layer 5 (Fibonacci Golden Pocket)": 0.11,
    "Layer 6 (Gautam Jha Retest Confirmation)": 0.11,
    "Layer 7 (Volume Expansion Surge)": 0.10,
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

    ce = trade_type == "CE"
    pcr, near = md.get("pcr"), md.get("pcr_near")
    layers = {
        "Layer 2 (Macro Trend & PCR)": None if pcr is None else (pcr >= 0.90 if ce else pcr <= 1.25),
        "Layer 3 (VWAP/TWAP Anchor)": None if md.get("twap") is None else ((md["spot"] > md["twap"]) if ce else (md["spot"] < md["twap"])),
        "Layer 4 (Price Action Structure)": md["candle_bias"] == ("bull" if ce else "bear"),
        "Layer 5 (Fibonacci Golden Pocket)": md.get("fib_ok"),
        "Layer 6 (Gautam Jha Retest Confirmation)": md["retest_ok"],
        "Layer 7 (Volume Expansion Surge)": None,   # index has no volume -> not evaluable
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
    if not layers["Layer 6 (Gautam Jha Retest Confirmation)"]:
        return score, audit, "HOLD: Gautam Jha retest confirmation not met (mandatory)"
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

overall_pcr = pcr_near = None
if kite_client and chain is not None and spot_price:
    overall_pcr, pcr_near = compute_pcr(kite_client, chain, spot_price, strike_step)

active_trade_type = determine_dynamic_trade_bias(mtf if mtf_ok else None, overall_pcr, fii_sentiment)
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
    elif not active_trade_type:
        st.info("⏸️ Standing aside: 15m and 60m trends disagree (or conflict with FII bias).")

    if engine_ready:
        ce = active_trade_type == "CE"
        bias_color = "#56d364" if ce else "#ff7b72"
        closed_5m = mtf["5m"][:-1]
        pattern_name, candle_bias = detect_candlestick_patterns(closed_5m)
        hi, lo = levels["swing_high"], levels["swing_low"]
        retest_zone = None
        if hi is not None and lo is not None and hi > lo:
            retest_zone = (hi - 0.618 * (hi - lo)) if ce else (lo + 0.618 * (hi - lo))
        retest_ok, retest_msg = check_gautam_jha_retest_confirmation(mtf["5m"], retest_zone, active_trade_type)

        st.markdown("### 📊 Gautam Jha Confirmation Retest Indicator")
        zone_txt = f"₹{retest_zone:,.1f} (Fib 61.8% of today's swing)" if retest_zone else "N/A (need ≥6 candles today)"
        st.markdown(
            f"""<div style="background-color:#161b22;border:1px solid #30363d;border-left:6px solid {bias_color};padding:20px;border-radius:10px;margin-bottom:20px;">
            <div style="color:{bias_color};font-size:1.2rem;font-weight:800;">🔍 {pattern_name} ({active_trade_type} Mode)</div>
            <div style="color:#f0f6fc;font-size:0.95rem;margin-top:8px;">
            • <b>Retest Zone:</b> {zone_txt}<br>
            • <b>Confirmation:</b> {retest_msg}<br>
            • <b>Target Ratio:</b> 1:{min_rr} R:R | <b>Active Bias:</b> <span style="color:{bias_color};font-weight:bold;">{active_trade_type}</span></div></div>""",
            unsafe_allow_html=True)

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
                snip_entry, snip_sl, snip_tgt = calculate_sniper_parameters(sniper_ltp, sl_pct, min_rr)
                md = {
                    "spot": spot_price, "pcr": overall_pcr, "pcr_near": pcr_near,
                    "twap": levels["twap"], "candle_bias": candle_bias,
                    "fib_ok": fib_golden_ok(mtf["5m"], hi, lo, ce),
                    "retest_ok": retest_ok, "greeks_ok": greeks_ok,
                    "sl_valid": 0 < snip_sl < snip_entry,
                }
                score, audit_trail, status = evaluate_confluence(md, active_trade_type, min_score)
                approved = status.startswith("EXECUTE")
                border = "#56d364" if approved else "#ff7b72"
                st.markdown(
                    f"""<div style="background-color:#161b22;border:1px solid #30363d;border-left:6px solid {border};padding:22px;border-radius:10px;margin-bottom:20px;">
                    <div style="color:{border};font-size:1.25rem;font-weight:800;">🔥 {active_trade_type} VAULT: {sym} — {status}</div>
                    <div style="color:#f0f6fc;font-size:0.95rem;margin-top:8px;">
                    • <b>Limit Entry:</b> ₹{snip_entry} | <b>Stop Loss:</b> ₹{snip_sl} | <b>Target (1:{min_rr}):</b>
                    <span style="color:#56d364;font-weight:bold;">₹{snip_tgt}</span> | <b>IV:</b> {fmt(iv)}%</div></div>""",
                    unsafe_allow_html=True)
                with st.expander("Layer-by-layer checklist"):
                    st.json(audit_trail)

                ok_new, why = can_open_new_trade(ledger, ledger_stats)
                existing = [t["Symbol"] for t in ledger]
                if approved and not ok_new:
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
                en, sl_v, tg = calculate_sniper_parameters(ltp, sl_pct, min_rr)
                recs.append({
                    "Strike": k, "Type": active_trade_type, "LTP (₹)": ltp, "Solved IV (%)": iv_,
                    "Delta": d, "Theta": th, "Gamma": g, "Vega": v, "Entry (₹)": en,
                    "Stop Loss (₹)": sl_v, f"Target (1:{min_rr})": tg,
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
