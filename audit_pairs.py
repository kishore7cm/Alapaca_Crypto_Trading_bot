"""
Run: python3 audit_pairs.py
Shows live signal-frequency and volatility for all Alpaca-tradeable USD crypto pairs.
Use to decide which symbols to keep in CRYPTO_SYMBOLS in config.py.
"""
import pandas as pd
from crypto_data import get_api, fetch_crypto_bars
from crypto_strategy import add_indicators

api = get_api()

assets = api.list_assets(asset_class="crypto")
usd_pairs = sorted(
    [a.symbol for a in assets if a.tradable and a.status == "active" and a.symbol.endswith("/USD")],
)
print(f"Scanning {len(usd_pairs)} USD pairs...\n")

rows = []
for sym in usd_pairs:
    try:
        df = fetch_crypto_bars(api, sym, hours=240)
        if len(df) < 50:
            continue
        df = add_indicators(df)
        last = df.iloc[-1]
        df["hl_pct"] = (df["High"] - df["Low"]) / df["Close"] * 100
        bb_touches = int(((df["Close"] < df["Lower"]) | (df["Close"] > df["Upper"])).sum())
        hourly_vol = round(df["hl_pct"].mean(), 2)
        rows.append({
            "symbol":       sym,
            "close":        round(float(last["Close"]), 4),
            "rsi":          round(float(last["RSI"]), 1),
            "bb_touches":   bb_touches,
            "hourly_vol%":  hourly_vol,
            "score":        round(bb_touches * 1.0 + hourly_vol * 10, 1),
        })
    except Exception as e:
        print(f"  skip {sym}: {e}")

df_out = pd.DataFrame(rows).sort_values("score", ascending=False)
print(df_out.to_string(index=False))
