"""
ICT Advanced (2022 Mentorship Model additions)
-----------------------------------------------
Four toggleable ICT features layered on top of the existing bot,
without changing its existing weighted-scoring path (see config.py's
"ICT strategy extensions" section for the on/off switches and every
tunable number used below):

1. Kill Zone / Silver Bullet time windows -- multiple named NY-time
   windows (London, NY AM, NY PM) instead of the old single 09:30-12:00
   window. See get_active_kill_zone.

2. MSS + Displacement (the "2022 Mentorship Model" entry sequence):
   after a liquidity sweep, price must (a) shift market structure by
   CLOSING beyond the most recent opposite-side swing point, (b) on a
   "displacement" candle (body >= ATR * multiplier), and (c) that same
   move must leave a Fair Value Gap. All three, in that order -- if any
   step is missing, there's no valid setup. See evaluate_ict_entry_sequence.

3. Counter-liquidity take-profit: TP1/TP2 come from the nearest
   unclaimed liquidity ABOVE (long) or BELOW (short) price -- equal
   highs/lows, unswept swing points, or the Asia range's far edge --
   instead of a fixed volatility-band distance. Stop-loss sits beyond
   the sweep candle's wick. A setup is rejected if its R:R falls short
   of MIN_RR_RATIO. See compute_ict_trade_levels.

4. Asia range + Power of Three, used ONLY as a directional filter (it
   never generates a signal by itself): during the London kill zone,
   if the Asia range's low or high was swept and rejected, that sets
   the day's directional bias, and only signals matching that bias are
   allowed through until the next day's Asia range resets it. See
   compute_asia_range / compute_po3_bias.

Every function here is look-ahead safe in the same sense as
ict_concepts.py: nothing depends on any candle after the one being
evaluated (df.iloc[-1]).
"""

from datetime import datetime, timezone, timedelta, time as dtime
import pandas as pd

from bist_data import NY_TZ
from ict_concepts import find_swing_points, find_equal_level_zones_indexed, find_fair_value_gaps


# ---------- 1. Kill Zone / Silver Bullet windows ----------

def get_active_kill_zone(now_utc: datetime, windows: dict) -> str | None:
    """
    `windows` is a dict of {name: (start_hour, start_minute, end_hour,
    end_minute)} in NEW YORK LOCAL time (see config.ICT_KILL_ZONE_WINDOWS).
    Returns the name of the window we're currently in, or None if we're
    outside all of them. Uses zoneinfo's America/New_York, so DST
    transitions are handled automatically -- no hardcoded UTC offset.
    """
    now_ny = now_utc.astimezone(NY_TZ)
    for name, (sh, sm, eh, em) in windows.items():
        start = now_ny.replace(hour=sh, minute=sm, second=0, microsecond=0)
        end = now_ny.replace(hour=eh, minute=em, second=0, microsecond=0)
        if start <= now_ny < end:
            return name
    return None


# ---------- Shared helper: ATR ----------

