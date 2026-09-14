"""
Supertrend
----------
Python port of the classic "Supertrend" indicator (ATR-based trend
following). This is a well-known, widely reproduced open-source
indicator, not a proprietary one.

Needs real OHLC (high/low/close) candles. For BIST, Yahoo Finance's
chart API (see bist_data.py) already provides full OHLCV in one call,
so this module only needs the actual indicator math -- no separate
data-fetching functions like the earlier crypto version required.
"""

import pandas as pd


def compute_supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0,
                        use_wilder_atr: bool = True) -> pd.DataFrame:
    """
    Direct port of the Pine Script's recursive up/dn/trend logic. Each
    value only depends on the PREVIOUS bar's already-finalized values --
    same causal structure as the original indicator, so this is safe to
    reuse in a walk-forward backtest loop with no look-ahead.
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
