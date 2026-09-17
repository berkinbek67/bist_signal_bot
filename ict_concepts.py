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

Equal Highs/Lows: price levels where two or more recent swing
highs (or lows) cluster closely together. ICT interpretation: these
are liquidity pools -- price often sweeps slightly past them (grabbing
stop orders) before reversing. Scored as a mean-reversion-style factor:
price near a cluster of equal highs -> bearish lean; near equal lows
-> bullish lean.
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


def score_equal_levels(df: pd.DataFrame, window: int = 5, tolerance_pct: float = 0.001) -> int:
    """
    +1 if current price is near a cluster of equal LOWS (potential
    liquidity sweep -> bullish reversal zone).
    -1 if near a cluster of equal HIGHS (potential liquidity sweep ->
    bearish reversal zone).
    0 otherwise.
    """
    swing_highs, swing_lows = find_swing_points(df, window=window)
    high_zones = find_equal_level_zones(swing_highs, tolerance_pct)
    low_zones = find_equal_level_zones(swing_lows, tolerance_pct)

    current_price = df.iloc[-1]["close"]

    for zone_price in high_zones:
        if abs(current_price - zone_price) / zone_price <= tolerance_pct:
            return -1
    for zone_price in low_zones:
        if abs(current_price - zone_price) / zone_price <= tolerance_pct:
            return 1
    return 0