def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder's ATR -- same formula/style as supertrend.py's internal
    ATR, exposed here as a reusable series so displacement and
    counter-liquidity tolerance can both use it."""
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    true_range = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return true_range.ewm(alpha=1 / period, adjust=False).mean()


# ---------- 2. MSS + Displacement + FVG sequence ----------

def _find_recent_sweep(df: pd.DataFrame, high_zones: list, low_zones: list, lookback: int):
    """
    Finds the most recent liquidity sweep: a candle whose wick traded
    through an equal-highs zone (-> "bearish" expected direction, buy-
    side liquidity grabbed) or an equal-lows zone (-> "bullish" expected
    direction, sell-side liquidity grabbed). Unlike
    ict_concepts.score_liquidity_sweep, this does NOT require price to
    have already closed back past the level -- that confirmation now
    comes from the MSS step instead. Returns the single most recent
    such event within `lookback` bars of the current candle, or None.
    """
    highs = df["high"].values
    lows = df["low"].values
    n = len(df)
    current_idx = n - 1

    events = []
    for level, formed_idx in high_zones:
        for i in range(formed_idx + 1, n):
            if highs[i] > level:
                events.append((i, level, "bearish"))
                break
    for level, formed_idx in low_zones:
        for i in range(formed_idx + 1, n):
            if lows[i] < level:
                events.append((i, level, "bullish"))
                break

    recent = [e for e in events if (current_idx - e[0]) <= lookback]
    if not recent:
        return None
    sweep_idx, level, direction = max(recent, key=lambda e: e[0])
    return {"sweep_idx": sweep_idx, "level": level, "direction": direction}


def _last_swing_before(swing_points: list, idx: int):
    """Most recent (index, price) in `swing_points` with index < idx,
    or None. `swing_points` is the raw list from find_swing_points
    (already sorted ascending by index)."""
    candidates = [p for p in swing_points if p[0] < idx]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p[0])


def _detect_mss_and_displacement(df: pd.DataFrame, sweep: dict, swing_highs: list, swing_lows: list,
                                  atr_series: pd.Series, displacement_multiplier: float):
    """
    Looks, candle by candle after the sweep, for the first candle that
    BOTH closes beyond the most recent opposite-side swing point (the
    Market Structure Shift) AND is itself a displacement candle (body
    >= ATR * displacement_multiplier). Simplification: the MSS-breaking
    candle and the displacement candle are treated as the same candle,
    rather than allowing a multi-candle move where they're different --
    keeps the sequence unambiguous. Returns {"mss_idx", "mss_level"} or
    None.
    """
    direction = sweep["direction"]
    sweep_idx = sweep["sweep_idx"]
    n = len(df)
    opens = df["open"].values
    closes = df["close"].values

    structure_point = _last_swing_before(swing_highs if direction == "bullish" else swing_lows, sweep_idx)
    if structure_point is None:
        return None
    structure_level = structure_point[1]

    for j in range(sweep_idx + 1, n):
        atr_j = atr_series.iloc[j]
        if pd.isna(atr_j):
            continue
        body = abs(closes[j] - opens[j])
        is_displacement = body >= displacement_multiplier * atr_j

        if direction == "bullish" and closes[j] > structure_level and is_displacement:
            return {"mss_idx": j, "mss_level": structure_level}
        if direction == "bearish" and closes[j] < structure_level and is_displacement:
            return {"mss_idx": j, "mss_level": structure_level}

    return None


def _find_fvg_from_displacement(df: pd.DataFrame, mss_idx: int, direction: str, search_window: int = 3):
    """The displacement candle at mss_idx should be the "explosive"
    middle candle of a Fair Value Gap -- so the gap's formed_idx (the
    3rd candle of that triple) normally lands at mss_idx or shortly
    after. Returns the matching zone (closest formed_idx to mss_idx) or
    None if no gap of the right direction shows up nearby."""
    zones = find_fair_value_gaps(df)
    candidates = [
        z for z in zones
        if z["type"] == direction and (mss_idx - 1) <= z["formed_idx"] <= (mss_idx + search_window)
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda z: abs(z["formed_idx"] - mss_idx))


def evaluate_ict_entry_sequence(
    df: pd.DataFrame,
    fractal_length: int = 2,
    sweep_tolerance_pct: float = 0.001,
    sweep_lookback_bars: int = 50,
    atr_period: int = 14,
    displacement_atr_multiplier: float = 1.2,
):
    """
    The full sweep -> MSS+displacement -> FVG sequence. Returns a dict
    describing the setup on success:
        {"direction": "bullish"/"bearish", "sweep_idx", "sweep_level",
         "mss_idx", "mss_level", "fvg": {...}, "swing_highs", "swing_lows"}
    or None if any step of the sequence is missing (this is expected to
    return None far more often than not -- that's the point, it's a
    strict filter).
    """
    swing_highs, swing_lows = find_swing_points(df, window=fractal_length)
    high_zones = find_equal_level_zones_indexed(swing_highs, sweep_tolerance_pct)
    low_zones = find_equal_level_zones_indexed(swing_lows, sweep_tolerance_pct)

    sweep = _find_recent_sweep(df, high_zones, low_zones, sweep_lookback_bars)
    if sweep is None:
        return None

    atr_series = compute_atr(df, period=atr_period)
    mss = _detect_mss_and_displacement(df, sweep, swing_highs, swing_lows, atr_series, displacement_atr_multiplier)
    if mss is None:
        return None

    fvg = _find_fvg_from_displacement(df, mss["mss_idx"], sweep["direction"])
    if fvg is None:
        return None

    return {
        "direction": sweep["direction"],
        "sweep_idx": sweep["sweep_idx"],
        "sweep_level": sweep["level"],
        "mss_idx": mss["mss_idx"],
        "mss_level": mss["mss_level"],
        "fvg": fvg,
        "swing_highs": swing_highs,
        "swing_lows": swing_lows,
    }


# ---------- 3. Counter-liquidity TP/SL ----------

def find_counter_liquidity_levels(
    df: pd.DataFrame, direction: str, swing_highs: list, swing_lows: list, atr_series: pd.Series,
    tolerance_atr_fraction: float = 0.10, asia_range: tuple | None = None,
) -> list:
    """
    Candidate opposing-liquidity levels, nearest first: equal highs/lows
    (tolerance derived from current ATR), unswept swing points (ones
    price hasn't already traded through), and the far edge of the Asia
    range if provided. "bullish" looks ABOVE current price, "bearish"
    BELOW it.
    """
    current_price = df.iloc[-1]["close"]
    current_atr = atr_series.iloc[-1]
    tolerance = tolerance_atr_fraction * current_atr if pd.notna(current_atr) and current_atr > 0 else 0
    tol_pct = (tolerance / current_price) if current_price else 0.001

    highs = df["high"].values
    lows = df["low"].values
    n = len(df)
    levels = set()

    if direction == "bullish":
        high_zones = find_equal_level_zones_indexed(swing_highs, tol_pct)
        for level, _formed_idx in high_zones:
            if level > current_price:
                levels.add(round(float(level), 6))
        for idx, price in swing_highs:
            if price <= current_price:
                continue
            already_swept = any(highs[i] > price for i in range(idx + 1, n))
            if not already_swept:
                levels.add(round(float(price), 6))
        if asia_range is not None:
            asia_high, _asia_low = asia_range
            if asia_high > current_price:
                levels.add(round(float(asia_high), 6))
    else:
        low_zones = find_equal_level_zones_indexed(swing_lows, tol_pct)
        for level, _formed_idx in low_zones:
            if level < current_price:
                levels.add(round(float(level), 6))
        for idx, price in swing_lows:
            if price >= current_price:
                continue
            already_swept = any(lows[i] < price for i in range(idx + 1, n))
            if not already_swept:
                levels.add(round(float(price), 6))
        if asia_range is not None:
            _asia_high, asia_low = asia_range
            if asia_low < current_price:
                levels.add(round(float(asia_low), 6))

    return sorted(levels) if direction == "bullish" else sorted(levels, reverse=True)


def compute_ict_trade_levels(
    df: pd.DataFrame, setup: dict, atr_series: pd.Series,
    tolerance_atr_fraction: float = 0.10, min_rr: float = 1.5, asia_range: tuple | None = None,
):
    """
    Given a confirmed setup from evaluate_ict_entry_sequence, computes
    entry (current close), stop-loss (beyond the sweep candle's wick),
    TP1/TP2 (nearest two counter-liquidity levels) and R:R (of TP1).
    Returns None if there's no counter-liquidity level to target, or if
    the resulting R:R is below `min_rr` -- both are meant to actually
    block the alert, not just annotate it.
    """
    direction = setup["direction"]
    entry = df.iloc[-1]["close"]
    sweep_idx = setup["sweep_idx"]

    if direction == "bullish":
        sl = df.iloc[sweep_idx]["low"]
        risk = entry - sl
    else:
        sl = df.iloc[sweep_idx]["high"]
        risk = sl - entry

    if risk is None or risk <= 0:
        return None

    levels = find_counter_liquidity_levels(
        df, direction, setup["swing_highs"], setup["swing_lows"], atr_series,
        tolerance_atr_fraction, asia_range,
    )
    if not levels:
        return None

    tp1 = levels[0]
    tp2 = levels[1] if len(levels) > 1 else None

    reward = (tp1 - entry) if direction == "bullish" else (entry - tp1)
    if reward <= 0:
        return None

    rr = reward / risk
    if rr < min_rr:
        return None

    return {"entry": entry, "sl": sl, "tp1": tp1, "tp2": tp2, "rr": rr}


# ---------- 4. Asia range + Power of Three (directional filter only) ----------

def compute_asia_range(df: pd.DataFrame, lookback_days: int = 3):
    """
    Most recently COMPLETED Asia range (20:00-00:00 NY LOCAL time,
    spans midnight) as (asia_high, asia_low, range_end_time_utc), or
    None if not found within lookback_days. Same walk-backward approach
    as ict_concepts.get_most_recent_session_range, but the window is
    defined in NY local time (so it shifts a fixed clock hour across
    DST, matching how traders actually think of "the Asia session"),
    not fixed UTC hours.
    """
    current_time_utc = df.iloc[-1]["close_time"]
    current_ny_date = current_time_utc.astimezone(NY_TZ).date()

    for days_back in range(0, lookback_days + 1):
        day = current_ny_date - timedelta(days=days_back)
        window_start_ny = datetime.combine(day, dtime(hour=20), tzinfo=NY_TZ)
        window_end_ny = window_start_ny + timedelta(hours=4)  # 20:00 -> 00:00 the next day

        window_end_utc = window_end_ny.astimezone(timezone.utc)
        if window_end_utc > current_time_utc:
            continue  # this Asia range hasn't closed yet

        window_start_utc = window_start_ny.astimezone(timezone.utc)
        mask = (df["close_time"] >= window_start_utc) & (df["close_time"] < window_end_utc)
        candles = df[mask]
        if len(candles) == 0:
            continue

        return candles["high"].max(), candles["low"].min(), window_end_utc

    return None


def compute_po3_bias(
    df: pd.DataFrame, asia_high: float, asia_low: float, asia_range_end_utc: datetime,
    daily_atr: float | None, max_range_atr_fraction: float = 0.40,
) -> str | None:
    """
    "bullish" if the Asia low was swept (wicked below, closed back
    inside the range) and the high wasn't; "bearish" the mirror image;
    None if both/neither happened, or if the Asia range itself is wider
    than max_range_atr_fraction of the daily ATR (too wide to treat as
    a clean overnight liquidity pool -- the filter just sits out that
    day rather than guessing). Only looks at candles after the Asia
    range closed.
    """
    asia_range_size = asia_high - asia_low
    if daily_atr and daily_atr > 0 and asia_range_size > max_range_atr_fraction * daily_atr:
        return None

    post = df[df["close_time"] > asia_range_end_utc]
    if len(post) == 0:
        return None

    highs = post["high"].values
    lows = post["low"].values
    closes = post["close"].values

    swept_low = any(lows[i] < asia_low and closes[i] >= asia_low for i in range(len(post)))
    swept_high = any(highs[i] > asia_high and closes[i] <= asia_high for i in range(len(post)))

    if swept_low and not swept_high:
        return "bullish"
    if swept_high and not swept_low:
        return "bearish"
    return None
