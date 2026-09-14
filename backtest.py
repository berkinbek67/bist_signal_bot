"""
Backtest
--------
Runs the EXACT same indicator/scoring/decision logic from main.py against
historical data, walking forward candle by candle (no look-ahead -- each
step only sees data up to that point, same as the live bot).

Tests each addition IN ISOLATION against the original 6-factor baseline,
plus a combined "everything" run:
  1. Baseline (6 factors: EMA crossover, EMA200 trend, RSI, MACD, Volume, BB)
  2. + Funding rate only (Binance-only historical approximation)
  3. + Supertrend only
  4. + Both together (matches what's actually running live)

Compares all four against simple buy-and-hold over the same period.

This does NOT place real trades. It simulates a hypothetical spot
position using only past data at each step.
"""

import requests
import pandas as pd
from config import COIN_ID, VS_CURRENCY, FEE_RATE, MIN_PROFIT_MARGIN, SUPERTREND_PERIOD, SUPERTREND_MULTIPLIER
from main import compute_indicators, score_signal, classify, classify_trend_exit, decide_action, compute_price_range
from funding_rate import BASE_SYMBOL_MAP, score_funding_rate
from supertrend import fetch_ohlc_binance, compute_supertrend, score_supertrend

BACKTEST_DAYS = 90  # CoinGecko gives hourly granularity for 2-90 day ranges


def fetch_historical_prices(coin_id: str, vs_currency: str, days: int) -> pd.DataFrame:
    url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
    params = {"vs_currency": vs_currency, "days": days}
    response = requests.get(url, params=params, timeout=15)
    response.raise_for_status()
    raw = response.json()

    prices = raw["prices"]
    volumes = raw["total_volumes"]
    df = pd.DataFrame(prices, columns=["timestamp", "close"])
    df["volume"] = [v[1] for v in volumes]
    df["close_time"] = pd.to_datetime(df["timestamp"], unit="ms")
    return df[["close_time", "close", "volume"]]


def fetch_historical_funding(coin_id: str, days: int) -> pd.DataFrame:
    """Binance-only historical funding rate (free, public). Returns a
    dataframe with timestamp + rate (as %), settled every 8 hours."""
    base = BASE_SYMBOL_MAP.get(coin_id)
    if base is None:
        raise ValueError(f"No ticker mapping for {coin_id}")

    symbol = f"{base}USDT"
    url = "https://fapi.binance.com/fapi/v1/fundingRate"
    limit = min(1000, days * 3 + 20)
    response = requests.get(url, params={"symbol": symbol, "limit": limit}, timeout=15)
    response.raise_for_status()
    raw = response.json()

    df = pd.DataFrame(raw)
    df["close_time"] = pd.to_datetime(df["fundingTime"], unit="ms")
    df["rate_pct"] = df["fundingRate"].astype(float) * 100
    return df[["close_time", "rate_pct"]].sort_values("close_time")


def fetch_historical_supertrend(coin_id: str) -> pd.DataFrame:
    """
    Binance spot hourly candles, capped at 1000 (~41 days) by Binance's
    per-request limit. This means the Supertrend factor's backtest
    coverage is SHORTER than the full 90-day window -- it'll simply have
    no data (and be excluded, same as funding when unavailable) for the
    earlier part of the period.
    """
    base = BASE_SYMBOL_MAP.get(coin_id)
    if base is None:
        raise ValueError(f"No ticker mapping for {coin_id}")
    ohlc = fetch_ohlc_binance(base, interval="1h", limit=1000)
    ohlc = compute_supertrend(ohlc, period=SUPERTREND_PERIOD, multiplier=SUPERTREND_MULTIPLIER)
    ohlc["supertrend_score"] = ohlc["supertrend_trend"].apply(score_supertrend)
    return ohlc[["close_time", "supertrend_score"]]


def attach_scores(price_df: pd.DataFrame, extra_df: pd.DataFrame, score_col: str) -> pd.DataFrame:
    """Attach the most recent known value at each price timestamp using
    merge_asof (backward direction = only data available AT OR BEFORE
    that point, so this never leaks future information)."""
    return pd.merge_asof(
        price_df.sort_values("close_time"),
        extra_df[["close_time", score_col]].sort_values("close_time"),
        on="close_time",
        direction="backward",
    )


