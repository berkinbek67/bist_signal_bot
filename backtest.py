"""
Backtest
--------
Runs the EXACT same indicator/scoring/decision logic from main.py against
historical data, walking forward candle by candle (no look-ahead -- each
step only sees data up to that point, same as the live bot).

Runs TWO versions to test the funding-rate addition in isolation:
  1. WITHOUT funding rate (the original 6-factor system)
  2. WITH funding rate (Binance-only historical rate, as an approximation
     of the full 5-exchange OI-weighted version used live -- historical
     open interest across all 5 exchanges isn't freely available)

Compares both against simple buy-and-hold over the same period.

This does NOT place real trades. It simulates a hypothetical spot
position using only past data at each step.
"""

import requests
import pandas as pd
from config import COIN_ID, VS_CURRENCY, FEE_RATE, MIN_PROFIT_MARGIN
from main import compute_indicators, score_signal, classify, decide_action, compute_price_range
from funding_rate import BASE_SYMBOL_MAP, score_funding_rate

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
    # ~3 settlements/day, add buffer
    limit = min(1000, days * 3 + 20)
    response = requests.get(url, params={"symbol": symbol, "limit": limit}, timeout=15)
    response.raise_for_status()
    raw = response.json()

    df = pd.DataFrame(raw)
    df["close_time"] = pd.to_datetime(df["fundingTime"], unit="ms")
    df["rate_pct"] = df["fundingRate"].astype(float) * 100
    return df[["close_time", "rate_pct"]].sort_values("close_time")


def attach_funding_scores(price_df: pd.DataFrame, funding_df: pd.DataFrame) -> pd.DataFrame:
    """Attach the most recent known funding rate at each price timestamp
    (merge_asof = only uses data available AT OR BEFORE that point, so
    this does not leak future information)."""
    df = pd.merge_asof(
        price_df.sort_values("close_time"),
        funding_df.sort_values("close_time"),
        on="close_time",
        direction="backward",
    )
    df["funding_score"] = df["rate_pct"].apply(
        lambda r: score_funding_rate(r) if pd.notna(r) else None
    )
    return df


def run_simulation(df: pd.DataFrame, use_funding: bool) -> dict:
    """Walk forward through the historical data, simulating the bot's
    exact decision logic at each step. Starts flat (not in position)."""
    state = {"in_position": False, "entry_price": None, "entry_time": None}
    trades = []

    # Need at least EMA_TREND (200) candles of warm-up before indicators
    # are meaningful, same constraint the live bot has.
    start_index = 210

    for i in range(start_index, len(df)):
        window = df.iloc[: i + 1].copy()
        window = compute_indicators(window)

        funding_score = df.iloc[i]["funding_score"] if use_funding else None
        result = score_signal(window, funding_score=funding_score)
        classification = classify(result["weighted_total"])

        current_price = window.iloc[-1]["close"]
        action, reason = decide_action(classification, state, current_price)

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

    # If still in a position at the end, close it at the last price so
    # results are comparable (mark-to-market).
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

    return summarize(trades, df)


def summarize(trades: list, df: pd.DataFrame) -> dict:
    if not trades:
        return {
            "num_trades": 0, "win_rate": None, "total_return_pct": 0.0,
            "avg_win_pct": None, "avg_loss_pct": None, "trades": [],
        }

    fee_cost = 2 * FEE_RATE  # round trip
    net_pnls = [t["pnl_pct"] - fee_cost for t in trades]

    wins = [p for p in net_pnls if p > 0]
    losses = [p for p in net_pnls if p <= 0]

    # Compound the trades to get total strategy return over the period.
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
    start_price = df.iloc[210]["close"]  # same warm-up start point, fair comparison
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
    price_df = fetch_historical_prices(COIN_ID, VS_CURRENCY, BACKTEST_DAYS)

    print("Fetching historical funding rate (Binance only, approximation)...")
    try:
        funding_df = fetch_historical_funding(COIN_ID, BACKTEST_DAYS)
        df = attach_funding_scores(price_df, funding_df)
        funding_available = True
    except Exception as e:
        print(f"[!] Could not fetch funding history ({e}) -- running without it.")
        df = price_df.copy()
        df["funding_score"] = None
        funding_available = False

    if len(df) <= 210:
        print("[!] Not enough historical data for a meaningful backtest (need 200+ candles warm-up).")
        return

    bh_return = buy_and_hold_return(df)

    result_without_funding = run_simulation(df, use_funding=False)
    print_report("WITHOUT funding rate (6-factor system)", result_without_funding)

    if funding_available:
        result_with_funding = run_simulation(df, use_funding=True)
        print_report("WITH funding rate (7-factor system, Binance-only approx)", result_with_funding)
    else:
        print("\n=== WITH funding rate ===\nSkipped -- funding history unavailable this run.")

    print(f"\n=== Buy-and-hold (same period, for reference) ===")
    print(f"Return: {bh_return:+.2f}%")

    print("\nReminder: this is historical performance on one specific period and")
    print("one specific coin. It does not guarantee future results. Small trade")
    print("counts especially should be treated as low-confidence.")


if __name__ == "__main__":
    main()
