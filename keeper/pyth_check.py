#!/usr/bin/env python3
"""Pyth integration checks. Part 1 talks to the real Hermes (needs PYTH_API_KEY in the environment for the price
checks; the feed list and market flag are keyless). Part 2 runs the real keeper over local order files with
external calls replaced by controlled fakes, one condition at a time, and prints what it did. Nothing sends.
Run: set -a; . ./.env.local; set +a; .venv/bin/python keeper/pyth_check.py"""
import contextlib, importlib.util, io, json, os, sys, tempfile, time, urllib.request
from datetime import datetime, timezone
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "keeper"))
import pyth  # noqa: E402
import market_hours as mh  # noqa: E402
spec = importlib.util.spec_from_file_location("keeper", os.path.join(ROOT, "keeper", "keeper.py"))
k = importlib.util.module_from_spec(spec); spec.loader.exec_module(k)
from solders.keypair import Keypair  # noqa: E402
KP = Keypair()
results = []

def check(name, ok, detail):
    results.append((name, ok)); print(("PASS " if ok else "FAIL ") + name + "\n      " + str(detail).replace("\n", "\n      "))

print("=== 1. FEED IDS AGAINST PYTH'S PUBLISHED LIST (keyless) ===")
published = {}
for asset in ("equity", "crypto"):
    for q in ("NVDA", "TSLA", "SPY"):
        req = urllib.request.Request(f"{pyth.HERMES}/v2/price_feeds?query={q}&asset_type={asset}", headers=pyth.UA)
        for f in json.load(urllib.request.urlopen(req, timeout=20)):
            published[f["attributes"]["symbol"]] = f["id"]
for ticker, sides in pyth.FEEDS.items():
    for side, (sym, fid) in sides.items():
        check(f"1 {sym} id matches the published list", published.get(sym) == fid, f"ours {fid[:16]}… published {str(published.get(sym))[:16]}…")

print("=== 2. READ PATH, ENTITLEMENT, CADENCE (needs PYTH_API_KEY) ===")
ids = [fid for t in pyth.FEEDS.values() for _, fid in t.values()]
r1 = pyth.fetch_latest(ids); time.sleep(2); r2 = pyth.fetch_latest(ids)
by_status = {}
for tk, sides in pyth.FEEDS.items():
    for side, (sym, fid) in sides.items():
        by_status.setdefault(r1[fid]["status"], []).append(sym)
check("2a every feed has a status and none raised", all(r1[i]["status"] in ("ok", "not_entitled", "no_key", "key_rejected", "unreadable") for i in ids), json.dumps(by_status, indent=1))
ok_ids = [i for i in ids if r1[i]["status"] == "ok"]
if ok_ids:
    i = ok_ids[0]; a, b = r1[i], r2[i]
    check("2b entitled feed publishes continuously (two reads 2s apart carry different publish times)", b["publish_time"] > a["publish_time"],
          f"publish {pyth.iso(a['publish_time'])} -> {pyth.iso(b['publish_time'])}, price {a['price']:.4f} -> {b['price']:.4f}, conf {a['conf']:.4f} ({a['conf']/a['price']*100:.4f}% of price), age {int(time.time())-b['publish_time']}s")
else:
    check("2b entitled feed publishes continuously", False, "no entitled feed readable; is PYTH_API_KEY set?")

print("=== 3. MARKET-HOURS FLAG AND SCHEDULE (keyless) ===")
flag = pyth.market_hours()
check("3a flag readable with a schedule string", isinstance(flag["is_open"], bool) and flag["schedule"].startswith("America/New_York;"), f"is_open={flag['is_open']} next_open={pyth.iso(flag['next_open'])} next_close={pyth.iso(flag['next_close'])}\nschedule {flag['schedule']}")
weekly, overrides = mh.parse_schedule(flag["schedule"])
from market_calendar import HOLIDAYS, EARLY_CLOSES  # noqa: E402
listed = {d for d in HOLIDAYS if d.strftime("%m%d") in overrides and overrides[d.strftime("%m%d")] == "C"}
missing = sorted(d for d in HOLIDAYS if d.strftime("%m%d") not in overrides)
check("3b Pyth's schedule carries the hand calendar's holidays for the coming year", len(listed) >= 10,
      f"weekly {weekly}; hand-calendar holidays present as C: {len(listed)}; hand-calendar dates not in Pyth's 12-month window: {[str(d) for d in missing]}\nearly closes in Pyth: {[(d, h) for d, h in overrides.items() if h not in ('C',)]} vs hand {[str(d) for d in EARLY_CLOSES]}")
