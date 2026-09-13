"""
Supertrend
----------
Python port of the classic "Supertrend" indicator (ATR-based trend
following). This is a well-known, widely reproduced open-source
indicator, not a proprietary one.

Needs real OHLC (high/low/close) candles, which our main price feed
(CoinGecko) doesn't provide -- so this pulls candles directly from an
exchange's public spot market API instead. Binance spot is tried first
(different service from the futures API that gets blocked on GitHub's
servers), with Bybit spot as a fallback.
"""

import requests
import pandas as pd
from funding_rate import BASE_SYMBOL_MAP


def fetch_ohlc_binance(base: str, interval: str = "5m", limit: int = 200) -> pd.DataFrame:
    symbol = f"{base}USDT"
    url = "https://api.binance.com/api/v3/klines"
    response = requests.get(url, params={"symbol": symbol, "interval": interval, "limit": limit}, timeout=10)
    response.raise_for_status()
    raw = response.json()
    df = pd.DataFrame(raw, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "qav", "trades", "tbbav", "tbqav", "ignore",
    ])
    for col in ["high", "low", "close"]:
        df[col] = df[col].astype(float)
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms")
    return df[["close_time", "high", "low", "close"]]


def fetch_ohlc_bybit(base: str, interval: str = "5", limit: int = 200) -> pd.DataFrame:
    symbol = f"{base}USDT"
    url = "https://api.bybit.com/v5/market/kline"
    response = requests.get(
        url, params={"category": "spot", "symbol": symbol, "interval": interval, "limit": limit}, timeout=10
    )
    response.raise_for_status()
    raw = response.json()["result"]["list"]
    # Bybit returns newest-first: [start, open, high, low, close, volume, turnover]
    rows = list(reversed(raw))
    df = pd.DataFrame(rows, columns=["start", "open", "high", "low", "close", "volume", "turnover"])
    for col in ["high", "low", "close"]:
        df[col] = df[col].astype(float)
    df["close_time"] = pd.to_datetime(df["start"].astype(float), unit="ms")
    return df[["close_time", "high", "low", "close"]]


def fetch_ohlc(coin_id: str) -> pd.DataFrame:
    """Try Binance spot first, fall back to Bybit spot if it fails."""
    base = BASE_SYMBOL_MAP.get(coin_id)
    if base is None:
        raise ValueError(f"No ticker mapping for '{coin_id}' in BASE_SYMBOL_MAP")

    try:
        return fetch_ohlc_binance(base)
    except Exception as e:
        print(f"[!] Binance spot OHLC failed ({e}), trying Bybit spot...")
        return fetch_ohlc_bybit(base)


def compute_supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0,
                        use_wilder_atr: bool = True) -> pd.DataFrame:
    """
    Direct port of the Pine Script's recursive up/dn/trend logic. Each
    value only depends on the PREVIOUS bar's already-finalized values --
    same causal structure as the original indicator, so this is safe to
    reuse in the backtest's walk-forward loop with no look-ahead.
    """
    df = df.copy()
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)

    true_range = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    if use_wilder_atr:
        atr = true_range.ewm(alpha=1 / period, adjust=False).mean()
    else:
        atr = true_range.rolling(period).mean()

    src = (high + low) / 2  # hl2, matches the Pine Script's default source
    basic_up = src - multiplier * atr
    basic_dn = src + multiplier * atr

    n = len(df)
    final_up = [None] * n
    final_dn = [None] * n
    trend = [1] * n

    for i in range(n):
        if i == 0 or final_up[i - 1] is None:
            final_up[i] = basic_up.iloc[i]
        elif close.iloc[i - 1] > final_up[i - 1]:
            final_up[i] = max(basic_up.iloc[i], final_up[i - 1])
        else:
            final_up[i] = basic_up.iloc[i]

        if i == 0 or final_dn[i - 1] is None:
            final_dn[i] = basic_dn.iloc[i]
        elif close.iloc[i - 1] < final_dn[i - 1]:
            final_dn[i] = min(basic_dn.iloc[i], final_dn[i - 1])
        else:
            final_dn[i] = basic_dn.iloc[i]

        if i == 0:
            trend[i] = 1
        else:
            prev_trend = trend[i - 1]
            if prev_trend == -1 and close.iloc[i] > final_dn[i - 1]:
                trend[i] = 1
            elif prev_trend == 1 and close.iloc[i] < final_up[i - 1]:
                trend[i] = -1
            else:
                trend[i] = prev_trend

    df["supertrend_up"] = final_up
    df["supertrend_dn"] = final_dn
    df["supertrend_trend"] = trend
    return df


def score_supertrend(trend_value: int) -> int:
    """+1 while in an uptrend, -1 while in a downtrend. Supertrend has
    no neutral state by design -- it's always one or the other."""
    return 1 if trend_value == 1 else -1
