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
from bist_data import fetch_ohlc_yahoo, fetch_ohlc_cross, is_forex_market_open, is_us_stock_market_open
from supertrend import compute_supertrend, score_supertrend
from config import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    WATCHLIST,
    CROSS_RATE_PAIRS,
    OHLC_RANGE,
    OHLC_INTERVAL,
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
    SELL_THRESHOLD,
    STRONG_SELL_THRESHOLD,
    TARGET_BAND_FRACTION,
    STOP_BAND_FRACTION,
    CHECK_INTERVAL_SECONDS,
)

# Which market-hours check applies to each symbol. Anything not listed
# here defaults to is_forex_market_open (Brent/gold's near-24/5 schedule).
MARKET_HOURS_CHECKS = {
    "QQQ": is_us_stock_market_open,
}


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
    if weighted_score <= STRONG_SELL_THRESHOLD:
        return "STRONG SELL"
    if weighted_score <= SELL_THRESHOLD:
        return "SELL"
    return "NO SIGNAL"


def compute_price_range(df: pd.DataFrame, direction: str = "LONG") -> dict:
    """
    Entry/Target/Invalidation as distances from current price, scaled by
    volatility (Bollinger Band width). For LONG: target is above entry,
    invalidation below. For SHORT: mirrored -- target below entry,
    invalidation above. Same distances either way, just flipped sign.
    """
    curr = df.iloc[-1]
    price = curr["close"]
    band_width = curr["bb_upper"] - curr["bb_lower"]

    if direction == "SHORT":
        return {
            "entry": price,
            "target": price - TARGET_BAND_FRACTION * band_width,
            "invalidation": price + STOP_BAND_FRACTION * band_width,
        }
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
    """Fetch, score, and classify one ticker. Handles both regular Yahoo
    tickers and computed cross-rate symbols (e.g. XAUEUR). Returns None
    if data couldn't be fetched (skipped, not a hard failure)."""
    try:
        if symbol in CROSS_RATE_PAIRS:
            base_ticker, quote_ticker = CROSS_RATE_PAIRS[symbol]
            df = fetch_ohlc_cross(base_ticker, quote_ticker, range_=OHLC_RANGE, interval=OHLC_INTERVAL)
        else:
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
    """Latest daily close vs the previous daily close, as a % change.
    Handles both regular tickers and computed cross-rate symbols."""
    try:
        if symbol in CROSS_RATE_PAIRS:
            base_ticker, quote_ticker = CROSS_RATE_PAIRS[symbol]
            df = fetch_ohlc_cross(base_ticker, quote_ticker, range_="5d", interval="1d")
        else:
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
    (after the trading day has fully closed and settled). With a small
    watchlist, this just lists everything sorted by change -- no
    separate gainers/losers split, since that would just repeat the same
    handful of items twice."""
    changes = [c for c in (fetch_daily_change(s) for s in WATCHLIST) if c is not None]
    if not changes:
        print("[!] No daily change data available for night recap, skipping.")
        return

    ranked = sorted(changes, key=lambda c: c["pct_change"], reverse=True)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = ["Yesterday's recap", ""]
    for c in ranked:
        lines.append(f"  {c['symbol']}: {c['pct_change']:+.2f}% ({c['prev_close']:.2f} -> {c['latest_close']:.2f})")
    lines.append(f"\nTime: {now}")

    send_telegram_message("\n".join(lines))


# ---------- Message formatting (Turkish, Style 3 / dashboard) ----------

DISPLAY_NAMES = {
    "BZ=F": "BRENT",
    "GC=F": "XAUUSD",
    "XAUEUR": "XAUEUR",
}

CLASSIFICATION_TR = {
    "BUY": "AL",
    "STRONG BUY": "GÜÇLÜ AL",
    "SELL": "SAT",
    "STRONG SELL": "GÜÇLÜ SAT",
}

DIRECTION_LINE_TR = {
    "BUY": "→ LONG pozisyon aç",
    "STRONG BUY": "→ LONG pozisyon aç",
    "SELL": "→ SHORT pozisyon aç",
    "STRONG SELL": "→ SHORT pozisyon aç",
}


def format_eu_number(value: float, decimals: int = 4) -> str:
    """European/Turkish number style: period for thousands, comma for
    decimals -- e.g. 3758.1713 becomes '3.758,1713'."""
    raw = f"{value:,.{decimals}f}"  # e.g. '3,758.1713' (US style)
    return raw.replace(",", "TEMP").replace(".", ",").replace("TEMP", ".")


def build_buy_alert_message(symbol: str, entry: dict, price_range: dict, now: datetime) -> str:
    display_name = DISPLAY_NAMES.get(symbol, symbol)
    classification = entry["classification"]
    label_tr = CLASSIFICATION_TR.get(classification, classification)
    direction_line = DIRECTION_LINE_TR.get(classification, "")
    max_score = sum(INDICATOR_WEIGHTS.values())
    check_time_str = now.strftime("%H:%M UTC")

    # The actual timestamp of the candle this price came from -- this is
    # what tells you how stale the data really is, NOT the check time
    # above (which is just when the bot happened to run).
    data_time = entry["df"].iloc[-1]["close_time"]
    data_time_str = data_time.strftime("%H:%M UTC")

    return (
        f"━━━━━━━━━━━━━\n"
        f"  {display_name} · {label_tr}\n"
        f"━━━━━━━━━━━━━\n"
        f"{direction_line}\n"
        f"Skor       {entry['result']['weighted_total']:+d}/{max_score}\n"
        f"Giriş      {format_eu_number(price_range['entry'])}\n"
        f"Hedef      {format_eu_number(price_range['target'])}\n"
        f"Stop       {format_eu_number(price_range['invalidation'])}\n"
        f"Veri saati {data_time_str}  (fiyat bu ana ait)\n"
        f"Kontrol    {check_time_str}  (bot bu ana kontrol etti)\n"
        f"━━━━━━━━━━━━━"
    )


def run_once() -> None:
    now = datetime.now(timezone.utc)

    print(f"[{now}] Checking {len(WATCHLIST)} instruments...")

    scanned = []
    for symbol in WATCHLIST:
        market_check = MARKET_HOURS_CHECKS.get(symbol, is_forex_market_open)
        if not market_check(now):
            print(f"    {symbol}: market closed, skipping.")
            continue

        entry = scan_ticker(symbol)
        if entry is None:
            continue
        scanned.append(entry)

        print(f"    {symbol}: price={entry['price']:.4f} score={entry['result']['weighted_total']} "
              f"classification={entry['classification']}")

        if entry["classification"] in ("BUY", "STRONG BUY"):
            price_range = compute_price_range(entry["df"], direction="LONG")
            message = build_buy_alert_message(symbol, entry, price_range, now)
            send_telegram_message(message)
        elif entry["classification"] in ("SELL", "STRONG SELL"):
            price_range = compute_price_range(entry["df"], direction="SHORT")
            message = build_buy_alert_message(symbol, entry, price_range, now)
            send_telegram_message(message)

    if scanned:
        reply_to_pending_messages(f"Last scan covered {len(scanned)}/{len(WATCHLIST)} instruments.")


def main() -> None:
    print(f"Starting signal bot for {len(WATCHLIST)} instruments.")
    while True:
        try:
            run_once()
        except Exception as e:
            print(f"[!] Error: {e}")
        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
