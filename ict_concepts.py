"""
ICT concepts (Kill Zone, Equal Highs/Lows)
--------------------------------------------
Popular retail trading concepts from the "ICT" (Inner Circle Trader)
methodology. Unlike EMA/RSI/MACD, these don't have the same body of
academic backtesting behind them -- treat this as a well-defined but
less rigorously validated addition, not an established indicator.

Kill Zone: a time-window filter, not a directional signal. Sources
vary somewhat on exact times, but for Nasdaq/QQQ specifically, the
consensus "high activity" window is the first ~90 minutes after the
9:30 ET cash open (09:30-11:00 ET).

Liquidity Sweep: price levels where two or more recent swing highs
(or lows) cluster closely together are treated as resting liquidity
pools. Unlike a plain "equal highs/lows" proximity check, this looks
for the actual ICT event: price has to (1) break past the level --
a high/low wick trading through it, grabbing the stop orders resting
there -- and then (2) close back on the other side of it, confirming
the reversal. Proximity alone (price just sitting near the level) no
longer scores anything; the level has to actually get swept and
rejected.
"""

from datetime import datetime, timezone
import pandas as pd
from bist_data import NY_TZ

QQQ_KILL_ZONE_START_HOUR = 9
QQQ_KILL_ZONE_START_MINUTE = 30
QQQ_KILL_ZONE_END_HOUR = 12  # widened from 11:00 -- matches the broader
QQQ_KILL_ZONE_END_MINUTE = 0  # "NY AM session" window some ICT sources use


def is_qqq_kill_zone(now: datetime | None = None) -> bool:
    """True during the 09:30-11:00 ET window -- the consensus
    highest-activity period for Nasdaq/QQQ per ICT sources."""
    if now is None:
        now = datetime.now(timezone.utc)
    now_ny = now.astimezone(NY_TZ)

    start = now_ny.replace(hour=QQQ_KILL_ZONE_START_HOUR, minute=QQQ_KILL_ZONE_START_MINUTE, second=0, microsecond=0)
    end = now_ny.replace(hour=QQQ_KILL_ZONE_END_HOUR, minute=QQQ_KILL_ZONE_END_MINUTE, second=0, microsecond=0)
    return start <= now_ny < end


def find_swing_points(df: pd.DataFrame, window: int = 5) -> tuple[list, list]:
    """
    Find local swing highs and lows: a candle's high is a swing high if
    it's the maximum within `window` candles on each side (same logic
    for lows, using the minimum). Only uses data within the given
    window on both sides, so this is inherently a LAGGING detector --
    a swing point isn't confirmed until `window` candles after it,
    same as any real swing-point method. No look-ahead risk when used
    on data strictly up to the current point, since we only ever look
    at swings that are already `window` candles old by construction.
    """
    highs = df["high"].values
    lows = df["low"].values
    n = len(df)

    swing_highs = []
    swing_lows = []

    for i in range(window, n - window):
        local_highs = highs[i - window: i + window + 1]
        local_lows = lows[i - window: i + window + 1]
        if highs[i] == local_highs.max():
            swing_highs.append((i, highs[i]))
        if lows[i] == local_lows.min():
            swing_lows.append((i, lows[i]))

    return swing_highs, swing_lows


def find_equal_level_zones(points: list, tolerance_pct: float = 0.001) -> list:
    """
    Given a list of (index, price) swing points, find clusters where
    two or more are within tolerance_pct of each other -- these are
    "equal highs" or "equal lows" zones. Returns the price levels of
    each such cluster (as the average of the clustered points).
    """
    if len(points) < 2:
        return []

    prices = sorted(p for _, p in points)
    zones = []
    cluster = [prices[0]]

    for p in prices[1:]:
        if abs(p - cluster[-1]) / cluster[-1] <= tolerance_pct:
            cluster.append(p)
        else:
            if len(cluster) >= 2:
                zones.append(sum(cluster) / len(cluster))
            cluster = [p]
    if len(cluster) >= 2:
        zones.append(sum(cluster) / len(cluster))

    return zones


def find_equal_level_zones_indexed(points: list, tolerance_pct: float = 0.001) -> list:
    """
    Same clustering as find_equal_level_zones, but also returns the
    index of the most recent point in each cluster -- i.e. the candle
    at which the level became a confirmed 2+ point equal-high/low zone.
    Used so the sweep detector only looks for a sweep AFTER the level
    actually existed (no look-ahead).
    Returns a list of (avg_price, formed_at_index) tuples.
    """
    if len(points) < 2:
        return []

    pts_sorted = sorted(points, key=lambda p: p[1])
    zones = []
    cluster = [pts_sorted[0]]

    for pt in pts_sorted[1:]:
        if abs(pt[1] - cluster[-1][1]) / cluster[-1][1] <= tolerance_pct:
            cluster.append(pt)
        else:
            if len(cluster) >= 2:
                avg_price = sum(p for _, p in cluster) / len(cluster)
                formed_at = max(i for i, _ in cluster)
                zones.append((avg_price, formed_at))
            cluster = [pt]
    if len(cluster) >= 2:
        avg_price = sum(p for _, p in cluster) / len(cluster)
        formed_at = max(i for i, _ in cluster)
        zones.append((avg_price, formed_at))

    return zones


def score_liquidity_sweep(
    df: pd.DataFrame,
    window: int = 5,
    tolerance_pct: float = 0.001,
    sweep_lookback: int = 20,
) -> int:
    """
    Detects an actual liquidity sweep event -- not just proximity to a
    level:

    Bearish sweep (-1): an equal-highs zone got a high wick traded
    through it (stop-hunt above the highs), and price has since closed
    back BELOW that level -- the breakout failed and reversed down.

    Bullish sweep (+1): an equal-lows zone got a low wick traded
    through it (stop-hunt below the lows), and price has since closed
    back ABOVE that level -- the breakdown failed and reversed up.

    `sweep_lookback` caps how many candles ago the sweep wick is
    allowed to have happened, so a sweep from days ago doesn't keep
    scoring forever. Only uses swing points/candles at or before the
    current candle, so this is look-ahead safe: nothing here depends
    on any bar past df.iloc[-1].
    """
    swing_highs, swing_lows = find_swing_points(df, window=window)
    high_zones = find_equal_level_zones_indexed(swing_highs, tolerance_pct)
    low_zones = find_equal_level_zones_indexed(swing_lows, tolerance_pct)

    highs = df["high"].values
    lows = df["low"].values
    n = len(df)
    current_idx = n - 1
    current_close = df.iloc[-1]["close"]

    bearish_sweep_idx = None
    for level, formed_idx in high_zones:
        search_start = formed_idx + 1
        if search_start > current_idx:
            continue
        for i in range(search_start, n):
            if highs[i] > level:
                if current_close < level and (current_idx - i) <= sweep_lookback:
                    if bearish_sweep_idx is None or i > bearish_sweep_idx:
                        bearish_sweep_idx = i
                break  # only the first touch of this level counts as "the sweep"

    bullish_sweep_idx = None
    for level, formed_idx in low_zones:
        search_start = formed_idx + 1
        if search_start > current_idx:
            continue
        for i in range(search_start, n):
            if lows[i] < level:
                if current_close > level and (current_idx - i) <= sweep_lookback:
                    if bullish_sweep_idx is None or i > bullish_sweep_idx:
                        bullish_sweep_idx = i
                break

    if bullish_sweep_idx is not None and bearish_sweep_idx is not None:
        return 1 if bullish_sweep_idx > bearish_sweep_idx else -1
    if bullish_sweep_idx is not None:
        return 1
    if bearish_sweep_idx is not None:
        return -1
    return 0
