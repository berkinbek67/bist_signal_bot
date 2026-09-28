import os
from dotenv import load_dotenv

load_dotenv()

# --- Telegram settings (fill these in your .env file, or GitHub secrets) ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# --- Market settings (US indices via a local MetaTrader 5 terminal) ---
# Brent (BZ=F) dropped -- Midas charges a flat $1.5 commission per
# transaction, which made the scalp-style trading this bot was tuned
# for uneconomical there.
#
# Data source switched from Yahoo Finance to a locally-running, logged-in
# MT5 terminal (see mt5_data.py) -- the Yahoo ^NDX/^SPX feed was giving
# prices that didn't match the real market, even allowing for a
# reasonable delay. MT5 gives your broker's own live feed instead, but
# ONLY works while MT5 is open and logged in on this same Windows
# machine -- this is why the bot no longer runs on GitHub Actions and
# instead runs continuously here (see CHECK_INTERVAL_SECONDS below).
#
# These are YOUR broker's own symbol names for the Nasdaq-100 and S&P
# 500 index CFDs, exactly as they appear in MT5's Market Watch -- they
# vary by broker (e.g. some use "NAS100"/"US100" and "US500" instead).
# IMPORTANT: still index/CFD instruments, not necessarily the exact
# security you'd place a real order on -- use as a directional read and
# confirm against your broker's live price before trading.
#
# XAUUSD (gold) added too -- unlike the indices, it trades nearly 24/5
# (not tied to the NYSE/Nasdaq cash session), so it's deliberately left
# out of KILL_ZONE_SYMBOLS and HTF_FILTER_SYMBOLS below: no time-window
# gating, no hourly-trend confluence check, just a plain threshold-based
# alert (same style as the old Brent/gold setup). Add it to either list
# later if you want those filters applied to it too.
WATCHLIST = ["NASDAQ", "SPX500", "XAUUSD"]

# Symbols messaged as plain AL/SAT instead of "LONG/SHORT pozisyon aç".
# Empty for now -- every instrument uses the same LONG/SHORT framing.
SPOT_STYLE_SYMBOLS = []

# XAUEUR isn't a real MT5/Yahoo ticker -- when scanning it, fetch these
# two instead and divide (see fetch_ohlc_cross in bist_data.py). Not
# currently wired up for the MT5 path -- only used by the Yahoo fetch.
CROSS_RATE_PAIRS = {}

OHLC_INTERVAL = "1m"  # candle size for the intraday index scan
OHLC_MT5_COUNT = 500  # how many recent 1-minute candles to pull from MT5 each scan (enough for EMA200 + a safety margin)

# Both NASDAQ and SPX500 trade on the same NYSE/Nasdaq cash session and
# get the same ICT NY AM kill-zone treatment -- neither is BIST/forex-style.
KILL_ZONE_SYMBOLS = ["NASDAQ", "SPX500"]

# --- Higher-timeframe confluence filter ---
# The 1-minute signal is checked against the 1-hour trend before it's
# allowed to fire -- catches the case where a 1-min liquidity sweep/FVG
# rejection fires bullish while the broader hourly trend is actually
# still bearish (countertrend noise, not a real confluence setup). Only
# applied to symbols listed in HTF_FILTER_SYMBOLS; a data-fetch failure
# or a genuinely mixed hourly read (EMA200 and Supertrend disagree)
# does NOT block the signal -- only a clearly OPPOSING hourly trend does.
HTF_FILTER_SYMBOLS = ["NASDAQ", "SPX500"]
HTF_INTERVAL = "60m"
HTF_MT5_COUNT = 300  # how many recent hourly candles to pull from MT5 for the HTF check

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
    "Session Liquidity Sweep": 1,  # Asia/London/New York session high-low sweeps --
                            # same lightweight weighting as the other ICT-style
                            # factors above. Added specifically for XAUUSD (gold
                            # reacts strongly to session opens/closes), but applies
                            # to every WATCHLIST symbol, same as Liquidity Sweep/FVG.
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
