"""
MetaTrader 5 Auto-Trading Extension
--------------------------------------
Runs LOCALLY on your Windows PC, with the MT5 terminal open and logged
into your account. Cannot run on GitHub Actions -- MT5's Python API
only works on Windows with the actual terminal running (this is a
local IPC connection, not an outbound HTTPS call, so the old
proxy/network issue from your earlier setup does not apply here).

Reuses the EXACT same indicator/scoring logic as the Telegram bot
(imported directly from main.py), so decisions stay consistent between
the two -- just fed MT5's own live broker data instead of Yahoo
Finance's delayed data, and this one actually places orders instead of
sending a message.

SAFETY: this refuses to run if the connected account is not a demo
account. Remove that check only when you're deliberately ready to go
live, and even then, start with a very small lot size.

SETUP REQUIRED BEFORE RUNNING:
1. pip install MetaTrader5   (Windows only)
2. Open MT5, log into your DEMO account, leave it running
3. Fill in BRENT_SYMBOL and NASDAQ_SYMBOL below with your broker's
   EXACT symbol names (check the Market Watch panel in MT5)
4. Adjust LOT_SIZE if 0.01 isn't right for your account
"""

import time
from datetime import datetime, timezone
import pandas as pd
import MetaTrader5 as mt5

from main import compute_indicators, score_signal, classify, compute_price_range
from bist_data import is_forex_market_open, is_us_stock_market_open
from ict_concepts import is_qqq_kill_zone

# --- SETTINGS YOU MUST FILL IN ---
BRENT_SYMBOL = "UKOIL.cash"   # <-- CHANGE to your broker's exact Brent symbol (Market Watch)
NASDAQ_SYMBOL = "NAS100"      # <-- CHANGE to your broker's exact Nasdaq symbol (Market Watch)
LOT_SIZE = 0.01               # smallest typical lot size -- fine for a demo test
CHECK_INTERVAL_SECONDS = 60
MAGIC_NUMBER = 20260916       # arbitrary ID tagging orders placed by this bot

WATCHLIST_MT5 = {
    BRENT_SYMBOL: {"market_check": is_forex_market_open, "kill_zone_check": None},
    NASDAQ_SYMBOL: {"market_check": is_us_stock_market_open, "kill_zone_check": is_qqq_kill_zone},
}


def fetch_mt5_ohlc(symbol: str, timeframe=None, count: int = 300) -> pd.DataFrame:
    """Pull recent candles directly from MT5's own live feed -- this is
    your broker's actual data, not Yahoo's delayed feed."""
    if timeframe is None:
        timeframe = mt5.TIMEFRAME_M1
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
    if rates is None or len(rates) == 0:
        raise ValueError(
            f"No data returned for '{symbol}'. Check the symbol name is exactly "
            f"right (case-sensitive) and visible in Market Watch."
        )
    df = pd.DataFrame(rates)
    df["close_time"] = pd.to_datetime(df["time"], unit="s")
    df = df.rename(columns={"tick_volume": "volume"})
    return df[["close_time", "open", "high", "low", "close", "volume"]]


def has_open_position(symbol: str, direction: str) -> bool:
    """Avoids opening a duplicate position in the same direction on the
    same symbol. direction: 'LONG' or 'SHORT'."""
    positions = mt5.positions_get(symbol=symbol)
    if not positions:
        return False
    wanted_type = mt5.ORDER_TYPE_BUY if direction == "LONG" else mt5.ORDER_TYPE_SELL
    return any(p.type == wanted_type for p in positions)


def place_order(symbol: str, direction: str, price_range: dict) -> None:
    """Places a real market order with SL/TP attached."""
    order_type = mt5.ORDER_TYPE_BUY if direction == "LONG" else mt5.ORDER_TYPE_SELL
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        print(f"[!] {symbol}: could not get current tick, skipping order.")
        return
    price = tick.ask if direction == "LONG" else tick.bid

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": LOT_SIZE,
        "type": order_type,
        "price": price,
        "sl": price_range["invalidation"],
        "tp": price_range["target"],
        "deviation": 20,
        "magic": MAGIC_NUMBER,
        "comment": "signal bot auto-trade",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    result = mt5.order_send(request)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        retcode = result.retcode if result else "N/A"
        comment = result.comment if result else str(mt5.last_error())
        print(f"[!] {symbol}: order FAILED, retcode={retcode}, comment={comment}")
    else:
        print(f"    {symbol}: order placed -- {direction} {LOT_SIZE} lots @ {price}")


def scan_and_trade(symbol: str, config: dict) -> None:
    now = datetime.now(timezone.utc)

    if not config["market_check"](now):
        print(f"    {symbol}: market closed, skipping.")
        return
    if config["kill_zone_check"] is not None and not config["kill_zone_check"](now):
        print(f"    {symbol}: outside kill zone, skipping.")
        return

    try:
        df = fetch_mt5_ohlc(symbol)
    except Exception as e:
        print(f"[!] {symbol}: {e}")
        return

    if len(df) < 210:
        print(f"    {symbol}: not enough candles yet ({len(df)}), skipping.")
        return

    df = compute_indicators(df)
    result = score_signal(df)
    classification = classify(result["weighted_total"])
    price = df.iloc[-1]["close"]

    print(f"    {symbol}: price={price:.4f} score={result['weighted_total']} classification={classification}")

    if classification in ("BUY", "STRONG BUY") and not has_open_position(symbol, "LONG"):
        price_range = compute_price_range(df, direction="LONG")
        place_order(symbol, "LONG", price_range)
    elif classification in ("SELL", "STRONG SELL") and not has_open_position(symbol, "SHORT"):
        price_range = compute_price_range(df, direction="SHORT")
        place_order(symbol, "SHORT", price_range)


def main() -> None:
    if not mt5.initialize():
        print(f"[!] MT5 initialize() failed: {mt5.last_error()}")
        print("    Make sure MT5 is open and logged into your account.")
        return

    account_info = mt5.account_info()
    if account_info is not None:
        print(f"Connected to account #{account_info.login} ({account_info.server}), "
              f"balance={account_info.balance} {account_info.currency}")
        if account_info.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO:
            print("[!] This does NOT look like a demo account. Stopping for safety.")
            mt5.shutdown()
            return
    else:
        print("[!] Could not read account info -- stopping for safety.")
        mt5.shutdown()
        return

    print(f"Starting MT5 auto-trade bot for {list(WATCHLIST_MT5.keys())}. "
          f"Checking every {CHECK_INTERVAL_SECONDS}s.")

    try:
        while True:
            for symbol, config in WATCHLIST_MT5.items():
                try:
                    scan_and_trade(symbol, config)
                except Exception as e:
                    print(f"[!] {symbol}: unexpected error: {e}")
            time.sleep(CHECK_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print("Stopped by user.")
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
