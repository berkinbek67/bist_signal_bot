"""
Crypto Signal Bot (Spot trading version)
------------------------------------------
Pulls price + volume history from CoinGecko, scores six weighted
indicators, and decides BUY (enter spot) / HOLD (do nothing) /
SELL (exit spot) -- never a short position.

Position state (are we currently holding, at what price) is stored in
a small JSON file so decisions are consistent across separate runs.

This does NOT place real trades on any exchange. It only sends alerts
and tracks a hypothetical position so it can reason about entries/exits.
"""

import json
import os
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
    INDICATOR_WEIGHTS,
    BUY_THRESHOLD,
    STRONG_BUY_THRESHOLD,
    SELL_THRESHOLD,
    STRONG_SELL_THRESHOLD,
    FEE_RATE,
    MIN_PROFIT_MARGIN,
    ALLOW_STRONG_SELL_OVERRIDE,
    STATE_FILE,
    TARGET_BAND_FRACTION,
    STOP_BAND_FRACTION,
    ALWAYS_NOTIFY,
    CHECK_INTERVAL_SECONDS,
)

COINGECKO_URL_TEMPLATE = "https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"


# ---------- Data + indicators ----------

def fetch_candles(coin_id: str, vs_currency: str, days: int = 1) -> pd.DataFrame:
    """Fetch recent price + volume history from CoinGecko (no API key needed)."""
    url = COINGECKO_URL_TEMPLATE.format(coin_id=coin_id)
    params = {"vs_currency": vs_currency, "days": days}
    response = requests.get(url, params=params, timeout=10)
    response.raise_for_status()
    raw = response.json()

    prices = raw["prices"]
    volumes = raw["total_volumes"]

    df = pd.DataFrame(prices, columns=["timestamp", "close"])
    df["volume"] = [v[1] for v in volumes]
    df["close_time"] = pd.to_datetime(df["timestamp"], unit="ms")
    return df[["close_time", "close", "volume"]]


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add every indicator the scoring system needs. Only uses data up to
    the current row -- no future information leaks in (no look-ahead)."""
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

    return df


def score_signal(df: pd.DataFrame) -> dict:
    """Score six factors from -1/0/+1 each, then apply weights."""
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

    raw_total = sum(breakdown.values())
    weighted_total = sum(val * INDICATOR_WEIGHTS[name] for name, val in breakdown.items())

    return {"raw_total": raw_total, "weighted_total": weighted_total, "breakdown": breakdown}


def classify(weighted_score: int) -> str:
    """Turn the weighted score into a label. Range is -9 to +9."""
    if weighted_score >= STRONG_BUY_THRESHOLD:
        return "STRONG BUY"
    if weighted_score >= BUY_THRESHOLD:
        return "BUY"
    if weighted_score <= STRONG_SELL_THRESHOLD:
        return "STRONG SELL"
    if weighted_score <= SELL_THRESHOLD:
        return "SELL"
    return "HOLD"


# ---------- Position state (persists across runs via state.json) ----------

def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"in_position": False, "entry_price": None, "entry_time": None}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def decide_action(classification: str, state: dict, current_price: float) -> tuple[str, str]:
    """
    Decide the actual action given the classification AND current position
    state. This is what prevents excessive trading: a BUY/SELL label alone
    is not enough -- we also check whether it's a valid transition, and
    for exits, whether the move clears trading costs.

    Returns (action, reason) where action is one of BUY / SELL / HOLD.
    """
    in_position = state.get("in_position", False)

    if not in_position:
        if classification in ("BUY", "STRONG BUY"):
            return "BUY", "Entering spot position"
        return "HOLD", "Not in position, no buy signal"

    # We ARE in position -- only ever consider exiting, never shorting.
    if classification in ("SELL", "STRONG SELL"):
        entry_price = state.get("entry_price")
        if entry_price is None:
            return "SELL", "In position with no recorded entry price -- exiting to be safe"

        pnl_pct = (current_price - entry_price) / entry_price
        round_trip_cost = 2 * FEE_RATE + MIN_PROFIT_MARGIN

        if classification == "STRONG SELL" and ALLOW_STRONG_SELL_OVERRIDE:
            return "SELL", f"STRONG SELL overrides fee filter (P/L {pnl_pct:+.2%})"

        if abs(pnl_pct) >= round_trip_cost:
            return "SELL", f"Move clears trading costs (P/L {pnl_pct:+.2%} vs {round_trip_cost:.2%} threshold)"

        return "HOLD", f"SELL signal too weak to clear fees (P/L {pnl_pct:+.2%} vs {round_trip_cost:.2%} threshold)"

    return "HOLD", "In position, no exit signal"


# ---------- Reference price levels ----------

def compute_price_range(df: pd.DataFrame, action: str) -> dict | None:
    """
    Suggest entry/target/invalidation levels as DISTANCES from the current
    price, scaled by current volatility (Bollinger Band width). This is a
    reference level based on current volatility, NOT a prediction.

    Using distances (rather than raw bb_upper/bb_lower values) guarantees
    target is always on the favorable side of entry and invalidation
    always on the adverse side -- fixing the ordering bug where an
    absolute band level could end up on the wrong side of entry.
    """
    if action not in ("BUY", "SELL"):
        return None

    curr = df.iloc[-1]
    price = curr["close"]
    band_width = curr["bb_upper"] - curr["bb_lower"]

    if action == "BUY":
        return {
            "entry": price,
            "target": price + TARGET_BAND_FRACTION * band_width,
            "invalidation": price - STOP_BAND_FRACTION * band_width,
        }
    else:  # SELL (exit)
        return {
            "entry": price,
            "target": price - TARGET_BAND_FRACTION * band_width,
            "invalidation": price + STOP_BAND_FRACTION * band_width,
        }


# ---------- Telegram ----------

def send_telegram_message(text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    response = requests.post(url, data=payload, timeout=10)
    if not response.ok:
        print(f"[!] Telegram send failed: {response.status_code} {response.text}")


def build_status_message(df: pd.DataFrame, result: dict, classification: str) -> str:
    curr = df.iloc[-1]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    breakdown_lines = "\n".join(
        f"  {name}: {'+' if val > 0 else ''}{val} (weight {INDICATOR_WEIGHTS[name]})"
        for name, val in result["breakdown"].items()
    )
    return (
        f"Status for {COIN_ID}/{VS_CURRENCY}\n"
        f"Price: {curr['close']:.2f}\n"
        f"RSI: {curr['rsi']:.1f}\n"
        f"Weighted score: {result['weighted_total']:+d}/9 -> {classification}\n"
        f"{breakdown_lines}\n"
        f"Time: {now}"
    )


def reply_to_pending_messages(status_text: str) -> None:
    """Reply once if you've texted the bot since the last run. Uses
    Telegram's own offset tracking, so no extra storage is needed."""
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


