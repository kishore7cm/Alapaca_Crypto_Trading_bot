import numpy as np
import pandas as pd

from config import (
    CRYPTO_SYMBOLS,
    CRYPTO_BB_PERIOD, CRYPTO_BB_STD,
    CRYPTO_RSI_PERIOD, CRYPTO_RSI_OVERSOLD, CRYPTO_RSI_OVERBOUGHT,
    CRYPTO_ADX_PERIOD, CRYPTO_ADX_THRESHOLD,
    CRYPTO_BB_WIDTH_MIN, CRYPTO_BB_WIDTH_MAX,
)


# ── Indicator calculations ────────────────────────────────────────────────────

def _bollinger(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["MA"]     = df["Close"].rolling(CRYPTO_BB_PERIOD).mean()
    df["STD"]    = df["Close"].rolling(CRYPTO_BB_PERIOD).std()
    df["Upper"]  = df["MA"] + CRYPTO_BB_STD * df["STD"]
    df["Lower"]  = df["MA"] - CRYPTO_BB_STD * df["STD"]
    df["BBW"]    = (df["Upper"] - df["Lower"]) / df["MA"]   # BB Width
    return df


def _rsi(df: pd.DataFrame) -> pd.DataFrame:
    delta = df["Close"].diff()
    gain  = delta.clip(lower=0).rolling(CRYPTO_RSI_PERIOD).mean()
    loss  = (-delta.clip(upper=0)).rolling(CRYPTO_RSI_PERIOD).mean()
    rs    = gain / loss.replace(0, float("inf"))
    df    = df.copy()
    df["RSI"] = 100 - (100 / (1 + rs))
    return df


def _adx(df: pd.DataFrame) -> pd.DataFrame:
    """
    Wilder's ADX (Average Directional Index).
    ADX < ADX_THRESHOLD = ranging = safe for mean reversion.
    ADX >= ADX_THRESHOLD = trending = skip entry.
    """
    hi, lo, cl = df["High"], df["Low"], df["Close"]
    n = CRYPTO_ADX_PERIOD

    tr = pd.concat([
        hi - lo,
        (hi - cl.shift(1)).abs(),
        (lo - cl.shift(1)).abs(),
    ], axis=1).max(axis=1)

    dm_plus  = np.where((hi - hi.shift(1)) > (lo.shift(1) - lo),
                         np.maximum(hi - hi.shift(1), 0), 0)
    dm_minus = np.where((lo.shift(1) - lo) > (hi - hi.shift(1)),
                         np.maximum(lo.shift(1) - lo, 0), 0)

    atr14    = tr.ewm(alpha=1 / n, min_periods=n, adjust=False).mean()
    di_plus  = 100 * pd.Series(dm_plus,  index=df.index).ewm(alpha=1 / n, min_periods=n, adjust=False).mean() / atr14
    di_minus = 100 * pd.Series(dm_minus, index=df.index).ewm(alpha=1 / n, min_periods=n, adjust=False).mean() / atr14

    dx  = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus).replace(0, float("inf"))
    adx = dx.ewm(alpha=1 / n, min_periods=n, adjust=False).mean()

    df = df.copy()
    df["ATR14"] = atr14
    df["ADX"]   = adx
    return df


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = _bollinger(df)
    df = _rsi(df)
    df = _adx(df)
    df["VolMA"] = df["Volume"].rolling(20).mean()
    return df


# ── Signal logic ──────────────────────────────────────────────────────────────

def get_signal(df: pd.DataFrame) -> tuple[str, dict]:
    """
    BUY  — all five gates must pass:
      1. Close < Lower BB          (price is stretched below the mean)
      2. RSI < CRYPTO_RSI_OVERSOLD (momentum confirms oversold)
      3. Volume > 20-bar VolMA     (real selling pressure, not thin air)
      4. ADX < CRYPTO_ADX_THRESHOLD (ranging market — mean reversion valid)
      5. BB Width in [MIN, MAX]    (bands are active but not extreme)

    SELL signal logged for awareness (bracket TP handles long exits automatically).

    HOLD otherwise.
    """
    min_bars = max(CRYPTO_BB_PERIOD, CRYPTO_RSI_PERIOD, CRYPTO_ADX_PERIOD) + 5
    if len(df) < min_bars:
        return "HOLD", {}

    df   = add_indicators(df)
    last = df.iloc[-1]

    ctx = {
        "close":   float(round(last["Close"],  4)),
        "lower":   float(round(last["Lower"],  4)),
        "upper":   float(round(last["Upper"],  4)),
        "ma":      float(round(last["MA"],     4)),
        "rsi":     float(round(last["RSI"],    1)),
        "adx":     float(round(last["ADX"],    1)),
        "bbw":     float(round(last["BBW"],    4)),
        "atr14":   float(round(last["ATR14"],  4)),
        "vol":     float(round(last["Volume"], 4)),
        "vol_ma":  float(round(last["VolMA"],  4)),
    }

    # ── Gate checks ───────────────────────────────────────────────────────────
    below_lower  = last["Close"] < last["Lower"]
    above_upper  = last["Close"] > last["Upper"]
    rsi_oversold = last["RSI"]   < CRYPTO_RSI_OVERSOLD
    rsi_overbought = last["RSI"] > CRYPTO_RSI_OVERBOUGHT
    high_volume  = last["Volume"] > last["VolMA"]
    adx_ranging  = last["ADX"]   < CRYPTO_ADX_THRESHOLD
    bb_width_ok  = CRYPTO_BB_WIDTH_MIN < last["BBW"] < CRYPTO_BB_WIDTH_MAX

    ctx["gates"] = {
        "below_lower":   bool(below_lower),
        "rsi_oversold":  bool(rsi_oversold),
        "high_volume":   bool(high_volume),
        "adx_ranging":   bool(adx_ranging),
        "bb_width_ok":   bool(bb_width_ok),
    }

    if below_lower and rsi_oversold and high_volume and adx_ranging and bb_width_ok:
        return "BUY", ctx

    if above_upper and rsi_overbought and adx_ranging:
        return "SELL", ctx

    return "HOLD", ctx


def scan(api, fetch_fn) -> dict:
    """Return {symbol: (signal, context)} for all symbols in CRYPTO_SYMBOLS."""
    results = {}
    for symbol in CRYPTO_SYMBOLS:
        df = fetch_fn(api, symbol)
        results[symbol] = get_signal(df) if not df.empty else ("HOLD", {})
    return results
