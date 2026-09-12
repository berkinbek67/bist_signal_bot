import os
from dotenv import load_dotenv

load_dotenv()

# --- Telegram settings (fill these in your .env file) ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# --- Market settings (CoinGecko) ---
COIN_ID = "bitcoin"       # CoinGecko coin id, e.g. bitcoin, ethereum, solana
VS_CURRENCY = "usd"       # currency to price against
HISTORY_DAYS = 1          # how much history to pull each check (1 = ~5min granularity)

# --- Strategy settings ---
EMA_FAST = 9
EMA_SLOW = 21
RSI_PERIOD = 14

# --- How often the bot checks for a new signal ---
CHECK_INTERVAL_SECONDS = 60
