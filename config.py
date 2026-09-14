import os
from dotenv import load_dotenv

load_dotenv()

# --- Telegram settings (fill these in your .env file, or GitHub secrets) ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# --- Market settings (Forex/Commodities via Yahoo Finance) ---
# Only 3 instruments now: Brent Crude, Gold/USD, Gold/EUR (computed as a
# cross rate since Yahoo has no direct XAUEUR ticker).
WATCHLIST = ["BZ=F", "XAUUSD=X", "XAUEUR"]

# XAUEUR isn't a real Yahoo ticker -- when scanning it, fetch these two
# instead and divide (see fetch_ohlc_cross in bist_data.py).
CROSS_RATE_PAIRS = {
    "XAUEUR": ("XAUUSD=X", "EURUSD=X"),
}

OHLC_RANGE = "1mo"    # how far back to fetch (needs 200+ candles for EMA200)
OHLC_INTERVAL = "15m" # candle size

# --- Morning scan ---
# Sends a single ranked "top N" message once per day, in this specific
# time window (Istanbul time). No position/state tracking needed -- this
# just checks "is it currently within this window" on every run.
MORNING_SCAN_HOUR = 10
MORNING_SCAN_MINUTE_WINDOW = (15, 29)  # fires once, in this 15-min window
MORNING_SCAN_TOP_N = 5

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
# Funding Rate dropped -- it's a crypto perpetual-futures concept with no
# equivalent for stocks. Max possible score = sum of weights = 11.
INDICATOR_WEIGHTS = {
    "EMA crossover": 2,
    "Trend (vs EMA200)": 2,
    "MACD": 2,
    "Supertrend": 2,
    "RSI": 1,
    "Volume": 1,
    "Bollinger Bands": 1,
}

# --- Signal frequency ---
# Scaled down proportionally from the crypto version's thresholds to
# match the new max score of 11 (was 13). Still on the loose/frequent
# side by design -- revisit once you've seen real BIST signal volume.
BUY_THRESHOLD = 2
STRONG_BUY_THRESHOLD = 5

# --- Trading cost assumptions ---
# IMPORTANT: Turkish brokerage commissions vary a lot by broker (often a
# small % commission plus BSMV tax on that commission) -- these are
# placeholder values. Replace with your actual broker's real numbers
# before trusting the fee-vs-target math.
FEE_RATE = 0.0015         # placeholder -- confirm your broker's real commission
MIN_PROFIT_MARGIN = 0.002 # extra buffer above pure fee breakeven

# --- Reference price levels (target / invalidation) ---
# Calculated as distances from entry using current volatility (Bollinger
# Band width). Carried over from the crypto version -- BIST stocks have
# different volatility characteristics, so these likely need re-tuning
# once you've seen real data (this is flagged as a to-do, not done yet).
TARGET_BAND_FRACTION = 0.25
STOP_BAND_FRACTION = 0.15

# --- Local loop mode (only used if you run main.py continuously yourself) ---
CHECK_INTERVAL_SECONDS = 60
