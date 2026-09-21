import os
from dotenv import load_dotenv

load_dotenv()

# --- Telegram settings (fill these in your .env file, or GitHub secrets) ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# --- Market settings (Forex/Commodities via Yahoo Finance) ---
# Only 3 instruments now: Brent Crude, Gold futures (COMEX), Gold/EUR
# (computed as a cross rate). Note: XAUUSD=X does NOT work on Yahoo's
# actual data API (confirmed via a live 404) despite appearing to exist
# as a quote page -- GC=F (Gold futures) is the real, working ticker.
# Like BZ=F, GC=F is a regulated futures contract, so it carries its own
# "COMEX - Delayed Quote" delay, similar to how BIST stocks were delayed
# -- this is NOT the fresher near-real-time data true spot forex has.
WATCHLIST = ["BZ=F", "QQQ"]

# Symbols messaged as plain AL/SAT instead of "LONG/SHORT pozisyon aç".
# Empty for now -- every instrument uses the same LONG/SHORT framing.
SPOT_STYLE_SYMBOLS = []

# XAUEUR isn't a real Yahoo ticker -- when scanning it, fetch these two
# instead and divide (see fetch_ohlc_cross in bist_data.py).
CROSS_RATE_PAIRS = {}

OHLC_RANGE = "5d"    # Yahoo only keeps 1-minute data for the last 7 days -- 5d stays safely inside that limit
OHLC_INTERVAL = "1m" # candle size -- now on a 1-minute timeframe as requested

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
    "Liquidity Sweep": 1,  # weighted like a confirmation factor, not a trend one --
                            # less rigorously validated than the others, so it gets
                            # a lighter vote rather than equal say with EMA/MACD/etc.
}

# --- Signal frequency ---
# Scaled down proportionally from the crypto version's thresholds to
# match the new max score of 11 (was 13). Still on the loose/frequent
# side by design -- revisit once you've seen real BIST signal volume.
BUY_THRESHOLD = 3
STRONG_BUY_THRESHOLD = 6

# Mirrors of the BUY thresholds, for short signals.
SELL_THRESHOLD = -3
STRONG_SELL_THRESHOLD = -6

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
