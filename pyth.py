#!/usr/bin/env python3
"""Pyth as a second, independent price witness. Stdlib only. Shared by the recorder and the keeper.

Read path: Hermes (off-chain, HTTP). The on-chain pull-oracle accounts for these feeds are not populated on
Solana mainnet and the legacy push accounts were retired in 2024, so Hermes is the only live read. Price
updates need an API key since the Core upgrade of 2026-08-26 (`Authorization: Bearer $PYTH_API_KEY`); the
feed list with its `market_hours` flag and trading schedule stays keyless.

Entitlement is per feed, not per key. A feed the key does not cover answers 403 "Not entitled"; that is
reported as status `not_entitled`, distinct from `unreadable` (transport or server failure). The caller
decides what each status means. Nothing here ever raises past `fetch_latest`.
"""
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

HERMES = os.environ.get("PYTH_HERMES", "https://hermes.pyth.network")
KEY = os.environ.get("PYTH_API_KEY", "").strip()
UA = {"User-Agent": "gapless-recorder"}  # Cloudflare in front of Hermes rejects the urllib default agent

# Feed ids verified against GET /v2/price_feeds (keyless) on 2026-09-18; see keeper/pyth_check.py check 1.
FEEDS = {
    "NVDAx": {"equity": ("Equity.US.NVDA/USD", "b1073854ed24cbc755dc527418f52b7d271f6cc967bbf8d8129112b18860a593"),
              "xstock": ("Crypto.NVDAX/USD", "4244d07890e4610f46bbde67de8f43a4bf8b569eebe904f136b469f148503b7f")},
    "TSLAx": {"equity": ("Equity.US.TSLA/USD", "16dad506d7db8da01c87581c87ca897a012a153557d4d578c3b9c9e1bc0632f1"),
              "xstock": ("Crypto.TSLAX/USD", "47a156470288850a440df3a6ce85a55917b813a19bb5b31128a33a986566a362")},
    "SPYx": {"equity": ("Equity.US.SPY/USD", "19e09bb805456ada3979a7d1cbb4b6d63babc3a0f8e8a9509f68afa5c4c11cd5"),
             "xstock": ("Crypto.SPYX/USD", "2817b78438c769357182c04346fddaad1178c82f4048828fe0997c3c64624e14")},
}
NYSE_REFERENCE = "Equity.US.SPY/USD"  # the keyless market_hours flag of one NYSE-listed feed stands for the exchange


def _get(url, key=None, timeout=15):
    req = urllib.request.Request(url, headers={**UA, **({"Authorization": f"Bearer {key}"} if key else {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def fetch_latest(feed_ids, key=KEY):
    """{feed_id: reading}. reading = {"status": "ok", "price": float, "conf": float, "publish_time": int, "expo": int}
    or {"status": "not_entitled" | "no_key" | "key_rejected" | "unreadable", "error": str}. The first three mean
    access is absent (no grant, no key, expired key); only `unreadable` means access exists and the read failed. One batched call; a 403 names the
    feed it refuses, which is dropped and the rest retried, so one missing grant never hides the others."""
    ids = list(dict.fromkeys(feed_ids))
    out = {}
    if not key:
        return {i: {"status": "no_key", "error": "PYTH_API_KEY is not set"} for i in ids}
    for _ in range(len(ids) + 1):
        want = [i for i in ids if i not in out]
        if not want:
            break
        q = "&".join(f"ids[]={i}" for i in want) + "&parsed=true"
        try:
            data = _get(f"{HERMES}/v2/updates/price/latest?{q}", key)
        except urllib.error.HTTPError as exc:
            body = exc.read()[:300].decode(errors="replace")
            m = re.search(r"Not entitled: feed ([0-9a-f]{64})", body)
            if exc.code == 403 and m and m.group(1) in want:
                out[m.group(1)] = {"status": "not_entitled", "error": body.strip()}
                continue
            for i in want:  # 401 (key rejected) or anything else: nothing readable this call
                out[i] = {"status": {401: "key_rejected", 403: "not_entitled"}.get(exc.code, "unreadable"), "error": f"HTTP {exc.code}: {body.strip()}"}
            break
        except Exception as exc:  # noqa: BLE001
            for i in want:
                out[i] = {"status": "unreadable", "error": repr(exc)}
            break
        for p in data.get("parsed", []):
            pr = p["price"]
            out[p["id"]] = {"status": "ok", "price": int(pr["price"]) * 10 ** pr["expo"], "conf": int(pr["conf"]) * 10 ** pr["expo"],
                            "expo": pr["expo"], "publish_time": int(pr["publish_time"])}
        for i in want:
            out.setdefault(i, {"status": "unreadable", "error": "feed absent from a 200 response"})
    return out


def market_hours(symbol=NYSE_REFERENCE):
    """Keyless. {"is_open": bool, "next_open": int, "next_close": int, "schedule": str} for one feed, or raises."""
    q = urllib.parse.urlencode({"query": symbol.split(".")[-1].split("/")[0], "asset_type": symbol.split(".")[0].lower()})
    for f in _get(f"{HERMES}/v2/price_feeds?{q}", timeout=15):
        if f.get("attributes", {}).get("symbol") == symbol:
            mh = f.get("market_hours") or {}
            return {"is_open": bool(mh.get("is_open")), "next_open": mh.get("next_open"), "next_close": mh.get("next_close"),
                    "schedule": f["attributes"].get("schedule", "")}
    raise LookupError(f"{symbol} not in the Pyth feed list")


def iso(ts):
    return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if ts else ""


PYTH_HEADER = ["timestamp_utc", "ticker", "jupiter_price_usd", "market_open",
               "equity_feed", "equity_price", "equity_conf", "equity_publish_utc", "equity_status",
               "xstock_feed", "xstock_price", "xstock_conf", "xstock_publish_utc", "xstock_status"]


def pyth_rows(timestamp, jupiter_by_ticker, key=KEY):
    """One row per tracked ticker for data/pyth.csv: the Jupiter price the primary series recorded at the same
    stamp, the keyless market flag, and both Pyth feeds with status. Never raises."""
    ids = [fid for t in FEEDS.values() for _, fid in t.values()]
    readings = fetch_latest(ids, key)
    try:
        market_open = "1" if market_hours()["is_open"] else "0"
    except Exception:  # noqa: BLE001
        market_open = ""
    rows = []
    for ticker, sides in FEEDS.items():
        jp = jupiter_by_ticker.get(ticker)
        row = [timestamp, ticker, "" if jp is None else repr(jp), market_open]
        for side in ("equity", "xstock"):
            sym, fid = sides[side]
            r = readings.get(fid, {"status": "unreadable"})
            ok = r["status"] == "ok"
            row += [sym, repr(r["price"]) if ok else "", repr(r["conf"]) if ok else "", iso(r["publish_time"]) if ok else "", r["status"]]
        rows.append(row)
    return rows
