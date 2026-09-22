import os
from dotenv import load_dotenv

load_dotenv()

# --- Telegram settings (fill these in your .env file, or GitHub secrets) ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# --- Market settings (Nasdaq/QQQ via Yahoo Finance) ---
# Brent (BZ=F) dropped -- Midas charges a flat $1.5 commission per
# transaction, which made the scalp-style trading this bot was tuned
# for uneconomical there. QQQ stays in for tracking/notification
# purposes even though the same commission applies -- you're not
# trading it live off these alerts, just watching how it performs.
WATCHLIST = ["QQQ"]

# Symbols messaged as plain AL/SAT instead of "LONG/SHORT pozisyon aç".
# Empty for now -- every instrument uses the same LONG/SHORT framing.
SPOT_STYLE_SYMBOLS = []

# XAUEUR isn't a real Yahoo ticker -- when scanning it, fetch these two
# instead and divide (see fetch_ohlc_cross in bist_data.py).
CROSS_RATE_PAIRS = {}

OHLC_RANGE = "5d"    # Yahoo only keeps 1-minute data for the last 7 days -- 5d stays safely inside that limit
OHLC_INTERVAL = "1m" # candle size -- now on a 1-minute timeframe as requested

# --- BIST daily picks scan ---
# Runs once a day (its own GitHub Actions schedule, see
# bist_daily_scan.py / bist-daily-picks.yml -- NOT a continuous
# in-code time check), scores every ticker in BIST_30_WATCHLIST on
# DAILY candles, and reports only the ones that actually cross the BUY
# threshold that day. This is informational only, not a live intraday
# trigger -- Yahoo's BIST data delay is fine for a once-a-day read but
# was exactly why BIST got dropped as a live scalping target earlier.
BIST_DAILY_RANGE = "2y"
BIST_DAILY_INTERVAL = "1d"

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
    "Fair Value Gap": 1,    # same reasoning as Liquidity Sweep -- ICT concept, no
                            # academic backtesting track record, fires often on 1m
                            # candles, and overlaps conceptually with Liquidity Sweep.
                            # Bump to 2 later if it proves itself in practice.
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