# ---------- Main loop ----------

def run_once() -> None:
    df = fetch_candles(COIN_ID, VS_CURRENCY, HISTORY_DAYS)
    df = compute_indicators(df)
    result = score_signal(df)
    classification = classify(result["weighted_total"])

    current_price = df.iloc[-1]["close"]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    label = f"{COIN_ID}/{VS_CURRENCY}"

    state = load_state()
    action, reason = decide_action(classification, state, current_price)

    print(f"[{now}] {label} price={current_price} weighted_score={result['weighted_total']} "
          f"classification={classification} action={action} ({reason})")
    print(f"    breakdown: {result['breakdown']}")
    print(f"    state: {state}")

    reply_to_pending_messages(build_status_message(df, result, classification))

    if action == "BUY":
        state = {"in_position": True, "entry_price": current_price, "entry_time": now}
        save_state(state)
    elif action == "SELL":
        entry_price = state.get("entry_price") or current_price
        pnl_pct = (current_price - entry_price) / entry_price
        state = {"in_position": False, "entry_price": None, "entry_time": None}
        save_state(state)

    if action in ("BUY", "SELL"):
        price_range = compute_price_range(df, action)
        range_lines = ""
        if price_range:
            range_lines = (
                f"\nReference levels (not a prediction):\n"
                f"  Entry: {price_range['entry']:.2f}\n"
                f"  Target: {price_range['target']:.2f}\n"
                f"  Invalidation: {price_range['invalidation']:.2f}\n"
            )
        pnl_line = f"\nRealized P/L: {pnl_pct:+.2%}\n" if action == "SELL" else ""
        message = (
            f"{action} on {label} ({classification}, weighted score {result['weighted_total']:+d}/9)\n"
            f"Price: {current_price:.2f}\n"
            f"Reason: {reason}\n"
            f"{pnl_line}"
            f"{range_lines}"
            f"Time: {now}"
        )
        send_telegram_message(message)
    elif ALWAYS_NOTIFY:
        send_telegram_message(build_status_message(df, result, classification))


def main() -> None:
    print(f"Starting spot signal bot for {COIN_ID}/{VS_CURRENCY}. Checking every {CHECK_INTERVAL_SECONDS}s.")
    while True:
        try:
            run_once()
        except Exception as e:
            print(f"[!] Error: {e}")
        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
