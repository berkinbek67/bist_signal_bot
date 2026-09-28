"""
MT5 Data
--------
Fetches OHLCV data for the NASDAQ-100 and S&P 500 index CFDs directly
from a running, logged-in MetaTrader 5 terminal, via the official
MetaTrader5 Python package.

This replaces the earlier Yahoo Finance feed for those two instruments
(^NDX/^SPX) -- Yahoo's data was consistently mismatched with the real
market, in a way that couldn't be explained away by a fixed delay
assumption. MT5 instead gives you your own broker's live feed.

IMPORTANT -- this only works locally: unlike Yahoo (a public HTTP API
reachable from anywhere, including GitHub Actions), the MetaTrader5
package works by talking to a MetaTrader 5 terminal INSTALLED, OPEN and
LOGGED IN on this same Windows machine. There is no cloud equivalent.
This is why the bot moved off GitHub Actions and now runs continuously
on your own PC instead (see main.py's local loop mode).

The BIST daily-picks scan is untouched by any of this -- it still uses
Yahoo Finance via bist_data.py, since that part was never the problem.
"""

import MetaTrader5 as mt5
import pandas as pd

_initialized = False

# Maps the interval strings used elsewhere in this project ("1m", "60m",
# etc.) to MT5's own timeframe constants.
_TIMEFRAME_MAP = {
    "1m": mt5.TIMEFRAME_M1,
    "5m": mt5.TIMEFRAME_M5,
    "15m": mt5.TIMEFRAME_M15,
    "30m": mt5.TIMEFRAME_M30,
    "60m": mt5.TIMEFRAME_H1,
    "1h": mt5.TIMEFRAME_H1,
    "1d": mt5.TIMEFRAME_D1,
}


def ensure_connected() -> None:
    """
    Makes sure we're connected to the local MT5 terminal. Safe to call
    on every scan -- once connected, this is a cheap no-op check rather
    than a fresh login each time.
    """
    global _initialized
    if _initialized:
        return
    if not mt5.initialize():
        raise RuntimeError(
            f"MT5'e baglanilamadi ({mt5.last_error()}). "
            "MT5 terminalinin acik ve hesaba giris yapilmis oldugundan emin ol."
        )
    _initialized = True


def fetch_ohlc_mt5(symbol: str, interval: str = "1m", count: int = 500) -> pd.DataFrame:
    """
    Fetch the most recent `count` candles for `symbol` from MT5, as a
    DataFrame with the same columns as bist_data.fetch_ohlc_yahoo
    (close_time, open, high, low, close, volume) -- so compute_indicators/
    score_signal/etc. don't need to know or care which source the data
    came from.

    `count` (a number of candles) replaces Yahoo's range_ string (e.g.
    "5d") -- MT5's API asks how many candles back, not a date range.
    """
    ensure_connected()

    timeframe = _TIMEFRAME_MAP.get(interval)
    if timeframe is None:
        raise ValueError(f"Bilinmeyen interval: {interval}")

    if not mt5.symbol_select(symbol, True):
        raise ValueError(f"MT5 sembolu bulunamadi/secilemedi: {symbol} ({mt5.last_error()})")

    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
    if rates is None or len(rates) == 0:
        raise ValueError(f"MT5'ten veri alinamadi: {symbol} ({mt5.last_error()})")

    df = pd.DataFrame(rates)
    df = df.rename(columns={"time": "close_time", "tick_volume": "volume"})
    df["close_time"] = pd.to_datetime(df["close_time"], unit="s", utc=True)
    df = df[["close_time", "open", "high", "low", "close", "volume"]]
    df = df.dropna(subset=["close"]).reset_index(drop=True)
    return df
