"""
Walk-forward backtest of the crypto BB+RSI+ADX strategy.

Replays the exact same indicator + gate logic as the live bot on hourly bars.
Reports P&L for 7-day, 30-day, and 60-day windows.

Usage:
  cd ~/Documents/alpaca_trading_bot
  python3 backtest.py
"""
import sys
import os
from datetime import datetime, timedelta

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))

from crypto_data import get_api
from crypto_strategy import add_indicators
from config import (
    CRYPTO_SYMBOLS,
    CRYPTO_RSI_OVERSOLD, CRYPTO_ADX_THRESHOLD,
    CRYPTO_BB_WIDTH_MIN, CRYPTO_BB_WIDTH_MAX,
    CRYPTO_TAKE_PROFIT, CRYPTO_STOP_LOSS,
    CRYPTO_RISK_PCT,
)
from alpaca_trade_api.rest import TimeFrame

LOOKBACK_DAYS     = 63          # 60-day window + 3 days indicator warmup
PORTFOLIO         = 121_650.0
TRADE_SIZE        = PORTFOLIO * CRYPTO_RISK_PCT   # ~$10,133 per trade
WINDOWS           = [7, 30, 60]
WARMUP_BARS       = 55           # max(BB=48, RSI=14, ADX=14) + buffer
SL_COOLDOWN_HOURS = 4            # block re-entry N hours after a stop-loss on that symbol
DAILY_LOSS_HALT   = 0.0075       # halt after 0.75% daily portfolio loss


def _fetch(api, symbol: str) -> pd.DataFrame:
    end   = datetime.utcnow()
    start = end - timedelta(days=LOOKBACK_DAYS)
    bars  = api.get_crypto_bars(
        symbol, TimeFrame.Hour,
        start=start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        end=end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        loc="us",
    ).df
    if bars.empty:
        return pd.DataFrame()
    if isinstance(bars.index, pd.MultiIndex):
        bars = bars.xs(symbol, level="symbol")
    bars.index = (
        bars.index.tz_convert(None) if bars.index.tzinfo else bars.index.tz_localize(None)
    )
    return bars.rename(columns={
        "open": "Open", "high": "High", "low": "Low",
        "close": "Close", "volume": "Volume",
    })[["Open", "High", "Low", "Close", "Volume"]]


def _backtest_symbol(symbol: str, df: pd.DataFrame) -> list[dict]:
    """Walk forward bar-by-bar with per-symbol SL cooldown and daily loss halt."""
    df  = add_indicators(df)

    pos           = None
    trades        = []
    daily_loss    = {}   # date → cumulative loss USD that day
    sl_cooldown_until = None   # timestamp after which re-entry is allowed

    for i in range(WARMUP_BARS, len(df)):
        row  = df.iloc[i]
        ts   = df.index[i]
        day  = ts.date()

        # ── Check exit ────────────────────────────────────────────────────────
        if pos is not None:
            sl_hit = row["Low"]  <= pos["sl"]
            tp_hit = row["High"] >= pos["tp"]

            if sl_hit or tp_hit:
                exit_price  = pos["sl"] if sl_hit else pos["tp"]
                exit_reason = "SL"      if sl_hit else "TP"
                pnl_pct     = (exit_price - pos["entry_price"]) / pos["entry_price"]
                pnl_usd     = TRADE_SIZE * pnl_pct
                daily_loss[day] = daily_loss.get(day, 0) + min(pnl_usd, 0)
                if exit_reason == "SL":
                    sl_cooldown_until = ts + pd.Timedelta(hours=SL_COOLDOWN_HOURS)
                trades.append({
                    "symbol":      symbol,
                    "entry_bar":   pos["entry_bar"],
                    "exit_bar":    ts,
                    "entry_price": pos["entry_price"],
                    "exit_price":  exit_price,
                    "exit_reason": exit_reason,
                    "pnl_pct":     pnl_pct * 100,
                    "pnl_usd":     pnl_usd,
                    "rsi_entry":   pos["rsi"],
                    "adx_entry":   pos["adx"],
                })
                pos = None

        # ── Check entry ───────────────────────────────────────────────────────
        if pos is None:
            # Daily loss halt (0.75% of portfolio)
            if abs(daily_loss.get(day, 0)) / PORTFOLIO >= DAILY_LOSS_HALT:
                continue
            # Per-symbol SL cooldown
            if sl_cooldown_until is not None and ts < sl_cooldown_until:
                continue

            below_lower  = row["Close"]  < row["Lower"]
            rsi_oversold = row["RSI"]    < CRYPTO_RSI_OVERSOLD
            high_volume  = row["Volume"] > row["VolMA"]
            adx_ranging  = row["ADX"]    < CRYPTO_ADX_THRESHOLD
            bb_width_ok  = CRYPTO_BB_WIDTH_MIN < row["BBW"] < CRYPTO_BB_WIDTH_MAX

            if below_lower and rsi_oversold and high_volume and adx_ranging and bb_width_ok:
                ep = row["Close"]
                tp = max(round(ep * (1 + CRYPTO_TAKE_PROFIT), 6), round(ep + 0.01, 6))
                sl = round(ep * (1 - CRYPTO_STOP_LOSS), 6)
                pos = {
                    "entry_bar":   ts,
                    "entry_price": ep,
                    "tp":          tp,
                    "sl":          sl,
                    "rsi":         row["RSI"],
                    "adx":         row["ADX"],
                }

    return trades


