"""
Aggregated Funding Rate (AFR)
------------------------------
Python port of the "Aggregated Funding Rate" TradingView indicator logic
(open-interest-weighted average funding rate across multiple exchanges).

Original indicator: "Aggregated Funding Rate for Crypto with Alerts" by
LuckSoon (Pine Script, Mozilla Public License 2.0). This is an independent
Python re-implementation of its OI-weighting formula using each exchange's
own free public REST API -- no TradingView access involved.

This is a SENTIMENT gauge only. We never open a futures position -- we
only read public funding rate / open interest data to see what leveraged
traders are doing, then feed that into the spot bot's scoring system.
"""

import requests

# Map our CoinGecko-style COIN_ID to the base ticker each exchange expects.
# Extend this if you switch to a coin not listed here.
BASE_SYMBOL_MAP = {
    "bitcoin": "BTC",
    "ethereum": "ETH",
    "solana": "SOL",
    "binancecoin": "BNB",
    "ripple": "XRP",
    "cardano": "ADA",
    "dogecoin": "DOGE",
}


def _safe_get(url, params=None, timeout=8):
    """GET with a short timeout; returns parsed JSON or raises."""
    response = requests.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    return response.json()


def fetch_binance(base: str):
    """Binance USDT-margined perpetual. Funding rate (decimal) + OI (in coins)."""
    symbol = f"{base}USDT"
    fr_data = _safe_get("https://fapi.binance.com/fapi/v1/premiumIndex", {"symbol": symbol})
    oi_data = _safe_get("https://fapi.binance.com/fapi/v1/openInterest", {"symbol": symbol})
    funding_rate = float(fr_data["lastFundingRate"])
    open_interest_coins = float(oi_data["openInterest"])
    return funding_rate, open_interest_coins


def fetch_bybit(base: str):
    """Bybit USDT-margined linear perpetual. OI is already in coin units."""
    symbol = f"{base}USDT"
    data = _safe_get(
        "https://api.bybit.com/v5/market/tickers",
        {"category": "linear", "symbol": symbol},
    )
    ticker = data["result"]["list"][0]
    funding_rate = float(ticker["fundingRate"])
    open_interest_coins = float(ticker["openInterest"])
    return funding_rate, open_interest_coins


def fetch_okx(base: str):
    """OKX USDT-margined swap. oiCcy gives OI already in coin units."""
    inst_id = f"{base}-USDT-SWAP"
    fr_data = _safe_get("https://www.okx.com/api/v5/public/funding-rate", {"instId": inst_id})
    oi_data = _safe_get("https://www.okx.com/api/v5/public/open-interest", {"instId": inst_id})
    funding_rate = float(fr_data["data"][0]["fundingRate"])
    open_interest_coins = float(oi_data["data"][0]["oiCcy"])
    return funding_rate, open_interest_coins


def fetch_bitget(base: str):
    """Bitget USDT-margined perpetual."""
    symbol = f"{base}USDT"
    fr_data = _safe_get(
        "https://api.bitget.com/api/v2/mix/market/current-fund-rate",
        {"symbol": symbol, "productType": "USDT-FUTURES"},
    )
    oi_data = _safe_get(
        "https://api.bitget.com/api/v2/mix/market/open-interest",
        {"symbol": symbol, "productType": "USDT-FUTURES"},
    )
    funding_rate = float(fr_data["data"][0]["fundingRate"])
    open_interest_coins = float(oi_data["data"]["amount"])
    return funding_rate, open_interest_coins


def fetch_coinbase(base: str):
    """
    Coinbase perpetuals run on Coinbase International Exchange, which has
    less standardized public access than the other four. Best-effort only
    -- if this fails, it's simply excluded from the average for that run,
    same as the original indicator does when an exchange returns na.
    """
    product_id = f"{base}-PERP-INTX"
    data = _safe_get(f"https://api.exchange.coinbase.com/products/{product_id}/stats")
    # Coinbase's public stats endpoint doesn't expose funding rate directly
    # in all cases -- this is the weakest link of the five, by design.
    raise NotImplementedError("Coinbase public funding-rate endpoint not wired up yet")


EXCHANGE_FETCHERS = {
    "Binance": fetch_binance,
    "Bybit": fetch_bybit,
    "OKX": fetch_okx,
    "Bitget": fetch_bitget,
    "Coinbase": fetch_coinbase,
}


def get_aggregated_funding_rate(coin_id: str, enabled_exchanges=None) -> dict:
    """
    Pulls funding rate + open interest from each enabled exchange and
    computes the OI-weighted average, exactly like the Pine Script's
    "Open Interest Weighted" mode:

        weighted_fr = sum(OI_i * FR_i) / sum(OI_i)

    Returns a dict with the aggregated rate (as a %, scaled to 8h -- the
    standard funding interval) and which exchanges actually contributed.
    Exchanges that fail or aren't available are silently excluded, same
    as the original indicator's na-handling.
    """
    if enabled_exchanges is None:
        enabled_exchanges = ["Binance", "Bybit", "OKX", "Bitget", "Coinbase"]

    base = BASE_SYMBOL_MAP.get(coin_id)
    if base is None:
        raise ValueError(
            f"No exchange ticker mapping for '{coin_id}'. "
            f"Add it to BASE_SYMBOL_MAP in funding_rate.py."
        )

    sum_oi = 0.0
    sum_weighted_fr = 0.0
    contributing = []
    failed = []

    for name in enabled_exchanges:
        fetcher = EXCHANGE_FETCHERS.get(name)
        if fetcher is None:
            continue
        try:
            funding_rate, open_interest = fetcher(base)
            sum_oi += open_interest
            sum_weighted_fr += open_interest * funding_rate
            contributing.append(name)
        except Exception as e:
            failed.append((name, str(e)))

    if sum_oi == 0:
        return {
            "aggregated_rate_pct": None,
            "contributing_exchanges": contributing,
            "failed_exchanges": failed,
        }

    weighted_fr_raw = sum_weighted_fr / sum_oi
    aggregated_rate_pct = weighted_fr_raw * 100  # funding rates are per-8h by convention

    return {
        "aggregated_rate_pct": aggregated_rate_pct,
        "contributing_exchanges": contributing,
        "failed_exchanges": failed,
    }


def score_funding_rate(aggregated_rate_pct: float | None) -> int:
    """
    Contrarian scoring: extreme positive funding = crowded longs paying
    shorts = overheated to the upside = bearish reversal risk. Extreme
    negative = crowded shorts = bullish reversal risk. Thresholds follow
    commonly cited conventions (>0.05% strongly bullish-crowded,
    <-0.01% bearish-crowded), applied here as CONTRARIAN signals.
    """
    if aggregated_rate_pct is None:
        return 0
    if aggregated_rate_pct > 0.05:
        return -1  # crowded longs -> squeeze/reversal risk down
    if aggregated_rate_pct < -0.01:
        return 1   # crowded shorts -> squeeze/reversal risk up
    return 0