def run_simulation(df: pd.DataFrame, use_funding: bool, use_supertrend: bool) -> dict:
    """Walk forward through the historical data, simulating the bot's
    exact decision logic at each step. Starts flat (not in position)."""
    state = {"in_position": False, "entry_price": None, "entry_time": None}
    trades = []
    start_index = 210  # EMA200 warm-up, same constraint the live bot has

    for i in range(start_index, len(df)):
        window = df.iloc[: i + 1].copy()
        window = compute_indicators(window)

        funding_score = df.iloc[i].get("funding_score") if use_funding else None
        supertrend_score = df.iloc[i].get("supertrend_score") if use_supertrend else None
        if pd.isna(funding_score):
            funding_score = None
        if pd.isna(supertrend_score):
            supertrend_score = None

        result = score_signal(window, funding_score=funding_score, supertrend_score=supertrend_score)
        classification = classify(result["weighted_total"])

        current_price = window.iloc[-1]["close"]
        trend_classification = classify_trend_exit(result["breakdown"])
        action, reason = decide_action(classification, trend_classification, state, current_price)

        if action == "BUY":
            state = {
                "in_position": True,
                "entry_price": current_price,
                "entry_time": df.iloc[i]["close_time"],
            }
        elif action == "SELL":
            entry_price = state["entry_price"]
            pnl_pct = (current_price - entry_price) / entry_price
            trades.append({
                "entry_time": state["entry_time"],
                "exit_time": df.iloc[i]["close_time"],
                "entry_price": entry_price,
                "exit_price": current_price,
                "pnl_pct": pnl_pct,
            })
            state = {"in_position": False, "entry_price": None, "entry_time": None}

    if state["in_position"]:
        current_price = df.iloc[-1]["close"]
        pnl_pct = (current_price - state["entry_price"]) / state["entry_price"]
        trades.append({
            "entry_time": state["entry_time"],
            "exit_time": df.iloc[-1]["close_time"],
            "entry_price": state["entry_price"],
            "exit_price": current_price,
            "pnl_pct": pnl_pct,
            "still_open": True,
        })

    return summarize(trades)


def summarize(trades: list) -> dict:
    if not trades:
        return {
            "num_trades": 0, "win_rate": None, "total_return_pct": 0.0,
            "avg_win_pct": None, "avg_loss_pct": None, "trades": [],
        }

    fee_cost = 2 * FEE_RATE
    net_pnls = [t["pnl_pct"] - fee_cost for t in trades]
    wins = [p for p in net_pnls if p > 0]
    losses = [p for p in net_pnls if p <= 0]

    total_return = 1.0
    for p in net_pnls:
        total_return *= (1 + p)
    total_return_pct = (total_return - 1) * 100

    return {
        "num_trades": len(trades),
        "win_rate": len(wins) / len(trades) * 100,
        "total_return_pct": total_return_pct,
        "avg_win_pct": (sum(wins) / len(wins) * 100) if wins else None,
        "avg_loss_pct": (sum(losses) / len(losses) * 100) if losses else None,
        "trades": trades,
    }


def buy_and_hold_return(df: pd.DataFrame) -> float:
    start_price = df.iloc[210]["close"]
    end_price = df.iloc[-1]["close"]
    return (end_price - start_price) / start_price * 100


def print_report(label: str, result: dict) -> None:
    print(f"\n=== {label} ===")
    print(f"Trades taken:       {result['num_trades']}")
    if result["num_trades"] == 0:
        print("No trades triggered in this period.")
        return
    print(f"Win rate:           {result['win_rate']:.1f}%")
    print(f"Total return:       {result['total_return_pct']:+.2f}% (compounded, after fees)")
    if result["avg_win_pct"] is not None:
        print(f"Avg winning trade:  {result['avg_win_pct']:+.2f}%")
    if result["avg_loss_pct"] is not None:
        print(f"Avg losing trade:   {result['avg_loss_pct']:+.2f}%")


def main():
    print(f"Fetching {BACKTEST_DAYS} days of historical data for {COIN_ID}/{VS_CURRENCY}...")
    df = fetch_historical_prices(COIN_ID, VS_CURRENCY, BACKTEST_DAYS)

    print("Fetching historical funding rate (Binance only, approximation)...")
    funding_available = False
    try:
        funding_df = fetch_historical_funding(COIN_ID, BACKTEST_DAYS)
        df = attach_scores(df, funding_df.assign(funding_score=funding_df["rate_pct"].apply(score_funding_rate)), "funding_score")
        funding_available = True
    except Exception as e:
        print(f"[!] Could not fetch funding history ({e}) -- skipping that factor.")
        df["funding_score"] = None

    print("Fetching historical Supertrend data (Binance spot, capped at ~41 days)...")
    supertrend_available = False
    try:
        supertrend_df = fetch_historical_supertrend(COIN_ID)
        df = attach_scores(df, supertrend_df, "supertrend_score")
        supertrend_available = True
    except Exception as e:
        print(f"[!] Could not fetch Supertrend history ({e}) -- skipping that factor.")
        df["supertrend_score"] = None

    if len(df) <= 210:
        print("[!] Not enough historical data for a meaningful backtest (need 200+ candles warm-up).")
        return

    bh_return = buy_and_hold_return(df)

    print_report("Baseline (6 factors)", run_simulation(df, use_funding=False, use_supertrend=False))

    if funding_available:
        print_report("+ Funding rate only", run_simulation(df, use_funding=True, use_supertrend=False))
    if supertrend_available:
        print_report("+ Supertrend only", run_simulation(df, use_funding=False, use_supertrend=True))
    if funding_available and supertrend_available:
        print_report("+ Both (matches live bot)", run_simulation(df, use_funding=True, use_supertrend=True))

    print(f"\n=== Buy-and-hold (same period, for reference) ===")
    print(f"Return: {bh_return:+.2f}%")

    print("\nReminder: this is historical performance on one specific period and")
    print("one specific coin. It does not guarantee future results. Small trade")
    print("counts especially should be treated as low-confidence. The Supertrend")
    print("results only cover the last ~41 days (Binance's per-request candle limit),")
    print("shorter than the other tests -- not a fully apples-to-apples comparison.")


if __name__ == "__main__":
    main()
