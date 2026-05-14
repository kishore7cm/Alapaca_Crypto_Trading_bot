"""
HTTP control layer for the crypto trading bot.
Claude (or any HTTP client) calls these endpoints to inspect and control the bot.

Endpoints:
  GET  /status       — portfolio snapshot + bot state
  GET  /positions    — open crypto positions
  GET  /signals      — run a scan NOW and return signals (no orders placed)
  POST /run          — trigger a full trading cycle immediately
  POST /pause        — pause scheduled trading (open positions/bracket orders unaffected)
  POST /resume       — resume scheduled trading
  GET  /logs?n=100   — last N lines from crypto_bot.log
  GET  /performance  — 7-day P&L summary

Auth: pass  X-API-Key: <BOT_API_KEY>  header on every request.
Set BOT_API_KEY as an env var before starting the server.
"""
import os
import threading
import subprocess
from datetime import datetime

from fastapi import FastAPI, Depends, HTTPException, Header
from fastapi.responses import PlainTextResponse

app = FastAPI(title="Crypto Bot Control API", version="1.0")

BOT_API_KEY: str = os.environ.get("BOT_API_KEY", "change-me-before-deploy")

# Shared state — mutated by /pause and /resume
_paused = threading.Event()  # set → paused, clear → running


# ── Auth ──────────────────────────────────────────────────────────────────────

def _auth(x_api_key: str = Header(..., alias="X-API-Key")):
    if x_api_key != BOT_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/status")
def status(_=Depends(_auth)):
    from crypto_data import get_api
    from crypto_trader import crypto_position_count

    api = get_api()
    acc = api.get_account()
    return {
        "timestamp":        datetime.utcnow().isoformat() + "Z",
        "bot_paused":       _paused.is_set(),
        "portfolio_value":  round(float(acc.portfolio_value), 2),
        "cash":             round(float(acc.cash), 2),
        "buying_power":     round(float(acc.buying_power), 2),
        "crypto_positions": crypto_position_count(api),
    }


@app.get("/positions")
def positions(_=Depends(_auth)):
    from crypto_data import get_api
    from crypto_trader import _CRYPTO_SYMS

    api  = get_api()
    rows = []
    for p in api.list_positions():
        if p.symbol not in _CRYPTO_SYMS:
            continue
        rows.append({
            "symbol":        p.symbol,
            "qty":           float(p.qty),
            "entry_price":   round(float(p.avg_entry_price), 4),
            "current_price": round(float(p.current_price), 4),
            "market_value":  round(float(p.market_value), 2),
            "unrealized_pl": round(float(p.unrealized_pl), 2),
            "unrealized_plpc": f"{float(p.unrealized_plpc)*100:.2f}%",
        })
    return {"positions": rows, "count": len(rows)}


@app.get("/signals")
def signals(_=Depends(_auth)):
    """Dry-run scan — computes signals without placing any orders."""
    from crypto_data import get_api, fetch_crypto_bars
    from crypto_strategy import scan

    api     = get_api()
    results = scan(api, fetch_crypto_bars)
    output  = {}
    for sym, (sig, ctx) in results.items():
        output[sym] = {
            "signal": sig,
            "rsi":    ctx.get("rsi"),
            "adx":    ctx.get("adx"),
            "bbw":    ctx.get("bbw"),
            "close":  ctx.get("close"),
            "gates":  ctx.get("gates", {}),
        }
    return output


@app.post("/run")
def run_now(_=Depends(_auth)):
    """Trigger one full trading cycle in a background thread."""
    if _paused.is_set():
        return {"message": "Bot is paused — use /resume first"}
    from crypto_main import run_cycle
    threading.Thread(target=run_cycle, daemon=True).start()
    return {"message": "Cycle triggered", "timestamp": datetime.utcnow().isoformat() + "Z"}


@app.post("/pause")
def pause(_=Depends(_auth)):
    _paused.set()
    return {"message": "Bot paused — scheduled cycles will be skipped until /resume"}


@app.post("/resume")
def resume(_=Depends(_auth)):
    _paused.clear()
    return {"message": "Bot resumed — next cycle will run on schedule"}


@app.get("/logs", response_class=PlainTextResponse)
def logs(n: int = 100, _=Depends(_auth)):
    try:
        result = subprocess.run(
            ["tail", f"-{n}", "crypto_bot.log"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout or "(log file is empty)"
    except Exception as e:
        return f"Error reading log: {e}"


@app.get("/performance")
def performance(days: int = 7, _=Depends(_auth)):
    import io
    import sys
    from performance import report as _report

    buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf
    try:
        _report(days=days)
    finally:
        sys.stdout = old_stdout
    return PlainTextResponse(buf.getvalue())
