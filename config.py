import os
from dotenv import load_dotenv

load_dotenv()

# --- Telegram settings (fill these in your .env file, or GitHub secrets) ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# --- Market settings (CoinGecko) ---
COIN_ID = "bitcoin"       # CoinGecko coin id, e.g. bitcoin, ethereum, solana
VS_CURRENCY = "usd"       # currency to price against
HISTORY_DAYS = 1          # how much history to pull each check (1 = ~5min granularity)

# --- Indicator settings ---
EMA_FAST = 9
EMA_SLOW = 21
EMA_TREND = 200

RSI_PERIOD = 14

MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

BB_PERIOD = 20
BB_STD_DEV = 2

VOLUME_AVG_PERIOD = 20

# --- Weighted scoring ---
# Trend/momentum indicators count more than short-term confirmation ones.
# Funding rate and Supertrend are genuinely independent information
# (leveraged trader positioning, and a well-established ATR trend model)
# rather than more of the same price-derived math, so both are weighted
# like trend indicators. Max possible score = sum of all weights = 13.
INDICATOR_WEIGHTS = {
    "EMA crossover": 2,
    "Trend (vs EMA200)": 2,
    "MACD": 2,
    "Funding Rate": 2,
    "Supertrend": 2,
    "RSI": 1,
    "Volume": 1,
    "Bollinger Bands": 1,
}

# Thresholds on the WEIGHTED score (range -13 to +13)
BUY_THRESHOLD = 6
STRONG_BUY_THRESHOLD = 10
SELL_THRESHOLD = -6
STRONG_SELL_THRESHOLD = -10

# Supertrend parameters (from the original Pine Script defaults)
SUPERTREND_PERIOD = 10
SUPERTREND_MULTIPLIER = 3.0

# --- Spot trading behavior ---
# This bot only ever does: BUY (enter), HOLD (do nothing), SELL (exit).
# It never opens a short position.

# Trading cost assumptions (make these match your actual exchange).
# FEE_RATE is per trade (one side). A round trip (buy + sell) costs 2x this.
FEE_RATE = 0.001          # 0.1% per trade, adjust to your exchange's real fee
MIN_PROFIT_MARGIN = 0.002 # extra buffer required above pure fee breakeven

# A STRONG SELL always exits regardless of the fee filter (treated as a
# risk-cutting signal, not a profit-taking one). A regular SELL only exits
# if the position is far enough in profit (or loss) to clear costs.
ALLOW_STRONG_SELL_OVERRIDE = True

# Where position state (are we currently holding, at what entry price) is
# stored between runs, since each GitHub Actions run starts fresh.
STATE_FILE = "state.json"

# --- Reference price levels (target / invalidation) ---
# Calculated as distances from entry using current volatility (Bollinger
# Band width), NOT as absolute band levels -- this guarantees target is
# always on the favorable side and invalidation always on the adverse
# side, regardless of where price currently sits relative to the bands.
TARGET_BAND_FRACTION = 0.75
STOP_BAND_FRACTION = 0.25

# --- Notifications ---
# If True, sends a status message on every check even with no trade action.
ALWAYS_NOTIFY = False

# --- Local loop mode (only used if you run main.py continuously yourself) ---
CHECK_INTERVAL_SECONDS = 60
