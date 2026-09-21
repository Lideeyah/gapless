#!/usr/bin/env python3
"""Gapless recorder: append one live price row per tracked xStock to data/prices.csv.

Stdlib only. Never fails the caller: a bad fetch writes an empty price with source=error.
"""
import csv
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# Mint addresses verified on Solscan (Token-2022, 8 decimals) and via on-chain metadata.
TICKERS = {
    "NVDAx": "Xsc9qvGR1efVDFGLrVsmkzv3qi45LTBjeUKSPmx9qEh",
    "TSLAx": "XsDoVfqeBukxuZHWhdvWHBhgEHjGNst4MLodqsJHzoB",
    "SPYx": "XsoCS1TfEyfFhfvj8EtZ528L3CaKBDBRqRapnBbDF2W",
}
# PreStocks tokenised pre-IPO shares (Token-2022, 9 decimals, mints from https://prestocks.com/api/prestocks,
# verified on chain). A pre-IPO share has no public market, so these rows carry the session label "closed"
# instead of a NYSE session: every hour is a closed hour.
NO_MARKET = {
    "ANDURIL": "PresTj4Yc2bAR197Er7wz4UUKSfqt6FryBEdAriBoQB",
    "ANTHROPIC": "Pren1FvFX6J3E4kXhJuCiAD5aDmGEb7qJRncwA8Lkhw",
    "FIGUREAI": "PreZad18qfPtbxNpMtMuAuX2zVpvkEU8DnJx56faCWd",
    "KALSHI": "PreLWGkkeqG1s4HEfFZSy9moCrJ7btsHuUtfcCeoRua",
    "NEURALINK": "PrekqLJvJ3qVdXmBGDiexvwUTF4rLFDa6HWS4HJbw9S",
    "OPENAI": "PreweJYECqtQwBtpxHL171nL2K6umo692gTm7Q3rpgF",
    "POLYMARKET": "Pre8AREmFPtoJFT8mQSXQLh56cwJmM7CFDRuoGBZiUP",
    "SPACEX": "PreANxuXjsy2pvisWWMNB6YaJNzr7681wJJr2rHsfTh",
}
ALL = {**TICKERS, **NO_MARKET}
# Jupiter Price API v3, keyless access (0.5 RPS, no sign-up) on the main host. Response: {mint: {"usdPrice": float, ...}, ...}
PRICE_URL = "https://api.jup.ag/price/v3?ids="
SOURCE = "jupiter-price-v3"
HEADER = ["timestamp_utc", "ticker", "mint", "price_usd", "session", "source"]
CSV_PATH = os.environ.get(
    "GAPLESS_CSV",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "prices.csv"),
)
EASTERN = ZoneInfo("America/New_York")


def session_state(now_utc):
    """open: Mon-Fri 09:30-16:00 ET. overnight: any other Mon-Fri time. weekend: Sat/Sun."""
    et = now_utc.astimezone(EASTERN)
    if et.weekday() >= 5:
        return "weekend"
    minute_of_day = et.hour * 60 + et.minute
    if 9 * 60 + 30 <= minute_of_day < 16 * 60:
        return "open"
    return "overnight"


def fetch_prices(mints):
    """Return {mint: usdPrice}. Mints Jupiter has no reliable price for are omitted."""
    req = urllib.request.Request(PRICE_URL + ",".join(mints), headers={"User-Agent": "gapless-recorder"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.load(resp)
    return {m: float(data[m]["usdPrice"]) for m in mints if m in data and data[m].get("usdPrice") is not None}  # a null price is a missing price for that mint only


def main():
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    session = session_state(now)
    try:
        prices = fetch_prices(list(ALL.values()))
    except Exception as exc:  # noqa: BLE001 - any failure must still produce rows and exit 0
        print(f"price fetch failed: {exc!r}", file=sys.stderr)
        prices = {}

    os.makedirs(os.path.dirname(CSV_PATH), exist_ok=True)
    write_header = not os.path.exists(CSV_PATH) or os.path.getsize(CSV_PATH) == 0
    with open(CSV_PATH, "a", newline="") as f:  # append only; existing rows are never touched
        writer = csv.writer(f)
        if write_header:
            writer.writerow(HEADER)
        for ticker, mint in ALL.items():
            price = prices.get(mint)
            if price is None:
                print(f"no price for {ticker}", file=sys.stderr)
            row = [timestamp, ticker, mint, "" if price is None else repr(price), "closed" if ticker in NO_MARKET else session,
                   SOURCE if price is not None else "error"]
            writer.writerow(row)
            print(",".join(row))


if __name__ == "__main__":
    main()
