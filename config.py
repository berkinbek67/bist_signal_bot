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

# --- Strategy settings ---
EMA_FAST = 9
EMA_SLOW = 21
EMA_TREND = 200           # longer-term trend filter

RSI_PERIOD = 14

MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

BB_PERIOD = 20            # Bollinger Band lookback window
BB_STD_DEV = 2            # how many standard deviations for the bands

VOLUME_AVG_PERIOD = 20    # rolling window to compare current volume against

# Score ranges from -6 to +6 (six factors). Alert fires when |score| >= this.
ALERT_SCORE_THRESHOLD = 2

# Fraction of the current Bollinger Band width used for the suggested
# "invalidation" reference level in alerts (0.25 = 25% of the band width).
STOP_BAND_FRACTION = 0.25

# --- How often the bot checks for a new signal (only used when run locally in a loop) ---
CHECK_INTERVAL_SECONDS = 60
