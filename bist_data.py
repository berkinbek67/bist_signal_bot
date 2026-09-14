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


def is_forex_market_open(now: datetime | None = None) -> bool:
    """
    True if the forex/commodities market is trading. Unlike BIST, this
    market runs nearly continuously: opens Sunday ~22:00 UTC and closes
    Friday ~22:00 UTC, with no daily close in between. Only genuinely
    closed on the weekend gap. Does not account for holidays.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    else:
        now = now.astimezone(timezone.utc)

    weekday = now.weekday()  # 0=Monday ... 5=Saturday, 6=Sunday
    hour = now.hour

    if weekday == 5:  # Saturday -- always closed
        return False
    if weekday == 6 and hour < 22:  # Sunday before 22:00 UTC -- still closed
        return False
    if weekday == 4 and hour >= 22:  # Friday after 22:00 UTC -- closed for the weekend
        return False
    return True


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


def fetch_ohlc_cross(base_ticker: str, quote_ticker: str, range_: str = "5d",
                      interval: str = "15m") -> pd.DataFrame:
    """
    For pairs Yahoo doesn't have directly (e.g. XAUEUR), compute the
    cross rate ourselves: base_ticker / quote_ticker at each matching
    timestamp. Standard practice when a direct symbol doesn't exist --
    e.g. XAUEUR = XAUUSD / EURUSD.

    Uses an inner join on close_time, so any timestamp missing from
    either series is dropped rather than guessed at.
    """
    base_df = fetch_ohlc_yahoo(base_ticker, range_=range_, interval=interval)
    quote_df = fetch_ohlc_yahoo(quote_ticker, range_=range_, interval=interval)

    merged = pd.merge(
        base_df, quote_df, on="close_time", suffixes=("_base", "_quote"), how="inner"
    )
    if len(merged) == 0:
        raise ValueError(f"No overlapping timestamps between {base_ticker} and {quote_ticker}")

    result = pd.DataFrame({"close_time": merged["close_time"]})
    for col in ["open", "high", "low", "close"]:
        result[col] = merged[f"{col}_base"] / merged[f"{col}_quote"]
    result["volume"] = merged["volume_base"]  # volume doesn't cross-divide meaningfully

    return result
