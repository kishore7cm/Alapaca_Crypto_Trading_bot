from datetime import datetime, timedelta
import pandas as pd
from alpaca_trade_api.rest import REST, TimeFrame

from config import ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_BASE_URL


def get_api() -> REST:
    return REST(ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_BASE_URL)


def fetch_bars(api: REST, symbol: str, days: int = 30) -> pd.DataFrame:
    end   = datetime.utcnow()
    start = end - timedelta(days=days)

    bars = api.get_bars(
        symbol,
        TimeFrame.Day,
        start=start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        end=end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        feed="iex",
    ).df

    if bars.empty:
        return pd.DataFrame()

    bars.index = bars.index.tz_localize(None) if bars.index.tzinfo is None else bars.index.tz_convert(None)
    bars = bars.rename(columns={"open": "Open", "high": "High", "low": "Low",
                                 "close": "Close", "volume": "Volume"})
    return bars[["Open", "High", "Low", "Close", "Volume"]]
