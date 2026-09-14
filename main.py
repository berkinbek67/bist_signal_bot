"""
BIST Signal Bot
----------------
Scans a watchlist of Borsa Istanbul stocks (via Yahoo Finance), scores
each on 7 weighted indicators, and:
  - Sends an individual BUY alert (with Entry/Target/Invalidation) for
    any stock whose score crosses the threshold, any time during market
    hours.
  - Sends one ranked "Top N" summary message shortly after market open.

Only runs during BIST's regular session (10:00-18:00 Istanbul time,
Mon-Fri) -- outside those hours, it does nothing.

This does NOT track a position or send active SELL alerts -- you place
your own limit sell order at the Target price (and optionally a stop at
Invalidation) manually on your broker's platform. Every check is fully
independent; there is no state carried between runs.

This does NOT place real trades. It only sends alerts.
"""

import time
import requests
import pandas as pd
from datetime import datetime, timezone
from bist_data import fetch_ohlc_yahoo, is_market_open, ISTANBUL_TZ
from supertrend import compute_supertrend, score_supertrend
from config import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    WATCHLIST,
    OHLC_RANGE,
    OHLC_INTERVAL,
    MORNING_SCAN_HOUR,
    MORNING_SCAN_MINUTE_WINDOW,
    MORNING_SCAN_TOP_N,
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
    INDICATOR_WEIGHTS,
    BUY_THRESHOLD,
    STRONG_BUY_THRESHOLD,
    TARGET_BAND_FRACTION,
    STOP_BAND_FRACTION,
    CHECK_INTERVAL_SECONDS,
)


# ---------- Indicators ----------

def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add every indicator the scoring system needs, including Supertrend
    (Yahoo gives us high/low/close together, so no separate fetch needed
    like the crypto version required). Only uses data up to the current
    row -- no future information leaks in (no look-ahead)."""
    df = df.copy()

    df["ema_fast"] = df["close"].ewm(span=EMA_FAST, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=EMA_SLOW, adjust=False).mean()
    df["ema_trend"] = df["close"].ewm(span=EMA_TREND, adjust=False).mean()

    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(RSI_PERIOD).mean()
    avg_loss = loss.rolling(RSI_PERIOD).mean()
    rs = avg_gain / avg_loss
    df["rsi"] = 100 - (100 / (1 + rs))

    ema_macd_fast = df["close"].ewm(span=MACD_FAST, adjust=False).mean()
    ema_macd_slow = df["close"].ewm(span=MACD_SLOW, adjust=False).mean()
    df["macd"] = ema_macd_fast - ema_macd_slow
    df["macd_signal"] = df["macd"].ewm(span=MACD_SIGNAL, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]

    bb_mid = df["close"].rolling(BB_PERIOD).mean()
    bb_std = df["close"].rolling(BB_PERIOD).std()
    df["bb_upper"] = bb_mid + BB_STD_DEV * bb_std
    df["bb_lower"] = bb_mid - BB_STD_DEV * bb_std

    df["volume_avg"] = df["volume"].rolling(VOLUME_AVG_PERIOD).mean()

    df = compute_supertrend(df)

    return df


def score_signal(df: pd.DataFrame) -> dict:
    """Score seven factors from -1/0/+1 each, then apply weights."""
    prev, curr = df.iloc[-2], df.iloc[-1]
    breakdown = {}

    breakdown["EMA crossover"] = 1 if curr["ema_fast"] > curr["ema_slow"] else -1
    breakdown["Trend (vs EMA200)"] = 1 if curr["close"] > curr["ema_trend"] else -1

    if curr["rsi"] < 30:
        breakdown["RSI"] = 1
    elif curr["rsi"] > 70:
        breakdown["RSI"] = -1
    else:
        breakdown["RSI"] = 0

    breakdown["MACD"] = 1 if curr["macd_hist"] > 0 else -1

    price_up = curr["close"] > prev["close"]
    high_volume = curr["volume"] > curr["volume_avg"]
    if high_volume and price_up:
        breakdown["Volume"] = 1
    elif high_volume and not price_up:
        breakdown["Volume"] = -1
    else:
        breakdown["Volume"] = 0

    if curr["close"] <= curr["bb_lower"]:
        breakdown["Bollinger Bands"] = 1
    elif curr["close"] >= curr["bb_upper"]:
        breakdown["Bollinger Bands"] = -1
    else:
        breakdown["Bollinger Bands"] = 0

    breakdown["Supertrend"] = score_supertrend(curr["supertrend_trend"])

    raw_total = sum(breakdown.values())
    weighted_total = sum(val * INDICATOR_WEIGHTS[name] for name, val in breakdown.items())

    return {"raw_total": raw_total, "weighted_total": weighted_total, "breakdown": breakdown}


def classify(weighted_score: int) -> str:
    if weighted_score >= STRONG_BUY_THRESHOLD:
        return "STRONG BUY"
    if weighted_score >= BUY_THRESHOLD:
        return "BUY"
    return "NO SIGNAL"


def compute_price_range(df: pd.DataFrame) -> dict:
    curr = df.iloc[-1]
    price = curr["close"]
    band_width = curr["bb_upper"] - curr["bb_lower"]
    return {
        "entry": price,
        "target": price + TARGET_BAND_FRACTION * band_width,
        "invalidation": price - STOP_BAND_FRACTION * band_width,
    }


# ---------- Telegram ----------

def send_telegram_message(text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    response = requests.post(url, data=payload, timeout=10)
    if not response.ok:
        print(f"[!] Telegram send failed: {response.status_code} {response.text}")


def reply_to_pending_messages(status_text: str) -> None:
    base_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    try:
        response = requests.get(f"{base_url}/getUpdates", timeout=10)
        response.raise_for_status()
        updates = response.json().get("result", [])
    except Exception as e:
        print(f"[!] Could not check for incoming messages: {e}")
        return

    if not updates:
        return

    relevant = [
        u for u in updates
        if str(u.get("message", {}).get("chat", {}).get("id")) == str(TELEGRAM_CHAT_ID)
    ]
    if relevant:
        send_telegram_message(status_text)

    max_update_id = max(u["update_id"] for u in updates)
    try:
        requests.get(f"{base_url}/getUpdates", params={"offset": max_update_id + 1}, timeout=10)
    except Exception as e:
        print(f"[!] Could not acknowledge messages: {e}")


# ---------- Core scan ----------

def scan_ticker(symbol: str) -> dict | None:
    """Fetch, score, and classify one ticker. Returns None if data for
    this symbol couldn't be fetched (skipped, not a hard failure)."""
    try:
        df = fetch_ohlc_yahoo(symbol, range_=OHLC_RANGE, interval=OHLC_INTERVAL)
        if len(df) < 210:
            print(f"[!] {symbol}: not enough candles yet ({len(df)}), skipping")
            return None
        df = compute_indicators(df)
        result = score_signal(df)
        classification = classify(result["weighted_total"])
        current_price = df.iloc[-1]["close"]
        return {
            "symbol": symbol,
            "price": current_price,
            "result": result,
            "classification": classification,
            "df": df,
        }
    except Exception as e:
        print(f"[!] {symbol}: scan failed ({e})")
        return None


