"""
Crypto Signal Bot
------------------
Pulls price history from CoinGecko's public API, checks a simple EMA
crossover + RSI rule, and sends you a Telegram message when it fires.

This does NOT place trades. It only sends alerts.
"""

import time
import requests
import pandas as pd
from datetime import datetime, timezone
from config import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    COIN_ID,
    VS_CURRENCY,
    HISTORY_DAYS,
    EMA_FAST,
    EMA_SLOW,
    RSI_PERIOD,
    CHECK_INTERVAL_SECONDS,
)

COINGECKO_URL_TEMPLATE = "https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"


def fetch_candles(coin_id: str, vs_currency: str, days: int = 1) -> pd.DataFrame:
    """Fetch recent price history from CoinGecko (no API key needed)."""
    url = COINGECKO_URL_TEMPLATE.format(coin_id=coin_id)
    params = {"vs_currency": vs_currency, "days": days}
    response = requests.get(url, params=params, timeout=10)
    response.raise_for_status()
    raw = response.json()

    prices = raw["prices"]  # list of [timestamp_ms, price]
    df = pd.DataFrame(prices, columns=["timestamp", "close"])
    df["close_time"] = pd.to_datetime(df["timestamp"], unit="ms")
    return df[["close_time", "close"]]


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add EMA fast/slow and RSI columns to the dataframe."""
    df = df.copy()
    df["ema_fast"] = df["close"].ewm(span=EMA_FAST, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=EMA_SLOW, adjust=False).mean()

    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(RSI_PERIOD).mean()
    avg_loss = loss.rolling(RSI_PERIOD).mean()
    rs = avg_gain / avg_loss
    df["rsi"] = 100 - (100 / (1 + rs))

    return df


def check_signal(df: pd.DataFrame) -> str | None:
    """
    Look at the last two candles to see if a crossover just happened.
    Returns 'BUY', 'SELL', or None.
    """
    prev, curr = df.iloc[-2], df.iloc[-1]

    crossed_up = prev["ema_fast"] <= prev["ema_slow"] and curr["ema_fast"] > curr["ema_slow"]
    crossed_down = prev["ema_fast"] >= prev["ema_slow"] and curr["ema_fast"] < curr["ema_slow"]

    if crossed_up and curr["rsi"] < 70:
        return "BUY"
    if crossed_down and curr["rsi"] > 30:
        return "SELL"
    return None


def send_telegram_message(text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    response = requests.post(url, data=payload, timeout=10)
    if not response.ok:
        print(f"[!] Telegram send failed: {response.status_code} {response.text}")


def run_once() -> None:
    df = fetch_candles(COIN_ID, VS_CURRENCY, HISTORY_DAYS)
    df = compute_indicators(df)
    signal = check_signal(df)

    last_close = df.iloc[-1]["close"]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    label = f"{COIN_ID}/{VS_CURRENCY}"
    print(f"[{now}] {label} price={last_close} rsi={df.iloc[-1]['rsi']:.1f} signal={signal}")

    if signal:
        message = (
            f"{signal} signal on {label}\n"
            f"Price: {last_close}\n"
            f"RSI: {df.iloc[-1]['rsi']:.1f}\n"
            f"Time: {now}"
        )
        send_telegram_message(message)


def main() -> None:
    print(f"Starting signal bot for {COIN_ID}/{VS_CURRENCY}. Checking every {CHECK_INTERVAL_SECONDS}s.")
    while True:
        try:
            run_once()
        except Exception as e:
            print(f"[!] Error: {e}")
        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
