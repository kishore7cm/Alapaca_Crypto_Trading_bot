"""
Run standalone: python3 performance.py [days]
Or called via the /performance API endpoint as get_summary(days).
"""
from datetime import datetime, timedelta
import pandas as pd
from crypto_data import get_api

_CRYPTO_SYMS = {
    "BTCUSD", "ETHUSD", "XRPUSD", "UNIUSD",
    "LINKUSD", "LTCUSD", "AAVEUSD", "AVAXUSD",
    "DOTUSD", "ADAUSD",
}


def _filled_orders(api, days: int) -> pd.DataFrame:
    after = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    orders = api.list_orders(status="closed", after=after, limit=500)
    rows = []
    for o in orders:
        if o.symbol not in _CRYPTO_SYMS:
            continue
        if not o.filled_qty or float(o.filled_qty) == 0:
            continue
        rows.append({
            "symbol":    o.symbol,
            "side":      o.side,
            "qty":       float(o.filled_qty),
            "avg_price": float(o.filled_avg_price) if o.filled_avg_price else 0.0,
            "filled_at": str(o.filled_at),
        })
    return pd.DataFrame(rows)


def _pair_trades(df: pd.DataFrame) -> list:
    trades = []
    for sym in df["symbol"].unique():
        sub   = df[df["symbol"] == sym].sort_values("filled_at").reset_index(drop=True)
        buys  = sub[sub["side"] == "buy"].reset_index(drop=True)
        sells = sub[sub["side"] == "sell"].reset_index(drop=True)
        for _, buy in buys.iterrows():
            later = sells[sells["filled_at"] > buy["filled_at"]]
            if later.empty:
                continue
            sell    = later.iloc[0]
            entry   = buy["avg_price"]
            exit_px = sell["avg_price"]
            pct     = (exit_px - entry) / entry * 100
            trades.append({
                "symbol":   sym,
                "entry":    round(entry, 4),
                "exit":     round(exit_px, 4),
                "qty":      buy["qty"],
                "pnl_pct":  round(pct, 3),
                "pnl_usd":  round((exit_px - entry) * buy["qty"], 2),
                "result":   "WIN" if pct > 0 else "LOSS",
                "opened":   buy["filled_at"],
            })
    return trades


def get_summary(days: int = 7) -> dict:
    """Returns a structured dict — safe to call from FastAPI."""
    api       = get_api()
    portfolio = float(api.get_account().portfolio_value)
    df        = _filled_orders(api, days=days)
    trades    = _pair_trades(df) if not df.empty else []

    if not trades:
        return {
            "period_days":     days,
            "portfolio_value": portfolio,
            "total_trades":    0,
            "wins":            0,
            "losses":          0,
            "win_rate_pct":    None,
            "avg_win_pct":     None,
            "avg_loss_pct":    None,
            "expectancy_pct":  None,
            "net_pnl_usd":     0.0,
            "portfolio_return_pct": 0.0,
            "trades":          [],
            "by_symbol":       {},
            "note": "No completed round-trip trades yet — bot needs to open and close a position first.",
        }

    t   = pd.DataFrame(trades)
    wins   = t[t["result"] == "WIN"]
    losses = t[t["result"] == "LOSS"]

    win_rate   = len(wins) / len(t) * 100
    avg_win    = float(wins["pnl_pct"].mean())   if not wins.empty   else 0.0
    avg_loss   = float(losses["pnl_pct"].mean()) if not losses.empty else 0.0
    expectancy = (win_rate / 100 * avg_win) + ((1 - win_rate / 100) * avg_loss)
    net_pnl    = float(t["pnl_usd"].sum())

    by_symbol = {}
    for sym, grp in t.groupby("symbol"):
        by_symbol[sym] = {
            "trades":   int(len(grp)),
            "wins":     int((grp["result"] == "WIN").sum()),
            "net_usd":  round(float(grp["pnl_usd"].sum()), 2),
            "win_rate": round(float((grp["result"] == "WIN").mean() * 100), 1),
        }

    return {
        "period_days":          days,
        "portfolio_value":      portfolio,
        "total_trades":         len(t),
        "wins":                 len(wins),
        "losses":               len(losses),
        "win_rate_pct":         round(win_rate, 1),
        "avg_win_pct":          round(avg_win, 3),
        "avg_loss_pct":         round(avg_loss, 3),
        "expectancy_pct":       round(expectancy, 3),
        "net_pnl_usd":          round(net_pnl, 2),
        "portfolio_return_pct": round(net_pnl / portfolio * 100, 3),
        "trades":               sorted(trades, key=lambda x: x["pnl_usd"], reverse=True),
        "by_symbol":            by_symbol,
    }


# ── Standalone CLI ────────────────────────────────────────────────────────────

def _print_report(days: int):
    s = get_summary(days)
    print(f"\n{'='*60}")
    print(f"  CRYPTO BOT PERFORMANCE — last {days} days")
    print(f"  Portfolio: ${s['portfolio_value']:,.2f}")
    print(f"{'='*60}")
    if s["total_trades"] == 0:
        print(f"  {s['note']}")
        print(f"{'='*60}\n")
        return
    print(f"  Trades      : {s['total_trades']}  ({s['wins']}W / {s['losses']}L)")
    print(f"  Win rate    : {s['win_rate_pct']}%")
    print(f"  Avg win     : +{s['avg_win_pct']}%")
    print(f"  Avg loss    : {s['avg_loss_pct']}%")
    print(f"  Expectancy  : {s['expectancy_pct']}% per trade")
    print(f"  Net P&L     : ${s['net_pnl_usd']:+,.2f}")
    print(f"  Return      : {s['portfolio_return_pct']:+.3f}%")
    print()
    print(f"  {'Symbol':<10} {'Trades':>6} {'Wins':>5} {'Win%':>6} {'Net $':>10}")
    print(f"  {'-'*42}")
    for sym, v in sorted(s["by_symbol"].items(), key=lambda x: -x[1]["net_usd"]):
        print(f"  {sym:<10} {v['trades']:>6} {v['wins']:>5} {v['win_rate']:>5.0f}% {v['net_usd']:>+10.2f}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    import sys
    _print_report(int(sys.argv[1]) if len(sys.argv) > 1 else 7)
