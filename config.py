import os

ALPACA_API_KEY    = os.getenv("ALPACA_API_KEY",    "PKQXKFDLV6EZHHBVAZB5JWUSP2")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "fed8wyd4WuBC1W6ktZndaTECNZyEAcWWf6pvanC8hF1")
ALPACA_BASE_URL   = os.getenv("ALPACA_BASE_URL",   "https://paper-api.alpaca.markets")

# ── Stock bot ─────────────────────────────────────────────────────────────────
STOCK_SYMBOLS   = ["SPY", "QQQ", "AAPL", "MSFT"]
STOCK_BB_PERIOD = 20
STOCK_BB_STD    = 2.0
STOCK_RISK_PCT  = 0.05
STOCK_STOP_LOSS = 0.02

# ── Crypto pairs ──────────────────────────────────────────────────────────────
# Backtest-validated (30-day, 720 hourly bars).
# Removed: ARB/USD (51.6% trending — worst mean-reversion candidate)
#          SOL/USD (25% win rate under both strategies)
# Added:   DOT/USD, ADA/USD (similar signal freq, untested but diversifying)
CRYPTO_SYMBOLS = [
    "BTC/USD",    # 67% win rate — anchor
    "ETH/USD",    # 80% win rate — best performer
    "XRP/USD",    # 60% win rate — liquid payments
    "UNI/USD",    # 67% win rate — DeFi DEX
    "LINK/USD",   # 50% win rate — oracle
    "LTC/USD",    # 50% win rate — digital silver
    "AAVE/USD",   # 25% baseline → ADX filter expected to improve
    "AVAX/USD",   # 33% baseline → ADX filter expected to improve
    "DOT/USD",    # replacing ARB — less trending
    "ADA/USD",    # replacing SOL — less trending
]

# ── Crypto strategy ───────────────────────────────────────────────────────────
CRYPTO_BB_PERIOD      = 48      # 2 days of hourly bars
CRYPTO_BB_STD         = 2.0
CRYPTO_RSI_PERIOD     = 14
CRYPTO_RSI_OVERSOLD   = 38      # entry: RSI below this to BUY
CRYPTO_RSI_OVERBOUGHT = 62      # entry: RSI above this to note SELL signal

# ADX regime filter — the single biggest win-rate improvement.
# ADX < threshold = ranging market = mean reversion works.
# ADX >= threshold = trending market = skip signal.
# Threshold 28 chosen to retain ~55-60% of hours (backtest: most pairs range 50-65%).
CRYPTO_ADX_PERIOD    = 14
CRYPTO_ADX_THRESHOLD = 28

# BB Width guard: skip if bands are too tight (no room to profit)
# or too wide (trending hard). Width = (Upper - Lower) / MA.
CRYPTO_BB_WIDTH_MIN  = 0.004   # below this = bands too tight, skip
CRYPTO_BB_WIDTH_MAX  = 0.10    # above this = extreme volatility, skip

# ── Crypto risk / money management ───────────────────────────────────────────
CRYPTO_RISK_PCT       = 0.10   # 10% of portfolio per trade (~$12k on $120k)
CRYPTO_TAKE_PROFIT    = 0.025  # 2.5% TP — bracket order limit leg
CRYPTO_STOP_LOSS      = 0.015  # 1.5% SL — bracket order stop leg (R:R = 1.67)
CRYPTO_MAX_DAILY_LOSS = 0.02   # halt if portfolio down > 2% from day-open value
MAX_CRYPTO_POSITIONS  = 4      # max concurrent crypto positions
MAX_POSITIONS         = 20     # global cap (account already holds 13 stock positions)