def is_morning_scan_window(now_istanbul: datetime) -> bool:
    start_min, end_min = MORNING_SCAN_MINUTE_WINDOW
    return now_istanbul.hour == MORNING_SCAN_HOUR and start_min <= now_istanbul.minute <= end_min


def send_morning_summary(scanned: list[dict]) -> None:
    ranked = sorted(scanned, key=lambda s: s["result"]["weighted_total"], reverse=True)
    top = ranked[:MORNING_SCAN_TOP_N]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    lines = [f"Top {len(top)} by score this morning:"]
    for s in top:
        lines.append(f"  {s['symbol']}: {s['result']['weighted_total']:+d} ({s['classification']}) @ {s['price']:.2f}")
    lines.append(f"\nTime: {now}")
    send_telegram_message("\n".join(lines))


def fetch_daily_change(symbol: str) -> dict | None:
    """Latest daily close vs the previous daily close, as a % change."""
    try:
        df = fetch_ohlc_yahoo(symbol, range_="5d", interval="1d")
        if len(df) < 2:
            return None
        prev_close = df.iloc[-2]["close"]
        latest_close = df.iloc[-1]["close"]
        pct_change = (latest_close - prev_close) / prev_close * 100
        return {"symbol": symbol, "prev_close": prev_close, "latest_close": latest_close, "pct_change": pct_change}
    except Exception as e:
        print(f"[!] {symbol}: daily change lookup failed ({e})")
        return None


def send_night_recap() -> None:
    """A recap of the watchlist's daily moves, meant to run once overnight
    (after the trading day has fully closed and settled)."""
    changes = [c for c in (fetch_daily_change(s) for s in WATCHLIST) if c is not None]
    if not changes:
        print("[!] No daily change data available for night recap, skipping.")
        return

    ranked = sorted(changes, key=lambda c: c["pct_change"], reverse=True)
    top_n = min(MORNING_SCAN_TOP_N, len(ranked))
    gainers = ranked[:top_n]
    losers = ranked[-top_n:][::-1]  # worst first

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = ["Yesterday's watchlist recap", "", "Top gainers:"]
    for c in gainers:
        lines.append(f"  {c['symbol']}: {c['pct_change']:+.2f}% ({c['prev_close']:.2f} -> {c['latest_close']:.2f})")
    lines.append("")
    lines.append("Top losers:")
    for c in losers:
        lines.append(f"  {c['symbol']}: {c['pct_change']:+.2f}% ({c['prev_close']:.2f} -> {c['latest_close']:.2f})")
    lines.append(f"\nTime: {now}")

    send_telegram_message("\n".join(lines))


def run_once() -> None:
    now_istanbul = datetime.now(ISTANBUL_TZ)

    if not is_market_open(now_istanbul):
        print(f"[{now_istanbul}] BIST closed, skipping.")
        return

    print(f"[{now_istanbul}] BIST open, scanning {len(WATCHLIST)} tickers...")

    scanned = []
    for symbol in WATCHLIST:
        entry = scan_ticker(symbol)
        if entry is None:
            continue
        scanned.append(entry)

        print(f"    {symbol}: price={entry['price']:.2f} score={entry['result']['weighted_total']} "
              f"classification={entry['classification']}")

        if entry["classification"] in ("BUY", "STRONG BUY"):
            price_range = compute_price_range(entry["df"])
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            message = (
                f"{entry['classification']} on {symbol} (weighted score {entry['result']['weighted_total']:+d})\n"
                f"Price: {entry['price']:.2f}\n\n"
                f"Entry: {price_range['entry']:.2f}\n"
                f"Target (sell here): {price_range['target']:.2f}\n"
                f"Invalidation (stop): {price_range['invalidation']:.2f}\n\n"
                f"Time: {now}"
            )
            send_telegram_message(message)

    if is_morning_scan_window(now_istanbul) and scanned:
        send_morning_summary(scanned)

    if scanned:
        reply_to_pending_messages(f"Last scan covered {len(scanned)}/{len(WATCHLIST)} tickers.")


def main() -> None:
    print(f"Starting BIST signal bot for {len(WATCHLIST)} tickers.")
    while True:
        try:
            run_once()
        except Exception as e:
            print(f"[!] Error: {e}")
        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
