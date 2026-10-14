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
