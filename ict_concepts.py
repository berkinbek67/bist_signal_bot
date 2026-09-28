"""
ICT concepts (Kill Zone, Equal Highs/Lows)
--------------------------------------------
Popular retail trading concepts from the "ICT" (Inner Circle Trader)
methodology. Unlike EMA/RSI/MACD, these don't have the same body of
academic backtesting behind them -- treat this as a well-defined but
less rigorously validated addition, not an established indicator.

Kill Zone: a time-window filter, not a directional signal. Sources
vary somewhat on exact times, but for major US index instruments
(Nasdaq-100, S&P 500), the consensus "high activity" window is the
NY AM session, starting at the 9:30 ET cash open.

Liquidity Sweep: price levels where two or more recent swing highs
(or lows) cluster closely together are treated as resting liquidity
pools. Unlike a plain "equal highs/lows" proximity check, this looks
for the actual ICT event: price has to (1) break past the level --
a high/low wick trading through it, grabbing the stop orders resting
there -- and then (2) close back on the other side of it, confirming
the reversal. Proximity alone (price just sitting near the level) no
longer scores anything; the level has to actually get swept and
rejected.

Fair Value Gap (FVG): a 3-candle imbalance -- candle 1 and candle 3
don't overlap, leaving a price gap that candle 2 blew straight
through. That gap is treated as a support zone (if it was a bullish/
up-gap) or a resistance zone (if bearish/down-gap) that price tends
to revisit later. Scored the same way as Liquidity Sweep: price has
to actually trade back INTO the gap and then close back out on the
far side (a rejection), not just sit near it. A gap that gets fully
closed through instead of rejected is "used up" and stops signaling.

Session Liquidity Sweep: the same sweep-and-reject logic as Liquidity
Sweep above, but the levels being swept are the Asia/London/New York
SESSION high and low (see SESSION_WINDOWS_UTC), not equal-highs/lows
swing clusters. Classic ICT "session liquidity" idea: each session's
high/low is resting liquidity that the next session often runs before
reversing. Only counts once the session that set the level has
actually closed -- a level from a session still in progress isn't
"live" yet.
"""

from datetime import datetime, timezone, timedelta, time
import pandas as pd
from bist_data import NY_TZ

US_INDEX_KILL_ZONE_START_HOUR = 9
US_INDEX_KILL_ZONE_START_MINUTE = 30
US_INDEX_KILL_ZONE_END_HOUR = 12  # widened from 11:00 -- matches the broader
US_INDEX_KILL_ZONE_END_MINUTE = 0  # "NY AM session" window some ICT sources use


def is_us_index_kill_zone(now: datetime | None = None) -> bool:
    """True during the 09:30-12:00 ET window -- the consensus
    highest-activity period for major US index instruments per ICT
    sources. Used for both Nasdaq-100 (^NDX) and S&P 500 (^SPX)."""
    if now is None:
        now = datetime.now(timezone.utc)
    now_ny = now.astimezone(NY_TZ)

    start = now_ny.replace(hour=US_INDEX_KILL_ZONE_START_HOUR, minute=US_INDEX_KILL_ZONE_START_MINUTE, second=0, microsecond=0)
    end = now_ny.replace(hour=US_INDEX_KILL_ZONE_END_HOUR, minute=US_INDEX_KILL_ZONE_END_MINUTE, second=0, microsecond=0)
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


def find_fair_value_gaps(df: pd.DataFrame, min_gap_pct: float = 0.0005) -> list:
    """
    Scans every 3-candle window (i-2, i-1, i) for a Fair Value Gap:

    Bullish FVG: candle i's low is above candle (i-2)'s high -- the
    middle candle rallied so hard it left a gap between them. The
    zone is [candle(i-2).high, candle(i).low], and it's treated as a
    support area price may return to.

    Bearish FVG: candle i's high is below candle (i-2)'s low -- the
    mirror image, a down-gap treated as a resistance area.

    `min_gap_pct` filters out microscopic gaps (as a fraction of
    price) so 1-minute noise doesn't generate a new zone every other
    candle. Only ever looks at candle i and two candles before it, so
    a gap is "confirmed" the moment candle i closes -- no look-ahead.

    Returns a list of dicts: {"type": "bullish"/"bearish", "top":
    float, "bottom": float, "formed_idx": int}.
    """
    highs = df["high"].values
    lows = df["low"].values
    n = len(df)
    zones = []

    for i in range(2, n):
        c1_high, c1_low = highs[i - 2], lows[i - 2]
        c3_high, c3_low = highs[i], lows[i]

        if c3_low > c1_high:
            gap_bottom, gap_top = c1_high, c3_low
            if (gap_top - gap_bottom) / gap_bottom >= min_gap_pct:
                zones.append({"type": "bullish", "top": gap_top, "bottom": gap_bottom, "formed_idx": i})

        if c3_high < c1_low:
            gap_bottom, gap_top = c3_high, c1_low
            if (gap_top - gap_bottom) / gap_bottom >= min_gap_pct:
                zones.append({"type": "bearish", "top": gap_top, "bottom": gap_bottom, "formed_idx": i})

    return zones


