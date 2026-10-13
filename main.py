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
    page_title="Kotegawa Quant Engine - Autonomous AI Sniper Terminal v15.10 Pro",
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

# --- 3. PERSISTENT PERMANENT STATE HANDLING (`daily_state.json`) ---
STATE_FILE = "daily_state.json"

def get_today_str():
    return str(datetime.date.today())

def load_daily_state():
    state = {
        "date": get_today_str(),
        "access_token": None,
        "request_token": None,
        "paper_ledger": [],
        "audit_logs": [],
        "auto_sniper_active": False,
        "runner_mode_active": True
    }
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                data = json.load(f)
                state["paper_ledger"] = data.get("paper_ledger", [])
                state["audit_logs"] = data.get("audit_logs", [])
                state["access_token"] = data.get("access_token", None)
                state["request_token"] = data.get("request_token", None)
                state["auto_sniper_active"] = data.get("auto_sniper_active", False)
                state["runner_mode_active"] = data.get("runner_mode_active", True)
        except Exception:
            pass
    return state

def save_daily_state(state):
    state["date"] = get_today_str()
    if "paper_ledger" not in state: state["paper_ledger"] = []
    if "audit_logs" not in state: state["audit_logs"] = []
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=4)

daily_state = load_daily_state()

refresh_count = st_autorefresh(interval=30000, key="quant_engine_live_refresh")

if "paper_ledger" not in st.session_state: st.session_state.paper_ledger = daily_state.get("paper_ledger", [])
if "audit_logs" not in st.session_state: st.session_state.audit_logs = daily_state.get("audit_logs", [])
if "auto_sniper_active" not in st.session_state: st.session_state.auto_sniper_active = daily_state.get("auto_sniper_active", False)
if "runner_mode_active" not in st.session_state: st.session_state.runner_mode_active = daily_state.get("runner_mode_active", True)
if "market_behavior_memory" not in st.session_state:
    st.session_state.market_behavior_memory = {
        "total_sessions_observed": 1,
        "optimal_win_hour": "09:30 - 11:00 AM",
        "adaptive_rr_bias": 4.0,
        "learned_insight": "Awaiting initial forward-test data to calibrate live Fibonacci weights."
    }

st.markdown(
    """
    <style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    .stDeployButton {display: none !important;}
    header[data-testid="stHeader"] { background-color: transparent !important; visibility: visible !important; height: 0px !important; }
    .stApp { background-color: #0b0f19; color: #f0f6fc; }
    .stTabs [data-baseweb="tab-list"] button [data-testid="stMarkdownContainer"] p { color: #f0f6fc !important; font-weight: 700 !important; font-size: 1.05rem !important; }
    .stTabs [data-baseweb="tab-list"] button[aria-selected="true"] [data-testid="stMarkdownContainer"] p { color: #58a6ff !important; font-weight: 800 !important; }
    label[data-testid="stWidgetLabel"] p, label[data-testid="stWidgetLabel"] span { color: #ffffff !important; font-weight: 700 !important; font-size: 0.95rem !important; }
    .terminal-banner { background: linear-gradient(135deg, #161b22 0%, #0d1117 100%); border: 1px solid #30363d; padding: 25px; border-radius: 12px; text-align: center; margin-bottom: 25px; box-shadow: 0 4px 15px rgba(0,0,0,0.6); }
    .terminal-title { color: #ffffff; font-size: 2.2rem; font-weight: 800; letter-spacing: 1px; margin-bottom: 5px; }
    .terminal-subtitle { color: #8b949e; font-size: 1.05rem; font-weight: 500; }
    .metric-card { background-color: #1
