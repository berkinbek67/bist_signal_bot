"""
Crypto Signal Bot
------------------
Pulls price + volume history from CoinGecko's public API, scores six
independent indicators (EMA crossover, EMA200 trend, RSI, MACD, volume
confirmation, Bollinger Bands), and messages you on Telegram when the
combined score is strongly bullish or bearish.

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
    EMA_TREND,
    RSI_PERIOD,
    MACD_FAST,
    MACD_SLOW,
    MACD_SIGNAL,
    BB_PERIOD,
    BB_STD_DEV,
    VOLUME_AVG_PERIOD,
    ALERT_SCORE_THRESHOLD,
    CHECK_INTERVAL_SECONDS,
)

COINGECKO_URL_TEMPLATE = "https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"


def fetch_candles(coin_id: str, vs_currency: str, days: int = 1) -> pd.DataFrame:
    """Fetch recent price + volume history from CoinGecko (no API key needed)."""
    url = COINGECKO_URL_TEMPLATE.format(coin_id=coin_id)
    params = {"vs_currency": vs_currency, "days": days}
    response = requests.get(url, params=params, timeout=10)
    response.raise_for_status()
    raw = response.json()

    prices = raw["prices"]         # [timestamp_ms, price]
    volumes = raw["total_volumes"]  # [timestamp_ms, volume]

    df = pd.DataFrame(prices, columns=["timestamp", "close"])
    df["volume"] = [v[1] for v in volumes]
    df["close_time"] = pd.to_datetime(df["timestamp"], unit="ms")
    return df[["close_time", "close", "volume"]]


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add every indicator the scoring system needs."""
    df = df.copy()

    # EMAs
    df["ema_fast"] = df["close"].ewm(span=EMA_FAST, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=EMA_SLOW, adjust=False).mean()
    df["ema_trend"] = df["close"].ewm(span=EMA_TREND, adjust=False).mean()

    # RSI
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(RSI_PERIOD).mean()
    avg_loss = loss.rolling(RSI_PERIOD).mean()
    rs = avg_gain / avg_loss
    df["rsi"] = 100 - (100 / (1 + rs))

    # MACD
    ema_macd_fast = df["close"].ewm(span=MACD_FAST, adjust=False).mean()
    ema_macd_slow = df["close"].ewm(span=MACD_SLOW, adjust=False).mean()
    df["macd"] = ema_macd_fast - ema_macd_slow
    df["macd_signal"] = df["macd"].ewm(span=MACD_SIGNAL, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]

    # Bollinger Bands
    bb_mid = df["close"].rolling(BB_PERIOD).mean()
    bb_std = df["close"].rolling(BB_PERIOD).std()
    df["bb_upper"] = bb_mid + BB_STD_DEV * bb_std
    df["bb_lower"] = bb_mid - BB_STD_DEV * bb_std

    # Volume average
    df["volume_avg"] = df["volume"].rolling(VOLUME_AVG_PERIOD).mean()

    return df


def score_signal(df: pd.DataFrame) -> dict:
    """
    Score six independent factors from -1 to +1 each and sum them.
    Returns a dict with the total score and a breakdown for the message.
    """
    prev, curr = df.iloc[-2], df.iloc[-1]
    breakdown = {}

    # 1. EMA9/EMA21 crossover direction
    breakdown["EMA crossover"] = 1 if curr["ema_fast"] > curr["ema_slow"] else -1

    # 2. Price vs EMA200 (broader trend filter)
    breakdown["Trend (vs EMA200)"] = 1 if curr["close"] > curr["ema_trend"] else -1

    # 3. RSI extremes
    if curr["rsi"] < 30:
        breakdown["RSI"] = 1   # oversold -> bullish bias
    elif curr["rsi"] > 70:
        breakdown["RSI"] = -1  # overbought -> bearish bias
    else:
        breakdown["RSI"] = 0

    # 4. MACD histogram sign
    breakdown["MACD"] = 1 if curr["macd_hist"] > 0 else -1

    # 5. Volume-confirmed price move
    price_up = curr["close"] > prev["close"]
    high_volume = curr["volume"] > curr["volume_avg"]
    if high_volume and price_up:
        breakdown["Volume"] = 1
    elif high_volume and not price_up:
        breakdown["Volume"] = -1
    else:
        breakdown["Volume"] = 0

    # 6. Bollinger Band position (mean-reversion bias)
    if curr["close"] <= curr["bb_lower"]:
        breakdown["Bollinger Bands"] = 1   # stretched low -> possible bounce
    elif curr["close"] >= curr["bb_upper"]:
        breakdown["Bollinger Bands"] = -1  # stretched high -> possible pullback
    else:
        breakdown["Bollinger Bands"] = 0

    total = sum(breakdown.values())
    return {"total": total, "breakdown": breakdown}


def classify(score: int) -> str:
    if score >= 4:
        return "STRONG BUY"
    if score >= ALERT_SCORE_THRESHOLD:
        return "BUY"
    if score <= -4:
        return "STRONG SELL"
    if score <= -ALERT_SCORE_THRESHOLD:
        return "SELL"
    return "NEUTRAL"


def send_telegram_message(text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    response = requests.post(url, data=payload, timeout=10)
    if not response.ok:
        print(f"[!] Telegram send failed: {response.status_code} {response.text}")


def run_once() -> None:
    df = fetch_candles(COIN_ID, VS_CURRENCY, HISTORY_DAYS)
    df = compute_indicators(df)
    result = score_signal(df)
    verdict = classify(result["total"])

    last_close = df.iloc[-1]["close"]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    label = f"{COIN_ID}/{VS_CURRENCY}"

    print(f"[{now}] {label} price={last_close} score={result['total']} verdict={verdict}")
    print(f"    breakdown: {result['breakdown']}")

    if abs(result["total"]) >= ALERT_SCORE_THRESHOLD:
        breakdown_lines = "\n".join(
            f"  {name}: {'+' if val > 0 else ''}{val}" for name, val in result["breakdown"].items()
        )
        message = (
            f"{verdict} ({result['total']:+d}/6) on {label}\n"
            f"Price: {last_close}\n"
            f"{breakdown_lines}\n"
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
