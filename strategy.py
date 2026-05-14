import pandas as pd

from config import STOCK_SYMBOLS, STOCK_BB_PERIOD, STOCK_BB_STD


def bollinger_bands(df: pd.DataFrame, period: int, std: float) -> pd.DataFrame:
    df = df.copy()
    df["MA"]    = df["Close"].rolling(period).mean()
    df["STD"]   = df["Close"].rolling(period).std()
    df["Upper"] = df["MA"] + std * df["STD"]
    df["Lower"] = df["MA"] - std * df["STD"]
    return df


def get_signal(df: pd.DataFrame) -> str:
    if len(df) < STOCK_BB_PERIOD:
        return "HOLD"

    df   = bollinger_bands(df, STOCK_BB_PERIOD, STOCK_BB_STD)
    last = df.iloc[-1]

    if last["Close"] < last["Lower"]:
        return "BUY"
    if last["Close"] > last["Upper"]:
        return "SELL"
    return "HOLD"


def scan(api, fetch_fn) -> dict:
    signals = {}
    for symbol in STOCK_SYMBOLS:
        df = fetch_fn(api, symbol)
        signals[symbol] = get_signal(df) if not df.empty else "HOLD"
    return signals
