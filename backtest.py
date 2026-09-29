"""
Backtest: old weighted-score pipeline vs. new ICT pipeline
-----------------------------------------------------------
Simple walk-forward comparison, run locally (needs a running, logged-in
MT5 terminal -- same requirement as main.py).

WHAT THIS DOES:
For each symbol in ICT_ADVANCED_SYMBOLS (NASDAQ, SPX500), pulls the last
CANDLE_COUNT 1-minute candles from MT5 and walks forward through them with
a sliding window (same window size the live bot uses), re-running the
EXACT SAME functions main.py calls live:
  - OLD pipeline: compute_indicators + score_signal + classify() -- counts
    how many BUY/SELL/STRONG BUY/STRONG SELL bars would have fired.
  - NEW pipeline: get_active_kill_zone + evaluate_ict_entry_sequence +
    compute_ict_trade_levels -- counts how many valid signals fire, and
    for each one, walks forward (up to LOOKAHEAD_BARS) to see whether
    TP1 or the stop-loss was hit first, giving a rough hit-rate and
    average R.

WHAT THIS DOESN'T DO (read before trusting the numbers):
  - No spread/slippage/commission modeling -- entries/exits are assumed
    to fill exactly at the computed price.
  - No PO3 bias filter and no 1h HTF filter applied here, to keep the
    walk-forward loop simple -- the live bot applies both, so live signal
    counts will be lower than what this script reports.
  - "Old pipeline" signal count is a raw classify() trigger count, not
    filtered by kill zone/HTF either -- it's meant as a rough baseline,
    not a like-for-like backtest of the exact old alerting behavior.
  - Can be slow: CANDLE_COUNT=20000 1-minute candles is roughly 2 weeks
    of 24/5 trading, not 2-3 months -- MT5's copy_rates_from_pos has its
    own history limits per broker, so raise CANDLE_COUNT and check how
    far back your broker's history actually goes. This is a sanity check,
    not a rigorous backtest engine.
  - Fewer signals from the new pipeline than the old one is EXPECTED and
    correct (that's the whole point of requiring sweep -> MSS+displacement
    -> FVG in strict order, plus the R:R minimum) -- don't read a low new
    signal count as a bug.

Run with: python backtest.py
"""

from datetime import datetime, timezone

from mt5_data import fetch_ohlc_mt5
from config import (
    ICT_ADVANCED_SYMBOLS,
    ICT_KILL_ZONES_ENABLED,
    ICT_KILL_ZONE_WINDOWS,
    FRACTAL_LENGTH,
    ICT_SWEEP_TOLERANCE_PCT,
    ICT_SWEEP_LOOKBACK_BARS,
    DISPLACEMENT_ATR_PERIOD,
    DISPLACEMENT_ATR_MULTIPLIER,
    EQUAL_LEVEL_TOLERANCE_ATR_FRACTION,
    MIN_RR_RATIO,
)
from ict_concepts import is_us_index_kill_zone
from ict_advanced import (
    get_active_kill_zone,
    compute_atr,
    evaluate_ict_entry_sequence,
    compute_ict_trade_levels,
)
from main import compute_indicators, score_signal, classify

CANDLE_COUNT = 20000   # raise this if your broker's MT5 history goes back further
WINDOW_SIZE = 300      # matches OHLC_MT5_COUNT-ish, enough for EMA200 + ATR + swings
STEP = 5               # advance 5 candles between checks (speed vs. resolution trade-off)
LOOKAHEAD_BARS = 500   # how far forward to look for a TP1/SL hit after a new-pipeline signal


def simulate_old_pipeline(df) -> int:
    """
    Walks the same sliding window the live bot uses and counts how many
    bars would have triggered a BUY/SELL/STRONG BUY/STRONG SELL under the
    old weighted-score classify() logic. No kill-zone/HTF filtering here
    (see module docstring) -- this is a raw trigger count, an upper bound
    on what the old pipeline could have alerted on.
    """
    signal_count = 0
    for end in range(WINDOW_SIZE, len(df), STEP):
        window = df.iloc[end - WINDOW_SIZE:end].reset_index(drop=True)
        try:
            df_indic = compute_indicators(window)
            result = score_signal(df_indic)
            classification = classify(result["weighted_total"])
        except Exception:
            continue
        if classification != "NO SIGNAL":
            signal_count += 1
    return signal_count