def _summarise(all_trades: list[dict], window_days: int) -> dict:
    cutoff = datetime.utcnow() - timedelta(days=window_days)
    trades = [t for t in all_trades if pd.Timestamp(t["exit_bar"]) >= cutoff]
    if not trades:
        return {"window": window_days, "trades": 0}
    wins   = [t for t in trades if t["pnl_usd"] > 0]
    losses = [t for t in trades if t["pnl_usd"] <= 0]
    return {
        "window":   window_days,
        "trades":   len(trades),
        "wins":     len(wins),
        "losses":   len(losses),
        "win_rate": round(len(wins) / len(trades) * 100, 1),
        "pnl_usd":  round(sum(t["pnl_usd"] for t in trades), 2),
        "pnl_pct":  round(sum(t["pnl_usd"] for t in trades) / PORTFOLIO * 100, 2),
        "avg_win":  round(sum(t["pnl_pct"] for t in wins)   / len(wins)   if wins   else 0, 2),
        "avg_loss": round(sum(t["pnl_pct"] for t in losses) / len(losses) if losses else 0, 2),
        "trades_list": trades,
    }


def main():
    api = get_api()
    print(f"\nFetching {LOOKBACK_DAYS} days of hourly data for {len(CRYPTO_SYMBOLS)} pairs …")
    print(f"Filters: SL cooldown ({SL_COOLDOWN_HOURS}h per symbol) + daily loss halt ({DAILY_LOSS_HALT*100:.2f}%)\n")

    all_trades = []
    for symbol in CRYPTO_SYMBOLS:
        print(f"  {symbol:12s}", end=" ", flush=True)
        df = _fetch(api, symbol)
        if df.empty or len(df) < WARMUP_BARS + 10:
            print("SKIP (insufficient data)")
            continue
        trades = _backtest_symbol(symbol, df)
        wins   = sum(1 for t in trades if t["pnl_usd"] > 0)
        print(f"{len(trades):3d} trades  {wins}W/{len(trades)-wins}L  "
              f"net ${sum(t['pnl_usd'] for t in trades):+,.0f}")
        all_trades.extend(trades)

    all_trades.sort(key=lambda t: t["exit_bar"])

    print(f"\nTotal closed trades ({LOOKBACK_DAYS}d): {len(all_trades)}")

    # ── Window summaries ──────────────────────────────────────────────────────
    for days in WINDOWS:
        r = _summarise(all_trades, days)
        bar = "=" * 52
        print(f"\n{bar}")
        print(f"  {days}-DAY BACKTEST  (portfolio = ${PORTFOLIO:,.0f})")
        print(bar)
        if r["trades"] == 0:
            print("  No completed trades in this window.")
            continue
        print(f"  Trades:    {r['trades']}  ({r['wins']}W / {r['losses']}L)")
        print(f"  Win rate:  {r['win_rate']}%")
        print(f"  Net P&L:   ${r['pnl_usd']:+,.2f}  ({r['pnl_pct']:+.2f}% of portfolio)")
        print(f"  Avg win:   +{r['avg_win']:.2f}%    Avg loss: {r['avg_loss']:.2f}%")

        tl = sorted(r["trades_list"], key=lambda t: t["exit_bar"])
        print(f"\n  {'Symbol':<10} {'Exit':<6} {'Entry date':<18} {'Exit date':<18} {'P&L $':>9}  {'P&L %':>7}  RSI  ADX")
        for t in tl:
            print(f"  {t['symbol']:<10} {t['exit_reason']:<6} "
                  f"{str(t['entry_bar'])[:16]:<18} {str(t['exit_bar'])[:16]:<18} "
                  f"${t['pnl_usd']:>+8,.2f}  {t['pnl_pct']:>+6.2f}%  "
                  f"{t['rsi_entry']:4.1f}  {t['adx_entry']:4.1f}")

    print("\nNotes:")
    print("  • Per-trade size: ${:,.0f} (8.33% of portfolio)".format(TRADE_SIZE))
    print("  • Max 4 simultaneous positions not enforced per-symbol (may overstate gains)")
    print("  • Entry at bar close; SL/TP checked against bar High/Low")
    print("  • SL takes precedence when both TP and SL triggered in same bar")


if __name__ == "__main__":
    main()
