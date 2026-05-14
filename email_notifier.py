"""
Daily email summary sent via SendGrid API (pure HTTPS — works on Railway).
Requires two env vars:
  SENDGRID_API_KEY  — from app.sendgrid.com → Settings → API Keys (free account)
  GMAIL_USER        — your Gmail address (recipient)
"""
import os
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

from performance import get_summary
from crypto_data import get_api
from crypto_trader import crypto_position_count, _CRYPTO_SYMS


def _portfolio_snapshot(api) -> dict:
    acc = api.get_account()
    positions = []
    for p in api.list_positions():
        if p.symbol not in _CRYPTO_SYMS:
            continue
        positions.append({
            "symbol":    p.symbol,
            "qty":       float(p.qty),
            "entry":     round(float(p.avg_entry_price), 4),
            "current":   round(float(p.current_price), 4),
            "pnl_usd":   round(float(p.unrealized_pl), 2),
            "pnl_pct":   round(float(p.unrealized_plpc) * 100, 2),
        })
    return {
        "portfolio_value": round(float(acc.portfolio_value), 2),
        "cash":            round(float(acc.cash), 2),
        "positions":       positions,
    }


def _build_html(snap: dict, perf: dict) -> str:
    now    = datetime.now(timezone.utc).strftime("%B %d, %Y  %H:%M UTC")
    pv     = snap["portfolio_value"]
    cash   = snap["cash"]
    trades = perf["total_trades"]
    wins   = perf["wins"]
    losses = perf["losses"]
    net    = perf["net_pnl_usd"]
    ret    = perf["portfolio_return_pct"]
    note   = perf.get("note", "")

    wr_str  = f"{perf['win_rate_pct']}%" if perf["win_rate_pct"] is not None else "—"
    net_col = "#27ae60" if net >= 0 else "#e74c3c"
    ret_col = "#27ae60" if ret >= 0 else "#e74c3c"

    # ── Open positions table ──────────────────────────────────────────────────
    if snap["positions"]:
        pos_rows = ""
        for p in snap["positions"]:
            c = "#27ae60" if p["pnl_pct"] >= 0 else "#e74c3c"
            pos_rows += f"""
            <tr>
              <td style="padding:6px 12px;border-bottom:1px solid #eee">{p['symbol']}</td>
              <td style="padding:6px 12px;border-bottom:1px solid #eee">{p['qty']:.6f}</td>
              <td style="padding:6px 12px;border-bottom:1px solid #eee">${p['entry']:,.4f}</td>
              <td style="padding:6px 12px;border-bottom:1px solid #eee">${p['current']:,.4f}</td>
              <td style="padding:6px 12px;border-bottom:1px solid #eee;color:{c};font-weight:bold">
                {p['pnl_pct']:+.2f}%&nbsp;(${p['pnl_usd']:+,.2f})
              </td>
            </tr>"""
        positions_section = f"""
        <h3 style="color:#2c3e50;margin-top:28px">Open Positions</h3>
        <table style="width:100%;border-collapse:collapse;font-size:14px">
          <thead>
            <tr style="background:#f8f9fa">
              <th style="padding:8px 12px;text-align:left">Symbol</th>
              <th style="padding:8px 12px;text-align:left">Qty</th>
              <th style="padding:8px 12px;text-align:left">Entry</th>
              <th style="padding:8px 12px;text-align:left">Current</th>
              <th style="padding:8px 12px;text-align:left">Unrealized P&L</th>
            </tr>
          </thead>
          <tbody>{pos_rows}</tbody>
        </table>"""
    else:
        positions_section = """
        <h3 style="color:#2c3e50;margin-top:28px">Open Positions</h3>
        <p style="color:#7f8c8d;font-size:14px">No open crypto positions — bot is waiting for the next signal.</p>"""

    # ── Trade history table ───────────────────────────────────────────────────
    if perf["trades"]:
        trade_rows = ""
        for t in perf["trades"][:10]:   # cap at 10 most recent
            c = "#27ae60" if t["pnl_pct"] >= 0 else "#e74c3c"
            label = "WIN" if t["pnl_pct"] >= 0 else "LOSS"
            trade_rows += f"""
            <tr>
              <td style="padding:6px 12px;border-bottom:1px solid #eee">{t['symbol']}</td>
              <td style="padding:6px 12px;border-bottom:1px solid #eee">${t['entry']:,.4f}</td>
              <td style="padding:6px 12px;border-bottom:1px solid #eee">${t['exit']:,.4f}</td>
              <td style="padding:6px 12px;border-bottom:1px solid #eee;color:{c};font-weight:bold">
                {t['pnl_pct']:+.2f}%
              </td>
              <td style="padding:6px 12px;border-bottom:1px solid #eee;color:{c}">{label}</td>
            </tr>"""
        trades_section = f"""
        <h3 style="color:#2c3e50;margin-top:28px">Recent Trades (last 7 days)</h3>
        <table style="width:100%;border-collapse:collapse;font-size:14px">
          <thead>
            <tr style="background:#f8f9fa">
              <th style="padding:8px 12px;text-align:left">Symbol</th>
              <th style="padding:8px 12px;text-align:left">Entry</th>
              <th style="padding:8px 12px;text-align:left">Exit</th>
              <th style="padding:8px 12px;text-align:left">P&L %</th>
              <th style="padding:8px 12px;text-align:left">Result</th>
            </tr>
          </thead>
          <tbody>{trade_rows}</tbody>
        </table>"""
    else:
        trades_section = f"""
        <h3 style="color:#2c3e50;margin-top:28px">Trades (last 7 days)</h3>
        <p style="color:#7f8c8d;font-size:14px">{note if note else 'No completed trades yet.'}</p>"""

    return f"""
<!DOCTYPE html>
<html>
<body style="font-family:Arial,sans-serif;background:#f4f6f8;margin:0;padding:20px">
  <div style="max-width:640px;margin:0 auto;background:#fff;border-radius:8px;
              box-shadow:0 2px 8px rgba(0,0,0,0.08);overflow:hidden">

    <!-- Header -->
    <div style="background:#1a1a2e;padding:24px 28px">
      <h1 style="color:#fff;margin:0;font-size:20px">Alpaca Crypto Bot — Daily Report</h1>
      <p style="color:#a0a8c0;margin:4px 0 0;font-size:13px">{now}</p>
    </div>

    <div style="padding:24px 28px">

      <!-- Portfolio snapshot -->
      <h3 style="color:#2c3e50;margin-top:0">Portfolio Snapshot</h3>
      <table style="width:100%;font-size:15px">
        <tr>
          <td style="padding:4px 0;color:#7f8c8d">Portfolio Value</td>
          <td style="padding:4px 0;font-weight:bold">${pv:,.2f}</td>
        </tr>
        <tr>
          <td style="padding:4px 0;color:#7f8c8d">Available Cash</td>
          <td style="padding:4px 0">${cash:,.2f}</td>
        </tr>
        <tr>
          <td style="padding:4px 0;color:#7f8c8d">Open Crypto Positions</td>
          <td style="padding:4px 0">{len(snap['positions'])}</td>
        </tr>
      </table>

      <!-- 7-day P&L -->
      <h3 style="color:#2c3e50;margin-top:28px">7-Day Performance</h3>
      <table style="width:100%;font-size:15px">
        <tr>
          <td style="padding:4px 0;color:#7f8c8d">Total Trades</td>
          <td style="padding:4px 0">{trades} &nbsp;({wins}W / {losses}L)</td>
        </tr>
        <tr>
          <td style="padding:4px 0;color:#7f8c8d">Win Rate</td>
          <td style="padding:4px 0">{wr_str}</td>
        </tr>
        <tr>
          <td style="padding:4px 0;color:#7f8c8d">Net P&L</td>
          <td style="padding:4px 0;color:{net_col};font-weight:bold">${net:+,.2f}</td>
        </tr>
        <tr>
          <td style="padding:4px 0;color:#7f8c8d">Portfolio Return</td>
          <td style="padding:4px 0;color:{ret_col};font-weight:bold">{ret:+.3f}%</td>
        </tr>
      </table>

      {positions_section}
      {trades_section}

    </div>

    <!-- Footer -->
    <div style="background:#f8f9fa;padding:16px 28px;border-top:1px solid #eee">
      <p style="color:#95a5a6;font-size:12px;margin:0">
        Alpaca Paper Trading · BB(48) + RSI(14) + ADX(28) · 10 pairs · TP +2.5% / SL -1.5%<br>
        Bot is running 24/7 on Railway. Ask Claude to check status anytime.
      </p>
    </div>

  </div>
</body>
</html>"""


