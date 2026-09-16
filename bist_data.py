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
from zoneinfo import ZoneInfo
import requests
import pandas as pd

# Istanbul is GMT+3 year-round (no daylight saving time changes).
ISTANBUL_TZ = timezone(timedelta(hours=3))

# Both COMEX gold (GC=F) and Brent/ICE (BZ=F) anchor their sessions to US
# Eastern Time. Using zoneinfo (not a fixed UTC offset) means daylight
# saving transitions are handled automatically and correctly.
NY_TZ = ZoneInfo("America/New_York")

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
    True if the gold/Brent futures market is trading. Both anchor to US
    Eastern Time: weekly session Sunday 6pm ET to Friday 5pm ET, PLUS a
    daily 1-hour maintenance break from 5-6pm ET every weekday (this is
    real -- confirmed for both COMEX gold and ICE Brent/oil). Converting
    to America/New_York handles daylight saving automatically -- no
    hardcoded UTC offset that would silently go wrong after a DST switch.

    Note: Brent's actual weekly reopen (ICE, London-anchored) may differ
    from gold's by an hour or so -- this uses gold's schedule for both,
    which is close enough that it's a minor, low-stakes imprecision
    rather than something worth a separate code path right now.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    now_ny = now.astimezone(NY_TZ)

    weekday = now_ny.weekday()  # 0=Monday ... 5=Saturday, 6=Sunday
    hour = now_ny.hour

    if weekday == 5:  # Saturday -- always closed
        return False
    if weekday == 6 and hour < 18:  # Sunday before 6pm ET -- still closed
        return False
    if weekday == 4 and hour >= 17:  # Friday after 5pm ET -- closed for the weekend
        return False
    if hour == 17:  # 5-6pm ET daily maintenance break, Monday-Thursday
        return False
    return True


def market_closed_reason(now: datetime | None = None) -> str:
    """Human-readable reason the market is currently closed (Turkish).
    Only meaningful to call when is_forex_market_open(now) is False."""
    if now is None:
        now = datetime.now(timezone.utc)
    now_ny = now.astimezone(NY_TZ)
    weekday = now_ny.weekday()
    hour = now_ny.hour

    if weekday == 5 or (weekday == 6 and hour < 18) or (weekday == 4 and hour >= 17):
        return "hafta sonu"
    if hour == 17:
        return "günlük bakım molası (17:00-18:00 ET)"
    return "kapalı"


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


def is_us_stock_market_open(now: datetime | None = None) -> bool:
    """
    True if NYSE/Nasdaq's regular session is trading: 9:30 AM - 4:00 PM
    ET, Monday-Friday. Unlike Brent/gold, this is a genuinely narrow
    window, not a near-24/5 market. Does not account for US market
    holidays (Thanksgiving, July 4th, etc.) -- add a holiday calendar
    later if that matters.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    now_ny = now.astimezone(NY_TZ)

    if now_ny.weekday() >= 5:  # Saturday or Sunday
        return False

    market_open = now_ny.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now_ny.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open <= now_ny < market_close
