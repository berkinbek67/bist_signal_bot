"""
BIST Data
---------
Fetches OHLCV data for Borsa Istanbul stocks via Yahoo Finance's free
public chart API (unofficial, but widely used and stable). BIST tickers
use a ".IS" suffix on Yahoo, e.g. GARAN.IS, THYAO.IS, XU100.IS (index).

Data is DELAYED, not real-time -- true real-time BIST data requires an
official licensed feed. Given this bot checks every 15-30 minutes
anyway, a short delay is unlikely to matter much in practice, but it's
worth knowing.

Also provides an is_market_open() guard, since BIST trades specific
hours (10:00-18:00 Istanbul time, Mon-Fri) unlike crypto's 24/7 markets.
"""

from datetime import datetime, timedelta, timezone
import requests
import pandas as pd

# Istanbul is GMT+3 year-round (no daylight saving time changes).
ISTANBUL_TZ = timezone(timedelta(hours=3))

MARKET_OPEN_HOUR = 10   # 10:00
MARKET_CLOSE_HOUR = 18  # 18:00

# A reasonable BIST 30 starting watchlist (most liquid names). Adjust as
# you like -- these are the ".IS"-suffixed Yahoo Finance tickers.
BIST_30_WATCHLIST = [
    "GARAN.IS", "AKBNK.IS", "ISCTR.IS", "YKBNK.IS", "THYAO.IS",
    "ASELS.IS", "TUPRS.IS", "SISE.IS", "KCHOL.IS", "SAHOL.IS",
    "EREGL.IS", "BIMAS.IS", "FROTO.IS", "TCELL.IS", "TOASO.IS",
]

# A standard browser User-Agent -- Yahoo's public endpoint often blocks
# requests that look like a bare script.
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def is_market_open(now: datetime | None = None) -> bool:
    """True if BIST is in its regular continuous trading session right
    now (10:00-18:00 Istanbul time, Monday-Friday). Does not account for
    exchange holidays -- add a holiday calendar later if that matters."""
    if now is None:
        now = datetime.now(ISTANBUL_TZ)
    else:
        now = now.astimezone(ISTANBUL_TZ)

    if now.weekday() >= 5:  # 5=Saturday, 6=Sunday
        return False
    return MARKET_OPEN_HOUR <= now.hour < MARKET_CLOSE_HOUR


def fetch_ohlc_yahoo(symbol: str, range_: str = "5d", interval: str = "15m") -> pd.DataFrame:
    """
    Fetch OHLCV candles from Yahoo Finance's public chart endpoint.
    range_: how far back (e.g. '5d', '1mo', '3mo')
    interval: candle size (e.g. '5m', '15m', '1h', '1d')
    Intraday intervals (under 1d) are only available for recent history
    (Yahoo limits how far back small intervals go).
    """
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {"range": range_, "interval": interval}
    response = requests.get(url, params=params, headers=_HEADERS, timeout=10)
    response.raise_for_status()
    data = response.json()

    if data.get("chart", {}).get("error"):
        raise ValueError(f"Yahoo Finance error for {symbol}: {data['chart']['error']}")

    result = data["chart"]["result"][0]
    timestamps = result["timestamp"]
    quote = result["indicators"]["quote"][0]

    df = pd.DataFrame({
        "close_time": pd.to_datetime(timestamps, unit="s", utc=True),
        "open": quote["open"],
        "high": quote["high"],
        "low": quote["low"],
        "close": quote["close"],
        "volume": quote["volume"],
    })
    # Yahoo sometimes includes null rows for illiquid moments -- drop them.
    df = df.dropna(subset=["close"]).reset_index(drop=True)
    return df
