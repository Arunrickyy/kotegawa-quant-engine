import json
import os
import datetime
import time
import pandas as pd
import numpy as np
import urllib.parse as urlparse
from scipy.stats import norm
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf
from kiteconnect import KiteConnect
from streamlit_autorefresh import st_autorefresh

# --- 1. PAGE CONFIGURATION ---
st.set_page_config(
    page_title="Kotegawa Quant Engine - Autonomous AI Sniper Terminal v14.0 Pro",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- 2. DEVICE SECURITY LOCK ---
AUTHORIZED_DEVICE_KEY = "arun_private_phone_key_2026"

query_params = st.query_params
device_key = query_params.get("device", "")

if device_key != AUTHORIZED_DEVICE_KEY:
    st.error("🚫 Access Denied: This trading engine is restricted to authorized devices only.")
    st.stop()

# --- 3. PERSISTENT DAILY STATE HANDLING (`daily_state.json`) ---
STATE_FILE = "daily_state.json"

def get_today_str():
    return str(datetime.date.today())

def load_daily_state():
    """Reads active daily session state and paper ledger from disk."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                data = json.load(f)
                if data.get("date") == get_today_str():
                    return data
        except Exception:
            pass
    return {
        "date": get_today_str(),
        "access_token": None,
        "request_token": None,
        "paper_ledger": [],
        "auto_sniper_active": False,
        "runner_mode_active": True
    }

def save_daily_state(state):
    """Saves updated engine state to disk for multi-device sync."""
    state["date"] = get_today_str()
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=4)

daily_state = load_daily_state()

# --- AUTO-REFRESH LIVE SCREENER TIMER ---
refresh_count = st_autorefresh(interval=30000, key="quant_engine_live_refresh")

# --- INITIALIZE SESSION STATE & BEHAVIORAL MEMORY ---
if "paper_ledger" not in st.session_state:
    st.session_state.paper_ledger = daily_state.get("paper_ledger", [])

if "auto_sniper_active" not in st.session_state:
    st.session_state.auto_sniper_active = daily_state.get("auto_sniper_active", False)

if "runner_mode_active" not in st.session_state:
    st.session_state.runner_mode_active = daily_state.get("runner_mode_active", True)

if "market_behavior_memory" not in st.session_state:
    st.session_state.market_behavior_memory = {
        "total_sessions_observed": 1,
        "optimal_win_hour": "09:30 - 11:00 AM",
        "adaptive_rr_bias": 2.0,
        "learned_insight": "Awaiting initial forward-test data to calibrate behavioral weights."
    }

# --- CUSTOM CSS FOR PROFESSIONAL TERMINAL & CLEAR TAB VISIBILITY ---
st.markdown("""
    <style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    .stDeployButton {display: none !important;}

    header[data-testid="stHeader"] {
        background-color: transparent !important;
        visibility: visible !important;
        height: 0px !important;
    }

    .stApp {
        background-color: #0b0f19;
        color: #f0f6fc;
    }

    .stTabs [data-baseweb="tab-list"] button [data-testid="stMarkdownContainer"] p {
        color: #f0f6fc !important;
        font-weight: 700 !important;
        font-size: 0.95rem !important;
    }
    .stTabs [data-baseweb="tab-list"] button[aria-selected="true"] [data-testid="stMarkdownContainer"] p {
        color: #58a6ff !important;
    }

    .terminal-banner {
        background: linear-gradient(135deg, #161b22 0%, #0d1117 100%);
        border: 1px solid #30363d;
        padding: 25px;
        border-radius: 12px;
        text-align: center;
        margin-bottom: 25px;
        box-shadow: 0 4px 15px rgba(0,0,0,0.6);
    }
    .terminal-title {
        color: #ffffff;
        font-size: 2.2rem;
        font-weight: 800;
        letter-spacing: 1px;
        margin-bottom: 5px;
    }
    .terminal-subtitle {
        color: #8b949e;
        font-size: 1.05rem;
        font-weight: 500;
    }

    .metric-card {
        background-color: #161b22;
        border: 1px solid #30363d;
        padding: 18px;
        border-radius: 10px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.4);
    }
    .metric-label {
        color: #8b949e;
        font-size: 0.9rem;
        font-weight: 600;
        margin-bottom: 6px;
    }
    .metric-value {
        color: #f0f6fc;
        font-size: 1.25rem;
        font-weight: 700;
    }

    .stButton > button {
        background: linear-gradient(135deg, #238636 0%, #2ea043 100%);
        color: white;
        border: 1px solid #3fb950;
        font-weight: 700;
        border-radius: 8px;
        padding: 0.6rem 1rem;
        box-shadow: 0 3px 6px rgba(0,0,0,0.3);
        transition: 0.2s ease-in-out;
    }
    .stButton > button:hover {
        background: linear-gradient(135deg, #2ea043 100%, #3fb950 100%);
        border-color: #56d364;
    }

    section[data-testid="stSidebar"] {
        background-color: #0d1117;
        border-right: 1px solid #30363d;
    }
    section[data-testid="stSidebar"] label, section[data-testid="stSidebar"] span {
        color: #c9d1d9 !important;
    }
    </style>
""", unsafe_allow_html=True)

current_datetime = datetime.datetime.now()
current_date_str = current_datetime.strftime("%d %b %Y")
current_day_str = current_datetime.strftime("%A")

st.markdown(f"""
    <div class="terminal-banner">
        <div class="terminal-title">⚡ KOTEGAWA QUANT ENGINE (AI SNIPER v14.0 PRO)</div>
        <div class="terminal-subtitle">Trailing Runners, Multi-Timeframe Matrix, Greeks Chain, AI Feedback & Historical Backtesting</div>
        <div style="color: #58a6ff; font-size: 0.95rem; margin-top: 10px;">
            📅 Live Session: <b>{current_day_str}, {current_date_str}</b> &nbsp;|&nbsp; Developed by <b>Arun Kummar</b>
        </div>
    </div>
""", unsafe_allow_html=True)

# --- SECURE CLOUD CREDENTIAL LOADING & PERSISTENT SESSION SYNC ---
st.sidebar.header("🔌 Zerodha Kite Cloud Bridge")
try:
    api_key = st.secrets["KITE_API_KEY"]
    api_secret = st.secrets["KITE_API_SECRET"]
    st.sidebar.success("🔒 Secure API Keys Loaded from Cloud Secrets")
except:
    api_key = st.sidebar.text_input("Kite API Key", type="password")
    api_secret = st.sidebar.text_input("Kite API Secret", type="password")

kite_client = None
if api_key and api_secret:
    try:
        kite = KiteConnect(api_key=api_key)
        if not daily_state.get("access_token"):
            login_url = kite.login_url()
            st.sidebar.markdown(f"[🔗 Authorize Daily Kite Session]({login_url})", unsafe_allow_html=True)
            token_or_url = st.sidebar.text_input("Paste Request Token or Full Redirect URL (PC):", type="password")
            if token_or_url:
                parsed = urlparse.urlparse(token_or_url)
                extracted_token = urlparse.parse_qs(parsed.query).get("request_token", [None])[0]
                request_token = extracted_token if extracted_token else token_or_url.strip()
                
                data = kite.generate_session(request_token, api_secret=api_secret)
                daily_state["access_token"] = data["access_token"]
                daily_state["request_token"] = request_token
                save_daily_state(daily_state)
                st.sidebar.success("✅ Cloud Session Authenticated & Synced Globally!")
                st.rerun()
        else:
            st.sidebar.success("🟢 Cloud Feed Active (Global Sync)")
            kite.set_access_token(daily_state["access_token"])
            kite_client = kite
    except Exception as e:
        st.sidebar.error(f"Auth Error: {e}")
else:
    st.sidebar.info("💡 Configure API keys in Streamlit Secrets or sidebar.")

st.sidebar.markdown("---")

selected_index = st.sidebar.selectbox("📊 Select Target Index", ["Nifty", "BankNifty", "FinNifty", "NiftyIT"])

if selected_index == "Nifty":
    spot_price, lot_size, india_vix, overall_pcr, expiry_day_str, target_weekday = 24500.0, 25, 13.45, 1.18, "Thursday", 3
elif selected_index == "BankNifty":
    spot_price, lot_size, india_vix, overall_pcr, expiry_day_str, target_weekday = 51200.0, 15, 15.20, 0.94, "Wednesday", 2
elif selected_index == "FinNifty":
    spot_price, lot_size, india_vix, overall_pcr, expiry_day_str, target_weekday = 23400.0, 25, 12.80, 1.32, "Tuesday", 1
else:
    spot_price, lot_size, india_vix, overall_pcr, expiry_day_str, target_weekday = 38500.0, 20, 14.00, 1.10, "Thursday", 3

current_weekday = current_datetime.weekday()
days_to_expiry = (target_weekday - current_weekday) % 7

spot_fetched_via_kite = False
if kite_client:
    try:
        kite_symbol_map = {"Nifty": "NSE:NIFTY 50", "BankNifty": "NSE:NIFTY BANK", "FinNifty": "NSE:NIFTY FIN SERVICE", "NiftyIT": "NSE:NIFTY IT"}
        quote = kite_client.quote(kite_symbol_map[selected_index])
        spot_price = float(quote[kite_symbol_map[selected_index]]["last_price"])
        spot_fetched_via_kite = True
    except: pass

if not spot_fetched_via_kite:
    try:
        live_ticker = yf.Ticker({"Nifty": "^NSEI", "BankNifty": "^NSEBANK", "FinNifty": "NIFTY_FIN_SERVICE.NS", "NiftyIT": "^CNXIT"}[selected_index])
        todays_data = live_ticker.history(period="1d")
        if not todays_data.empty: spot_price = float(todays_data['Close'].iloc[-1])
    except: pass

auto_fii = "Net Bullish (Longs)" if overall_pcr >= 1.0 else "Net Bearish (Shorts)"
fii_sentiment = st.sidebar.selectbox("🏛️ FII Index Future Bias", ["Net Bullish (Longs)", "Net Bearish (Shorts)", "Neutral / Mixed"], index=["Net Bullish (Longs)", "Net Bearish (Shorts)", "Neutral / Mixed"].index(auto_fii))
min_rr = st.sidebar.slider("Min Risk-Reward Ratio", 1.0, 4.0, 2.0, 0.5)
sl_pct = st.sidebar.slider("Stop Loss Drop (%)", 10.0, 50.0, 30.0, 5.0) / 100.0
target_points = st.sidebar.slider("Base Sniper Target Window (Points)", 40, 100, 60, 5)

st.sidebar.markdown("---")
st.sidebar.header("🤖 Automation & Runner Controls")
auto_forward_test = st.sidebar.toggle("Enable Auto-Capture & Test Mode", value=st.session_state.auto_sniper_active)
st.session_state.auto_sniper_active = auto_forward_test
daily_state["auto_sniper_active"] = auto_forward_test

runner_mode = st.sidebar.toggle("Enable Trailing Runner Mode (Past 70+ pts)", value=st.session_state.runner_mode_active)
st.session_state.runner_mode_active = runner_mode
daily_state["runner_mode_active"] = runner_mode
save_daily_state(daily_state)

# --- MATH & BLACK-SCHOLES GREEKS FUNCTIONS ---
fn_bs_price = lambda S, K, T, r, sigma, opt_type: (
    S * norm.cdf((np.log(S/K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))) - 
    K * np.exp(-r * T) * norm.cdf(((np.log(S/K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))) - sigma * np.sqrt(T))
) if opt_type == "CE" else (
    K * np.exp(-r * T) * norm.cdf(-(((np.log(S/K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))) - sigma * np.sqrt(T))) - 
    S * norm.cdf(-((np.log(S/K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))))
)

def calculate_implied_volatility(market_price, S, K, T, r, opt_type="CE"):
    sigma = 0.20
    for _ in range(100):
        price = fn_bs_price(S, K, T, r, sigma, opt_type)
        d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
        vega = S * norm.pdf(d1) * np.sqrt(T)
        diff = price - market_price
        if abs(diff) < 1e-5: return round(sigma * 100, 2)
        if vega == 0: break
        sigma -= diff / vega
        sigma = max(0.01, min(sigma, 3.00))
    return round(sigma * 100, 2)

def calculate_greeks(S, K, T, r, sigma, option_type="CE"):
    T = max(T, 0.0001)
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    if option_type == "CE":
        delta = norm.cdf(d1)
        theta = (- (S * norm.pdf(d1) * sigma) / (2 * np.sqrt(T)) - r * K * np.exp(-r * T) * norm.cdf(d2)) / 365
    else:
        delta = norm.cdf(d1) - 1
        theta = (- (S * norm.pdf(d1) * sigma) / (2 * np.sqrt(T)) + r * K * np.exp(-r * T) * norm.cdf(-d2)) / 365
    gamma = norm.pdf(d1) / (S * sigma * np.sqrt(T))
    vega = (S * norm.pdf(d1) * np.sqrt(T)) / 100
    return round(delta, 2), round(theta, 2), round(gamma, 4), round(vega, 2)

def calculate_sniper_parameters(ltp, target_pts):
    cp = float(ltp)
    entry = round(cp * 0.98, 2)
    sl = round(entry * 0.82, 2)
