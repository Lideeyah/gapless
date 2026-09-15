#!/usr/bin/env python3
"""Self-check for the pre-trade mint state guard. Runs the real keeper over a local order file with
mint_state() replaced by simulated states. Nothing here touches the network except the price read,
which is also replaced. Run: .venv/bin/python keeper/mint_guard_check.py"""
import contextlib, importlib.util, io, json, os, sys, tempfile
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("keeper", os.path.join(ROOT, "keeper", "keeper.py"))
k = importlib.util.module_from_spec(spec); spec.loader.exec_module(k)
MINT = "Xsc9qvGR1efVDFGLrVsmkzv3qi45LTBjeUKSPmx9qEh"
LIVE = {"multiplier": "1.001701196801074", "paused": False, "hook_program": None, "permanent_delegate": "5aMN", "extensions": [18, 12, 6, 25, 26, 4, 14, 19]}

def fresh_order(**over):
    o = {"id": "11111111-1111-4111-8111-111111111111", "owner_pubkey": "9XFDKiaWHRj65fTgtWQWc25MJATACd49NJEo4sVWUfCm", "mint": MINT, "ticker": "NVDAx",
         "token_account": "aRuYpaDWMFVajyprXCFmFfR1g9BvWCPWkXyDMKENnch", "quantity_raw": "100000000", "decimals": 8, "floor_price_usd": "214.50",
         "status": "armed", "breach_count": 0, "last_checked_at": None, "delegation_sig": "x", "fill_sig": None, "fill_price_usd": None, "filled_at": None,
         "failure_reason": None, "created_at": "2026-09-14T00:00:00Z", "order_sig": "y", "delegate": "", "remaining_raw": "100000000", "fills": [],
         "last_decision": None, "last_session": None, "last_price_usd": None, "pending_sig": None, "pending_amount_raw": None, "pending_since": None,
         "revoke_sig": None, "multiplier": LIVE["multiplier"], "rebases": [], "blocked": None}
    o.update(over); return o

def cycle(order, state, price, session="open"):
    """One keeper cycle with simulated mint state and price. Returns the order afterwards and whether a route was requested."""
    path = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False).name
    json.dump({"orders": [order]}, open(path, "w"))
    k.LOCAL_ORDERS, k.FORCE_SESSION, k.DRY_RUN = path, session, True
    routed = {"n": 0}
    if isinstance(state, Exception):
        k.mint_state = lambda m: (_ for _ in ()).throw(state)
    else:
        k.mint_state = lambda m: state
    k.fetch_prices = lambda mints: {m: (Decimal(str(price)), Decimal(1)) for m in mints}
    def no_route(*a, **kw): routed["n"] += 1; raise AssertionError("route requested")
    k.jup_build = no_route
    os.environ["KEEPER_SECRET_KEY"] = ""  # no key: a triggered order stops at 'cannot execute', which is fine for the guard checks
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf): k.run()
    out = json.load(open(path))["orders"][0]; os.unlink(path)
    return out, routed["n"], [l.split(" ", 1)[1] for l in buf.getvalue().splitlines() if " [" in l]

results = []
def check(name, ok, detail): results.append((name, ok, detail)); print(("PASS " if ok else "FAIL ") + name + " | " + detail)

# 3. multiplier change (10-for-1 split) with the price far below the floor: must rebase, reset, record, not fire
split = dict(LIVE, multiplier=str(float(LIVE["multiplier"]) * 10))
o, routed, log = cycle(fresh_order(breach_count=2), split, 21.45, "open")
ratio = Decimal(LIVE["multiplier"]) / Decimal(split["multiplier"])
expect = (Decimal("214.50") * ratio).quantize(Decimal("0.000001"))
check("3 rebase on multiplier change", o["floor_price_usd"] == str(expect) and o["breach_count"] == 0 and len(o["rebases"]) == 1 and o["status"] == "armed" and routed == 0,
      f"floor 214.50 -> {o['floor_price_usd']} (expected {expect}), breach {o['breach_count']}, rebases {len(o['rebases'])}, status {o['status']}, routes {routed} | {log[-1]}")
# 4. next cycle evaluates normally against the rebased floor (price 21.60 is above 21.43 -> hold; 21.40 is below -> breach 1/1 in open -> triggered)
o2, routed2, log2 = cycle(o, split, 21.60, "open")
o3, routed3, log3 = cycle(o, split, 21.40, "open")
check("4 next cycle evaluates against rebased floor", o2["status"] == "armed" and o2["breach_count"] == 0 and o3["status"] == "triggered" and o3["breach_count"] == 1,
      f"at 21.60: {log2[-1]} || at 21.40: {log3[-1]}")
# 5. paused: no route, refusal recorded, even with the price far below the floor
o, routed, log = cycle(fresh_order(), dict(LIVE, paused=True), 100.0, "open")
check("5 paused mint refused", o["blocked"] == "paused" and routed == 0 and o["status"] == "armed" and o["breach_count"] == 0, f"blocked={o['blocked']} routes={routed} | {log[-1]}")
# 6. transfer hook enabled: no swap attempt, recorded
o, routed, log = cycle(fresh_order(), dict(LIVE, hook_program="HookProgram1111111111111111111111111111111"), 100.0, "open")
check("6 transfer hook refused", o["blocked"] == "transfer_hook" and routed == 0 and o["status"] == "armed", f"blocked={o['blocked']} routes={routed} | {log[-1]}")
# 7. mint state unreadable: nothing evaluated, breach count untouched, no route
o, routed, log = cycle(fresh_order(breach_count=1), RuntimeError("rpc getAccountInfo: simulated outage"), 100.0, "open")
check("7 unreadable mint state does not fire", o["blocked"] == "mint_unreadable" and routed == 0 and o["status"] == "armed" and o["breach_count"] == 1, f"blocked={o['blocked']} breach={o['breach_count']} routes={routed} | {log[-1]}")
# order of checks: a triggered order from a previous cycle is still blocked by a pause before any route
o, routed, log = cycle(fresh_order(status="triggered", breach_count=1), dict(LIVE, paused=True), 100.0, "open")
check("guard runs before execution for already-triggered orders", routed == 0 and o["blocked"] == "paused", f"status={o['status']} routes={routed} | {log[-1]}")
# unchanged multiplier and clean mint: normal evaluation proceeds (control)
o, routed, log = cycle(fresh_order(), LIVE, 215.0, "open")
check("control: clean mint evaluates normally", o["blocked"] is None and o["last_decision"].startswith("hold"), log[-1])
print("\n%d/%d passed" % (sum(1 for r in results if r[1]), len(results)))
sys.exit(0 if all(r[1] for r in results) else 1)