now = datetime.now(timezone.utc)
closed = {**flag, "is_open": False}
cases = [("weekday holiday 2026-11-26 15:00Z labelled open", datetime(2026, 11, 26, 15, tzinfo=timezone.utc), "open", closed, "weekend"),
         ("half-day afternoon 2026-11-27 18:30Z labelled open", datetime(2026, 11, 27, 18, 30, tzinfo=timezone.utc), "open", closed, "overnight"),
         ("ordinary weekday 15:00Z, flag open", datetime(2026, 9, 21, 15, tzinfo=timezone.utc), "open", {**flag, "is_open": True}, "open"),
         ("saturday", datetime(2026, 9, 19, 15, tzinfo=timezone.utc), "weekend", closed, "weekend"),
         ("flag says open while the recorder labels overnight: label wins (never loosen)", datetime(2026, 9, 21, 2, tzinfo=timezone.utc), "overnight", {**flag, "is_open": True}, "overnight")]
for name, at, label, f, want in cases:
    got, src, note = mh.execution_session_live(at, label, lambda f=f: f)
    check(f"3c {name} -> {want}", got == want and src == "pyth", f"got {got} ({src}: {note})")
got, src, note = mh.execution_session_live(datetime(2026, 11, 26, 15, tzinfo=timezone.utc), "open", lambda: (_ for _ in ()).throw(RuntimeError("hermes down")))
check("3d flag unreadable on a holiday -> hand calendar decides weekend", got == "weekend" and src == "calendar", note)

print("=== 4. KEEPER CHECK AGAINST THE REAL FEED (needs an entitled feed) ===")
def order(**over):
    o = {"id": "11111111-1111-4111-8111-111111111111", "owner_pubkey": "9XFDKiaWHRj65fTgtWQWc25MJATACd49NJEo4sVWUfCm", "mint": "XsDoVfqeBukxuZHWhdvWHBhgEHjGNst4MLodqsJHzoB", "ticker": "TSLAx",
         "token_account": "aRuYpaDWMFVajyprXCFmFfR1g9BvWCPWkXyDMKENnch", "quantity_raw": "100000000", "decimals": 8, "floor_price_usd": "300", "status": "triggered", "breach_count": 3,
         "last_checked_at": None, "delegation_sig": "x", "fill_sig": None, "fill_price_usd": None, "filled_at": None, "failure_reason": None, "created_at": "2026-09-18T00:00:00Z",
         "order_sig": "y", "delegate": str(KP.pubkey()), "remaining_raw": "100000000", "fills": [], "last_decision": None, "last_session": None, "last_price_usd": None,
         "pending_sig": None, "pending_amount_raw": None, "pending_since": None, "revoke_sig": None, "multiplier": "1", "rebases": [], "blocked": None, "multiplier_event": None}
    o.update(over); return o
live = pyth.fetch_latest([pyth.FEEDS["TSLAx"]["equity"][1]])[pyth.FEEDS["TSLAx"]["equity"][1]]
if live["status"] == "ok":
    p = Decimal(repr(live["price"])); fresh = int(time.time()) - live["publish_time"] <= k.PYTH_CFG["max_age_s"]
    o = order(); d = k.pyth_check(o, p * Decimal("1.02"), "open")
    check("4a forced 2% divergence against the live TSLA feed -> refused, both values recorded", d is not None and o["blocked"] == "pyth_divergence" and o["pyth_check"]["status"] == "diverged" and o["pyth_check"]["divergence_bps"] >= 190, f"{d}\n{json.dumps(o['pyth_check'])}")
    o = order(); d = k.pyth_check(o, p, "open")
    check("4b execution price equal to the live feed -> not refused, agreement recorded", d is None and o["blocked"] is None and o["pyth_check"]["status"] == "agree" and o["pyth_check"]["divergence_bps"] == 0, json.dumps(o["pyth_check"]))
    check("4c the live equity print is fresh right now (extended hours)", fresh, f"age {int(time.time())-live['publish_time']}s, max {k.PYTH_CFG['max_age_s']}s, NYSE flag is_open={flag['is_open']}")
else:
    check("4 live TSLA feed readable", False, live)

print("=== 5. KEEPER CHECK UNDER CONTROLLED FEEDS ===")
def with_feed(reading, session="overnight", price=Decimal("300"), o=None):
    o = o or order(); k.pyth.fetch_latest = lambda ids, key=None: {i: dict(reading) for i in ids}
    d = k.pyth_check(o, price, session); return o, d
