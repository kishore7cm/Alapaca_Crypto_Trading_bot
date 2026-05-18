"""
News sentiment gate for the crypto bot.
Calls Alpaca's News API before every BUY to check for negative headlines
published in the last LOOKBACK_HOURS. If bad news is found, the trade is skipped.
"""
import os
import re
import logging
from datetime import datetime, timedelta, timezone

import requests

logger = logging.getLogger(__name__)

_API_KEY    = os.environ.get("ALPACA_API_KEY",    "PKQXKFDLV6EZHHBVAZB5JWUSP2")
_API_SECRET = os.environ.get("ALPACA_SECRET_KEY", "fed8wyd4WuBC1W6ktZndaTECNZyEAcWWf6pvanC8hF1")
_HEADERS    = {"APCA-API-KEY-ID": _API_KEY, "APCA-API-SECRET-KEY": _API_SECRET}
_NEWS_URL   = "https://data.alpaca.markets/v1beta1/news"

LOOKBACK_HOURS = 4   # check news from last 4 hours

# Words that indicate genuine risk — skip the trade
BAD_KEYWORDS = [
    "hack", "hacked", "breach", "exploit", "stolen", "theft",
    "sec", "lawsuit", "fraud", "scam", "rug pull", "rugpull",
    "ban", "banned", "illegal", "sanction",
    "crash", "collapse", "bankrupt", "insolvent", "shutdown",
    "delist", "delisted", "suspend",
    "investigation", "arrested", "charged",
]

# Symbols Alpaca News uses for each pair (no slash, USD appended)
_ALPACA_NEWS_SYM = {
    "BTC/USD":  "BTCUSD",
    "ETH/USD":  "ETHUSD",
    "XRP/USD":  "XRPUSD",
    "UNI/USD":  "UNIUSD",
    "LINK/USD": "LINKUSD",
    "LTC/USD":  "LTCUSD",
    "AAVE/USD": "AAVEUSD",
    "AVAX/USD": "AVAXUSD",
    "DOT/USD":  "DOTUSD",
    "ADA/USD":  "ADAUSD",
}


def _fetch_news(news_sym: str, hours: int) -> list:
    after = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    try:
        r = requests.get(
            _NEWS_URL,
            headers=_HEADERS,
            params={"symbols": news_sym, "start": after, "limit": 10},
            timeout=6,
        )
        if r.status_code == 200:
            return r.json().get("news", [])
    except Exception as e:
        logger.warning("News API error for %s: %s", news_sym, e)
    return []


def is_safe_to_trade(symbol: str) -> tuple[bool, str]:
    """
    Returns (True, "") if no bad news found — safe to trade.
    Returns (False, headline) if negative news detected — skip trade.
    """
    news_sym = _ALPACA_NEWS_SYM.get(symbol)
    if not news_sym:
        return True, ""

    articles = _fetch_news(news_sym, LOOKBACK_HOURS)
    if not articles:
        return True, ""

    for article in articles:
        headline = article.get("headline", "").lower()
        summary  = article.get("summary",  "").lower()
        text     = headline + " " + summary

        for kw in BAD_KEYWORDS:
            # whole-word match only — avoids "ban" hitting "abandoned", "urban" etc.
            if re.search(r'\b' + re.escape(kw) + r'\b', text):
                short = article.get("headline", "")[:80]
                logger.warning(
                    "NEWS BLOCK %s | keyword='%s' | headline: %s", symbol, kw, short
                )
                return False, short

    logger.debug("News clear for %s (%d articles checked)", symbol, len(articles))
    return True, ""
