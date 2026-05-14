"""
Run: python3 performance.py
Reads Alpaca order history and prints a P&L report for the crypto bot.
"""
from datetime import datetime, timedelta
import pandas as pd
from crypto_data import get_api

_CRYPTO_SYMS = {
    "BTCUSD", "ETHUSD", "XRPUSD", "UNIUSD",
    "LINKUSD", "LTCUSD", "AAVEUSD", "AVAXUSD",
    "DOTUSD", "ADAUSD",
}


def _get_filled_orders(api, days: int = 30) -> pd.DataFrame:
    after = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    orders = api.list_orders(status="closed", after=after, limit=500)

    rows = []
    for o in orders:
        if o.symbol not in _CRYPTO_SYMS:
            continue
        if o.filled_qty is None or float(o.filled_qty) == 0:
            continue
        rows.append({
            "symbol":     o.symbol,
            "side":       o.side,
            "qty":        float(o.filled_qty),
            "avg_price":  float(o.filled_avg_price) if o.filled_avg_price else 0,
            "filled_at":  o.filled_at,
            "order_class": getattr(o, "order_class", ""),
            "type":       o.type,
        })
    return pd.DataFrame(rows)


def _pair_trades(df: pd.DataFrame) -> list[dict]:
    """
    Match buy orders with their subsequent sell (TP or SL) orders per symbol.
    Returns a list of completed round-trip trade dicts.
    """
    trades = []
    for sym in df["symbol"].unique():
        sub = df[df["symbol"] == sym].sort_values("filled_at").reset_index(drop=True)
        buys  = sub[sub["side"] == "buy"].reset_index(drop=True)
        sells = sub[sub["side"] == "sell"].reset_index(drop=True)

        for i, buy in buys.iterrows():
            # find earliest sell after this buy
            later_sells = sells[sells["filled_at"] > buy["filled_at"]]
            if later_sells.empty:
                continue
            sell = later_sells.iloc[0]
            entry   = buy["avg_price"]
            exit_px = sell["avg_price"]
            pct     = (exit_px - entry) / entry * 100
            trades.append({
                "symbol":  sym,
                "entry":   round(entry, 4),
                "exit":    round(exit_px, 4),
                "qty":     buy["qty"],
                "pnl_pct": round(pct, 3),
                "pnl_$":   round((exit_px - entry) * buy["qty"], 2),
                "result":  "WIN" if pct > 0 else "LOSS",
            })
    return trades


def report(days: int = 7):
    api    = get_api()
    acc    = api.get_account()
    portfolio = float(acc.portfolio_value)

    df     = _get_filled_orders(api, days=days)
    trades = _pair_trades(df)

    print(f"\n{'='*60}")
    print(f"  CRYPTO BOT PERFORMANCE — last {days} days")
    print(f"  Portfolio value: ${portfolio:,.2f}")
    print(f"{'='*60}")

    if not trades:
        print("  No completed round-trip trades found.")
        print(f"{'='*60}\n")
        return

    t_df   = pd.DataFrame(trades)
    wins   = t_df[t_df["result"] == "WIN"]
    losses = t_df[t_df["result"] == "LOSS"]

    total_pnl   = t_df["pnl_$"].sum()
    win_rate    = len(wins) / len(t_df) * 100
    avg_win_pct = wins["pnl_pct"].mean()   if not wins.empty   else 0
    avg_los_pct = losses["pnl_pct"].mean() if not losses.empty else 0
    expectancy  = (win_rate/100 * avg_win_pct) + ((1-win_rate/100) * avg_los_pct)
    max_dd      = t_df["pnl_$"].cumsum().cummin().min()

    print(f"\n  Total trades   : {len(t_df)}")
    print(f"  Wins / Losses  : {len(wins)} / {len(losses)}")
    print(f"  Win rate       : {win_rate:.1f}%")
    print(f"  Avg win        : +{avg_win_pct:.2f}%")
    print(f"  Avg loss       : {avg_los_pct:.2f}%")
    print(f"  Expectancy     : {expectancy:.3f}% per trade")
    print(f"  Net P&L        : ${total_pnl:+,.2f}")
    print(f"  Portfolio ret  : {total_pnl/portfolio*100:+.2f}%")
    print(f"  Max drawdown   : ${max_dd:,.2f}")

    print(f"\n  {'Symbol':<10} {'Result':<6} {'Entry':>10} {'Exit':>10} {'P&L%':>8} {'P&L$':>10}")
    print(f"  {'-'*56}")
    for _, row in t_df.sort_values("pnl_$", ascending=False).iterrows():
        print(f"  {row['symbol']:<10} {row['result']:<6} "
              f"{row['entry']:>10.4f} {row['exit']:>10.4f} "
              f"{row['pnl_pct']:>+8.2f}% {row['pnl_$']:>+10.2f}")

    print(f"\n  By symbol:")
    sym_summary = t_df.groupby("symbol").agg(
        trades=("pnl_$", "count"),
        net_pnl=("pnl_$", "sum"),
        win_rate=("result", lambda x: (x == "WIN").mean() * 100),
    ).sort_values("net_pnl", ascending=False)
    for sym, row in sym_summary.iterrows():
        print(f"    {sym:<10} trades={int(row['trades'])}  "
              f"win={row['win_rate']:.0f}%  net=${row['net_pnl']:+.2f}")

    print(f"{'='*60}\n")


if __name__ == "__main__":
    import sys
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    report(days=days)