def simulate_new_pipeline(df) -> dict:
    """
    Walks the same sliding window and counts how many bars produce a
    valid sweep -> MSS+displacement -> FVG setup with an acceptable R:R
    (kill-zone gated, same as live). For each signal, looks forward up to
    LOOKAHEAD_BARS candles to see whether TP1 or the stop was touched
    first, to estimate a rough hit rate and average realized R.
    """
    signals = 0
    tp_hits = 0
    sl_hits = 0
    no_result = 0
    realized_r_values = []

    end = WINDOW_SIZE
    while end < len(df):
        window = df.iloc[end - WINDOW_SIZE:end].reset_index(drop=True)
        now = window.iloc[-1]["close_time"].to_pydatetime()

        if ICT_KILL_ZONES_ENABLED:
            active_kill_zone = get_active_kill_zone(now, ICT_KILL_ZONE_WINDOWS)
        else:
            active_kill_zone = "NY AM" if is_us_index_kill_zone(now) else None

        if active_kill_zone is None:
            end += STEP
            continue

        try:
            setup = evaluate_ict_entry_sequence(
                window,
                fractal_length=FRACTAL_LENGTH,
                sweep_tolerance_pct=ICT_SWEEP_TOLERANCE_PCT,
                sweep_lookback_bars=ICT_SWEEP_LOOKBACK_BARS,
                atr_period=DISPLACEMENT_ATR_PERIOD,
                displacement_atr_multiplier=DISPLACEMENT_ATR_MULTIPLIER,
            )
        except Exception:
            end += STEP
            continue

        if setup is None:
            end += STEP
            continue

        try:
            atr_series = compute_atr(window, period=DISPLACEMENT_ATR_PERIOD)
            levels = compute_ict_trade_levels(
                window, setup, atr_series,
                EQUAL_LEVEL_TOLERANCE_ATR_FRACTION, MIN_RR_RATIO,
                asia_range=None,
            )
        except Exception:
            end += STEP
            continue

        if levels is None:
            end += STEP
            continue

        signals += 1
        direction = setup["direction"]
        entry, sl, tp1 = levels["entry"], levels["sl"], levels["tp1"]

        # Look forward to see whether TP1 or SL is hit first.
        future = df.iloc[end:end + LOOKAHEAD_BARS]
        outcome = None
        for _, bar in future.iterrows():
            if direction == "bullish":
                hit_sl = bar["low"] <= sl
                hit_tp = bar["high"] >= tp1
            else:
                hit_sl = bar["high"] >= sl
                hit_tp = bar["low"] <= tp1
            if hit_sl and hit_tp:
                # Ambiguous (both touched in the same candle) -- count as
                # the worse outcome (stop) since we can't know which
                # happened first without intra-candle data.
                outcome = "sl"
                break
            elif hit_sl:
                outcome = "sl"
                break
            elif hit_tp:
                outcome = "tp"
                break

        if outcome == "tp":
            tp_hits += 1
            realized_r_values.append(levels["rr"])
        elif outcome == "sl":
            sl_hits += 1
            realized_r_values.append(-1.0)
        else:
            no_result += 1

        # Skip ahead past this signal's lookahead window so we don't
        # re-trigger on the same setup repeatedly.
        end += max(STEP, LOOKAHEAD_BARS // 4)

    avg_r = sum(realized_r_values) / len(realized_r_values) if realized_r_values else None
    resolved = tp_hits + sl_hits
    hit_rate = (tp_hits / resolved * 100) if resolved else None

    return {
        "signals": signals,
        "tp_hits": tp_hits,
        "sl_hits": sl_hits,
        "no_result": no_result,
        "hit_rate_pct": hit_rate,
        "avg_r": avg_r,
    }


def run_backtest_for_symbol(symbol: str) -> None:
    print(f"\n=== {symbol} ===")
    try:
        df = fetch_ohlc_mt5(symbol, interval="1m", count=CANDLE_COUNT)
    except Exception as e:
        print(f"[!] {symbol}: veri alinamadi ({e})")
        return

    print(f"{len(df)} mum cekildi ({df.iloc[0]['close_time']} -> {df.iloc[-1]['close_time']})")

    if len(df) < WINDOW_SIZE + STEP:
        print("Yeterli veri yok, atlaniyor.")
        return

    print("Eski pipeline calistiriliyor...")
    old_signals = simulate_old_pipeline(df)
    print(f"  Eski pipeline: {old_signals} sinyal (kill zone/HTF filtresi uygulanmadan, ham tetikleme sayisi)")

    print("Yeni pipeline calistiriliyor (bu daha yavas olabilir)...")
    new_result = simulate_new_pipeline(df)
    print(f"  Yeni pipeline: {new_result['signals']} sinyal")
    print(f"    TP1'e ulasan : {new_result['tp_hits']}")
    print(f"    Stop'a takilan: {new_result['sl_hits']}")
    print(f"    Sonuclanmayan (lookahead penceresi bitti): {new_result['no_result']}")
    if new_result["hit_rate_pct"] is not None:
        print(f"    Isabet orani (sonuclananlar arasinda): %{new_result['hit_rate_pct']:.1f}")
    if new_result["avg_r"] is not None:
        print(f"    Ortalama R: {new_result['avg_r']:.2f}")

    print(f"\n  Not: sinyal sayisinin eski pipeline'a gore dusuk olmasi beklenen "
          f"ve istenen bir durum (sweep->MSS+displacement->FVG sirasi + min R:R "
          f"filtresi az ama daha kaliteli sinyal aramasindan kaynaklaniyor).")


if __name__ == "__main__":
    print(f"[{datetime.now(timezone.utc)}] Backtest basliyor...")
    print(f"CANDLE_COUNT={CANDLE_COUNT}, WINDOW_SIZE={WINDOW_SIZE}, STEP={STEP}, LOOKAHEAD_BARS={LOOKAHEAD_BARS}")
    print("(MT5 terminalinin acik ve giris yapilmis olmasi gerekiyor)")
    for sym in ICT_ADVANCED_SYMBOLS:
        run_backtest_for_symbol(sym)
    print("\nBitti.")