def send_daily_summary():
    sg_key    = os.environ.get("SENDGRID_API_KEY")
    recipient = os.environ.get("GMAIL_USER")

    if not sg_key:
        logger.warning("Email skipped — SENDGRID_API_KEY not set")
        return
    if not recipient:
        logger.warning("Email skipped — GMAIL_USER not set")
        return

    try:
        import sendgrid
        from sendgrid.helpers.mail import Mail

        api  = get_api()
        snap = _portfolio_snapshot(api)
        perf = get_summary(days=7)
        html = _build_html(snap, perf)

        subject = (
            f"Crypto Bot Report — "
            f"Portfolio ${snap['portfolio_value']:,.0f} · "
            f"{perf['total_trades']} trades · "
            f"P&L ${perf['net_pnl_usd']:+,.2f}"
        )

        message = Mail(
            from_email=recipient,
            to_emails=recipient,
            subject=subject,
            html_content=html,
        )

        sg  = sendgrid.SendGridAPIClient(api_key=sg_key)
        res = sg.send(message)

        if res.status_code in (200, 202):
            logger.info("Daily summary email sent to %s (status %d)", recipient, res.status_code)
        else:
            logger.error("SendGrid returned status %d: %s", res.status_code, res.body)

    except Exception as e:
        logger.error("Failed to send email: %s", e)