def score_fair_value_gap(
    df: pd.DataFrame,
    min_gap_pct: float = 0.0005,
    max_zone_age: int = 100,
) -> int:
    """
    +1 if price just dipped INTO an unfilled bullish FVG zone and
    closed back ABOVE it (support held -- rejection off the gap).

    -1 if price just rallied INTO an unfilled bearish FVG zone and
    closed back BELOW it (resistance held -- rejection off the gap).

    A zone only counts once: if some candle between its formation and
    now already closed all the way through it (fully filling the
    gap instead of rejecting off it), it's treated as used up and is
    skipped. `max_zone_age` caps how many candles old a gap can be
    before it stops being considered "live". Only ever looks at
    candles up to and including the current (last) one, so this is
    look-ahead safe.
    """
    zones = find_fair_value_gaps(df, min_gap_pct=min_gap_pct)
    n = len(df)
    current_idx = n - 1
    closes = df["close"].values

    curr = df.iloc[-1]
    current_close, current_low, current_high = curr["close"], curr["low"], curr["high"]

    candidates = [
        z for z in zones
        if z["formed_idx"] < current_idx and (current_idx - z["formed_idx"]) <= max_zone_age
    ]

    best_bullish_idx = None
    best_bearish_idx = None

    for z in candidates:
        # skip zones already fully closed-through (used up) before the current candle
        already_filled_through = False
        for j in range(z["formed_idx"] + 1, current_idx):
            if z["type"] == "bullish" and closes[j] < z["bottom"]:
                already_filled_through = True
                break
            if z["type"] == "bearish" and closes[j] > z["top"]:
                already_filled_through = True
                break
        if already_filled_through:
            continue

        if z["type"] == "bullish":
            touched = current_low <= z["top"]
            rejected = current_close > z["top"]
            if touched and rejected:
                if best_bullish_idx is None or z["formed_idx"] > best_bullish_idx:
                    best_bullish_idx = z["formed_idx"]
        else:
            touched = current_high >= z["bottom"]
            rejected = current_close < z["bottom"]
            if touched and rejected:
                if best_bearish_idx is None or z["formed_idx"] > best_bearish_idx:
                    best_bearish_idx = z["formed_idx"]

    if best_bullish_idx is not None and best_bearish_idx is not None:
        return 1 if best_bullish_idx > best_bearish_idx else -1
    if best_bullish_idx is not None:
        return 1
    if best_bearish_idx is not None:
        return -1
    return 0


# (start_hour, end_hour) in UTC. London/New York deliberately overlap
# (13:00-16:00 UTC) -- that's real, not a bug: those are the two most
# active sessions and they genuinely run concurrently for a few hours.
SESSION_WINDOWS_UTC = [
    (0, 8),    # Asia (Tokyo)
    (8, 16),   # London
    (13, 21),  # New York
]


def get_most_recent_session_range(
    df: pd.DataFrame, start_hour: int, end_hour: int, lookback_days: int = 5
):
    """
    Finds the most recently COMPLETED occurrence of the [start_hour,
    end_hour) UTC session window, walking backward day by day from the
    date of the current (last) candle until one is found that (a) has
    already closed as of the current candle and (b) actually has data
    in it (weekends / not-enough-history days are skipped).

    Returns (session_high, session_low, session_end_time), or None if
    nothing usable was found within lookback_days. Only ever looks at
    candles at or before df's last row -- look-ahead safe.
    """
    current_time = df.iloc[-1]["close_time"]
    current_date = current_time.date()

    for days_back in range(0, lookback_days + 1):
        day = current_date - timedelta(days=days_back)
        window_start = datetime.combine(day, time(hour=start_hour), tzinfo=timezone.utc)
        window_end = datetime.combine(day, time(hour=end_hour), tzinfo=timezone.utc)

        if window_end > current_time:
            continue  # this session hasn't closed yet as of the current candle

        mask = (df["close_time"] >= window_start) & (df["close_time"] < window_end)
        session_candles = df[mask]
        if len(session_candles) == 0:
            continue  # no candles in this window -- try the day before

        return session_candles["high"].max(), session_candles["low"].min(), window_end

    return None


def score_session_liquidity_sweep(df: pd.DataFrame, sweep_lookback: int = 20) -> int:
    """
    Same sweep-and-reject logic as score_liquidity_sweep, but the levels
    being swept are the Asia/London/New York SESSION high/low (see
    SESSION_WINDOWS_UTC) instead of equal-highs/lows swing clusters.

    Bearish sweep (-1): a session's high got wicked through, and price
    has since closed back below it.
    Bullish sweep (+1): a session's low got wicked through, and price
    has since closed back above it.

    Each session's level only becomes "live" for sweeping once that
    session has actually closed -- candles from before the session
    ended are never checked against it. Only ever looks at data up to
    and including the current (last) row -- look-ahead safe, same as
    score_liquidity_sweep.
    """
    highs = df["high"].values
    lows = df["low"].values
    n = len(df)
    current_idx = n - 1
    current_close = df.iloc[-1]["close"]

    bearish_sweep_idx = None
    bullish_sweep_idx = None

    for start_hour, end_hour in SESSION_WINDOWS_UTC:
        session = get_most_recent_session_range(df, start_hour, end_hour)
        if session is None:
            continue
        session_high, session_low, session_end_time = session

        after_session = df["close_time"] > session_end_time
        if not after_session.any():
            continue
        search_start = after_session.idxmax()  # first True -- df has a plain 0..n-1 index
        if search_start > current_idx:
            continue

        for i in range(search_start, n):
            if highs[i] > session_high:
                if current_close < session_high and (current_idx - i) <= sweep_lookback:
                    if bearish_sweep_idx is None or i > bearish_sweep_idx:
                        bearish_sweep_idx = i
                break  # only the first touch of this level counts as "the sweep"

        for i in range(search_start, n):
            if lows[i] < session_low:
                if current_close > session_low and (current_idx - i) <= sweep_lookback:
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
