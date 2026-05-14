from datetime import datetime, timedelta
import pandas as pd
from alpaca_trade_api.rest import REST, TimeFrame

from config import ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_BASE_URL

CRYPTO_LOOKBACK_HOURS = 240


def get_api() -> REST:
    return REST(ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_BASE_URL)


def fetch_crypto_bars(api: REST, symbol: str, hours: int = CRYPTO_LOOKBACK_HOURS) -> pd.DataFrame:
    """
    Fetch hourly OHLCV bars for a crypto symbol (e.g. 'BTC/USD') via Alpaca's
    v1beta3 crypto data endpoint. Returns a DataFrame with columns
    Open, High, Low, Close, Volume indexed by UTC timestamp.
    """
    end   = datetime.utcnow()
    start = end - timedelta(hours=hours)

    bars = api.get_crypto_bars(
        symbol,
        TimeFrame.Hour,
        start=start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        end=end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        loc="us",
    ).df

    if bars.empty:
        return pd.DataFrame()

    if isinstance(bars.index, pd.MultiIndex):
        bars = bars.xs(symbol, level="symbol")

    bars.index = (
        bars.index.tz_localize(None)
        if bars.index.tzinfo is None
        else bars.index.tz_convert(None)
    )

    bars = bars.rename(columns={
        "open":   "Open",
        "high":   "High",
        "low":    "Low",
        "close":  "Close",
        "volume": "Volume",
    })
    return bars[["Open", "High", "Low", "Close", "Volume"]]
