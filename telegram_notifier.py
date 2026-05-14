"""
Daily Telegram summary for the Alpaca crypto bot.
Uses the Telegram Bot API (pure HTTPS — works on Railway, no SMTP needed).

Env vars:
  TELEGRAM_TOKEN   — bot token from BotFather
  TELEGRAM_CHAT_ID — your personal chat ID (fetched via getUpdates)
"""
import os
import logging
import requests
from datetime import datetime, timezone

from performance import get_summary
from crypto_data import get_api
from crypto_trader import _CRYPTO_SYMS

logger = logging.getLogger(__name__)

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN",   "8814468120:AAF8UWakbHyEE6W0pl7m2lbIEKgNmSDZPEY")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "7443859269")


def _send(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    resp = requests.post(url, data={
        "chat_id":    TELEGRAM_CHAT_ID,
        "text":       text,
        "parse_mode": "HTML",
    }, timeout=10)
    if not resp.json().get("ok"):
        raise RuntimeError(resp.text)


def _portfolio_snapshot(api) -> dict:
    acc = api.get_account()
    positions = []
    for p in api.list_positions():
        if p.symbol not in _CRYPTO_SYMS:
            continue
        positions.append({
            "symbol":  p.symbol,
            "pnl_pct": round(float(p.unrealized_plpc) * 100, 2),
            "pnl_usd": round(float(p.unrealized_pl), 2),
        })
    return {
        "portfolio_value": round(float(acc.portfolio_value), 2),
        "cash":            round(float(acc.cash), 2),
        "positions":       positions,
    }


def _build_message(snap: dict, perf: dict) -> str:
    now = datetime.now(timezone.utc).strftime("%b %d, %Y  %H:%M UTC")
    pv  = snap["portfolio_value"]

    # ── Header ────────────────────────────────────────────────────────────────
    lines = [
        f"📊 <b>Alpaca Crypto Bot — Daily Report</b>",
        f"<i>{now}</i>",
        "",
        f"💼 <b>Portfolio:</b> ${pv:,.2f}",
        f"💵 <b>Cash:</b> ${snap['cash']:,.2f}",
    ]

    # ── Open positions ────────────────────────────────────────────────────────
    if snap["positions"]:
        lines.append("")
        lines.append("📌 <b>Open Positions</b>")
        for p in snap["positions"]:
            arrow = "🟢" if p["pnl_pct"] >= 0 else "🔴"
            lines.append(f"  {arrow} {p['symbol']}  {p['pnl_pct']:+.2f}%  (${p['pnl_usd']:+,.2f})")
    else:
        lines.append("📌 <b>Positions:</b> none — waiting for signal")

    # ── 7-day performance ─────────────────────────────────────────────────────
    lines.append("")
    lines.append("📈 <b>7-Day Performance</b>")

    if perf["total_trades"] == 0:
        lines.append("  No completed trades yet")
    else:
        wr  = perf["win_rate_pct"]
        net = perf["net_pnl_usd"]
        ret = perf["portfolio_return_pct"]
        net_emoji = "🟢" if net >= 0 else "🔴"
        lines += [
            f"  Trades:    {perf['total_trades']}  ({perf['wins']}W / {perf['losses']}L)",
            f"  Win rate:  {wr}%",
            f"  Avg win:   +{perf['avg_win_pct']}%   Avg loss: {perf['avg_loss_pct']}%",
            f"  {net_emoji} Net P&L:   ${net:+,.2f}  ({ret:+.3f}%)",
        ]

        # By-symbol breakdown
        if perf["by_symbol"]:
            lines.append("")
            lines.append("  <b>By symbol:</b>")
            for sym, v in sorted(perf["by_symbol"].items(),
                                 key=lambda x: -x[1]["net_usd"]):
                e = "🟢" if v["net_usd"] >= 0 else "🔴"
                lines.append(
                    f"  {e} {sym:<10} {v['trades']}T  "
                    f"{v['win_rate']:.0f}%WR  ${v['net_usd']:+,.2f}"
                )

    # ── Footer ────────────────────────────────────────────────────────────────
    lines += [
        "",
        "<i>BB(48)+RSI(14)+ADX(28) · 10 pairs · TP +2.5% / SL -1.5%</i>",
    ]

    return "\n".join(lines)


def send_daily_summary():
    try:
        api  = get_api()
        snap = _portfolio_snapshot(api)
        perf = get_summary(days=7)
        msg  = _build_message(snap, perf)
        _send(msg)
        logger.info("Daily Telegram summary sent to chat %s", TELEGRAM_CHAT_ID)
    except Exception as e:
        logger.error("Failed to send Telegram summary: %s", e)


def send_alert(text: str):
    """Send an immediate alert (e.g. trade fired, error)."""
    try:
        _send(text)
    except Exception as e:
        logger.error("Telegram alert failed: %s", e)