now_ts = int(time.time())
o, d = with_feed({"status": "ok", "price": 300.0, "conf": 0.05, "publish_time": now_ts, "expo": -5})
check("5a fresh, within band -> proceeds", d is None and o["pyth_check"]["status"] == "agree", o["pyth_check"])
o, d = with_feed({"status": "ok", "price": 303.5, "conf": 0.05, "publish_time": now_ts, "expo": -5})
check("5b fresh, 116 bps apart -> refused, both values and the divergence recorded", d and o["blocked"] == "pyth_divergence" and o["pyth_check"]["divergence_bps"] == 116 and o["pyth_check"]["pyth_price_usd"] == "303.5" and o["pyth_check"]["exec_price_usd"] == "300", d)
o, d = with_feed({"status": "ok", "price": 300.0, "conf": 0.05, "publish_time": now_ts - 600, "expo": -5}, session="open")
check("5c stale (600s) while the market is open -> refused as unknown, not agreement", d and o["blocked"] == "pyth_unavailable" and o["pyth_check"]["status"] == "stale", d)
eq_id = pyth.FEEDS["TSLAx"]["equity"][1]
k.pyth.fetch_latest = lambda ids, key=None: {i: ({"status": "ok", "price": 300.0, "conf": 0.05, "publish_time": now_ts - 36000, "expo": -5} if i == eq_id else {"status": "not_entitled", "error": "x"}) for i in ids}
o = order(); d = k.pyth_check(o, Decimal("300"), "weekend")
check("5d only the equity feed entitled, its print frozen 10h while NYSE is shut -> market_closed, proceeds (the thesis, not a fault)", d is None and o["pyth_check"]["status"] == "market_closed" and o["blocked"] is None, o["pyth_check"])
o = order(); d = k.pyth_check(o, Decimal("300"), "open")
check("5d' same frozen equity print while the session is open -> refused as stale", d and o["blocked"] == "pyth_unavailable" and o["pyth_check"]["status"] == "stale", d)
o = order(); k.pyth.fetch_latest = lambda ids, key=None: {i: ({"status": "ok", "price": 300.0, "conf": 0.05, "publish_time": now_ts, "expo": -5} if i == eq_id else {"status": "not_entitled", "error": "x"}) for i in ids}
d = k.pyth_check(o, Decimal("300"), "weekend")
check("5d'' equity feed fresh outside NYSE hours (extended hours) -> compared, agree", d is None and o["pyth_check"]["status"] == "agree" and o["pyth_check"]["reference"] == "Equity.US.TSLA/USD", o["pyth_check"])
o, d = with_feed({"status": "unreadable", "error": "timeout"})
check("5e feed unreadable (transport) -> refused", d and o["blocked"] == "pyth_unavailable" and o["pyth_check"]["status"] == "unreadable", d)
for st in ("not_entitled", "no_key", "key_rejected"):
    o, d = with_feed({"status": st, "error": st})
    check(f"5f access absent ({st}) -> proceeds, recorded, never blocks", d is None and o["blocked"] is None and o["pyth_check"]["status"] == "not_entitled", o["pyth_check"])
o, d = with_feed({"status": "ok", "price": 300.0, "conf": 0.05, "publish_time": now_ts, "expo": -5}, o=order(ticker="FOOx"))
check("5g asset with no Pyth feed at all -> proceeds, recorded no_feed", d is None and o["pyth_check"]["status"] == "no_feed", o["pyth_check"])

print("=== 6. PYTH CANNOT CAUSE A TRADE ===")
src = open(os.path.join(ROOT, "keeper", "keeper.py")).read()
head, tail = src.split("def pyth_check(", 1); body = tail.split("\ndef ", 1)[0]
check("6a pyth_check writes no status, breach count, floor or amount; it only sets `blocked` and `pyth_check`",
      all(s not in body for s in ('o["status"]', 'o["breach_count"]', 'o["floor_price_usd"]', 'remaining_raw', 'quantity_raw')) and 'o["blocked"]' in body, "fields written: blocked, pyth_check")
check("6b the only call site is in the execution step, after the mint guard's `blocked` check and before token_account_state",
      src.count("refused = pyth_check(o,") == 1 and src.count("pyth_check(o,") == 2  # the def and one call
      and (lambda s4: s4.index('if o.get("blocked"):') < s4.index("refused = pyth_check(o,") < s4.index("token_account_state(o["))(src.split("# 4. execute", 1)[1]), "one call site, in step 4, between the blocked check and the token-account read")
check("6c the evaluation step (breach counting, triggering) never reads Pyth", "pyth" not in src.split("# 3. evaluate", 1)[1].split("# 4. execute", 1)[0], "no pyth reference between step 3 and step 4")
check("6d market-hours flag can only tighten the session", "max((labelled, live), key=STRICTNESS.get)" in open(os.path.join(ROOT, "keeper", "market_hours.py")).read(), "strictest of label and live reading")

print("=== 7. MAIN UNTOUCHED ===")
import subprocess  # noqa: E402
tag = subprocess.run(["git", "rev-parse", "known-good-weekend"], capture_output=True, text=True, cwd=ROOT).stdout.strip()
human = subprocess.run(["git", "log", "--format=%s", "known-good-weekend..origin/main"], capture_output=True, text=True, cwd=ROOT).stdout.splitlines()
non_bot = [h for h in human if not (h.startswith("record ") or h.startswith("keeper ") or h.startswith("order "))]
check("7 tag known-good-weekend at a20c79e; main since then holds only recorder, keeper and order-store commits", tag.startswith("a20c79e") and not non_bot, f"tag {tag[:7]}; {len(human)} commits on main since the tag, {len(non_bot)} by hand")

print(f"\n{sum(ok for _, ok in results)}/{len(results)} passed")
sys.exit(0 if all(ok for _, ok in results) else 1)
