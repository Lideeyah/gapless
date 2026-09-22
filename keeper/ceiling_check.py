#!/usr/bin/env python3
"""Take-profit (ceiling) checks. Runs the real keeper over local order files with external calls replaced by controlled
fakes, the same technique as audit_check.py, one condition at a time. Nothing here sends anything.
Run: .venv/bin/python keeper/ceiling_check.py"""
import contextlib, importlib.util, io, json, os, sys, tempfile, time
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("keeper", os.path.join(ROOT, "keeper", "keeper.py"))
k = importlib.util.module_from_spec(spec); spec.loader.exec_module(k)
from solders.keypair import Keypair  # noqa: E402
KP = Keypair()
MINT = "Xsc9qvGR1efVDFGLrVsmkzv3qi45LTBjeUKSPmx9qEh"
LIVE = {"multiplier": "1.001701196801074", "paused": False, "hook_program": None, "permanent_delegate": "5aMN", "extensions": [18, 12, 6, 25, 26, 4, 14, 19]}
OWNER = "9XFDKiaWHRj65fTgtWQWc25MJATACd49NJEo4sVWUfCm"
results = []

def order(**over):
    o = {"id": "22222222-2222-4222-8222-222222222222", "owner_pubkey": OWNER, "mint": MINT, "ticker": "NVDAx",
         "token_account": "aRuYpaDWMFVajyprXCFmFfR1g9BvWCPWkXyDMKENnch", "quantity_raw": "100000000", "decimals": 8,
         "floor_price_usd": "200.00", "ceiling_price_usd": "240.00",
         "status": "armed", "breach_count": 0, "last_checked_at": None, "delegation_sig": "x", "fill_sig": None, "fill_price_usd": None, "filled_at": None,
         "failure_reason": None, "created_at": "2026-09-22T00:00:00Z", "order_sig": "y", "delegate": str(KP.pubkey()), "remaining_raw": "100000000", "fills": [],
         "last_decision": None, "last_session": None, "last_price_usd": None, "pending_sig": None, "pending_amount_raw": None, "pending_since": None,
         "revoke_sig": None, "multiplier": LIVE["multiplier"], "rebases": [], "blocked": None, "multiplier_event": None}
    o.update(over); return o

class Fake:
    def __init__(self):
        self.state = dict(LIVE); self.price = Decimal("220"); self.sig_status = None; self.usdc_out = 240_000_000
        self.acct = (OWNER, str(KP.pubkey()), 100000000, 100000000); self.lamports = 50_000_000; self.height = 1000
        self.sent = []; self.builds = []; self.build_out = "240000000"; self.build_impact = "0"
    def rpc(self, method, params):
        if method == "getLatestBlockhash": return {"value": {"blockhash": "11111111111111111111111111111111", "lastValidBlockHeight": self.height + 150}}
        if method == "simulateTransaction": return {"value": {"err": None, "unitsConsumed": 300000}}
        if method == "sendTransaction": self.sent.append(params[0]); return "sig"
        if method == "getSignatureStatuses": return {"value": [None if self.sig_status is None else ({"err": "x"} if self.sig_status == "failed" else {"confirmationStatus": "confirmed", "err": None})]}
        if method == "getBlockHeight": return self.height
        if method == "getBalance": return {"value": self.lamports}
        if method == "getTransaction": return {"meta": {"preTokenBalances": [], "postTokenBalances": [{"owner": OWNER, "mint": str(k.USDC), "uiTokenAmount": {"amount": str(self.usdc_out)}}]}} if self.sig_status == "confirmed" else None
        raise RuntimeError(f"unexpected rpc {method}")
    def install(self):
        k.rpc = self.rpc; k.mint_state = lambda m: self.state
        k.fetch_prices = lambda mints: {m: (self.price, Decimal(1)) for m in mints}
        k.token_account_state = lambda ta: self.acct
        k.keeper_lamports = lambda pk: self.lamports
        k.pyth.fetch_latest = lambda ids, key=None: {i: {"status": "not_entitled", "error": "x"} for i in ids}
        def jb(input_mint, amount, taker, slippage_bps, dest):
            self.builds.append({"amount": int(amount), "slippage": slippage_bps})
            return {"inAmount": str(amount), "outAmount": self.build_out, "priceImpactPct": self.build_impact, "routePlan": [{"swapInfo": {"label": "Orca"}}],
                    "computeBudgetInstructions": [{"programId": str(k.COMPUTE_BUDGET), "accounts": [], "data": "AwAAAAAAAAAA"}],
                    "setupInstructions": [], "otherInstructions": [], "cleanupInstruction": None, "addressesByLookupTableAddress": {},
                    "swapInstruction": {"programId": "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4", "data": "AA==", "accounts": [
                        {"pubkey": str(k.ata(KP.pubkey(), k.Pubkey.from_string(MINT), k.TOKEN_2022)), "isSigner": False, "isWritable": True},
                        {"pubkey": str(k.ata(k.Pubkey.from_string(OWNER), k.USDC, k.TOKEN)), "isSigner": False, "isWritable": True},
                        {"pubkey": str(taker), "isSigner": True, "isWritable": True}]}}
        k.jup_build = jb
        os.environ["KEEPER_SECRET_KEY"] = KP.to_json()

def cycle(fake, *orders, session="open", dry=False):
    path = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False).name
    json.dump({"orders": list(orders)}, open(path, "w"))
    k.LOCAL_ORDERS, k.FORCE_SESSION, k.DRY_RUN = path, session, dry
    fake.install()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf): k.run()
    out = json.load(open(path))["orders"]; os.unlink(path)
    return out, [l.split(" ", 1)[1] for l in buf.getvalue().splitlines() if " [" in l or "aborted" in l]

dec = lambda line: line.split("] ", 1)[1] if "] " in line else line  # the decision text after the order tag

def check(name, ok, detail):
    results.append((name, ok)); print(("PASS " if ok else "FAIL ") + name + "\n      " + str(detail).replace("\n", "\n      "))

print("=== 1. THE BAND: CONDITION AND CONFIRMATION COUNTING ===")
f = Fake(); f.price = Decimal("220")
o, log = cycle(f, order(), session="weekend")
check("1a inside the band: hold, count 0, decision names the band", o[0]["breach_count"] == 0 and o[0]["status"] == "armed" and "inside the band 200.00 to 240.00" in log[-1], log[-1])
f = Fake(); f.price = Decimal("241")
o, log = cycle(f, order(), session="weekend")
check("1b above the ceiling in weekend: breach 1/3, string names the ceiling", o[0]["breach_count"] == 1 and o[0]["breach_side"] == "ceiling" and "at or above ceiling 240.00" in dec(log[-1]) and "floor" not in dec(log[-1]), dec(log[-1]))
o2, log2 = cycle(f, *o, session="weekend"); o3, log3 = cycle(f, *o2, session="weekend", dry=True)
check("1c three consecutive readings above the ceiling: triggered as a take profit", o3[0]["status"] == "triggered" and o3[0]["triggered_side"] == "ceiling" and "triggered (take profit)" in dec(log3[-2]), dec(log3[-2]))
f = Fake(); f.price = Decimal("241")
o, log = cycle(f, order(), session="weekend"); f.price = Decimal("230")
o2, log2 = cycle(f, *o, session="weekend")
check("1d a reading back inside the band re-arms and resets the ceiling count", o2[0]["breach_count"] == 0 and o2[0]["status"] == "armed" and o2[0].get("breach_side") is None, log2[-1])
f = Fake(); f.price = Decimal("241")
o, log = cycle(f, order(), session="weekend"); f.price = Decimal("199")
o2, log2 = cycle(f, *o, session="weekend")
check("1e a ceiling breach followed by a floor breach does not accumulate: count restarts at 1 on the floor side", o2[0]["breach_count"] == 1 and o2[0]["breach_side"] == "floor" and "at or below floor 200.00" in log2[-1], log2[-1])
f = Fake(); f.price = Decimal("241")
o, log = cycle(f, order(), session="overnight"); o2, log2 = cycle(f, *o, session="open")
check("1f a regime change resets a ceiling sequence exactly as it does a floor sequence", o2[0]["breach_count"] == 1, f"overnight 1/2 then open: {log2[-1]}")
f = Fake(); f.price = Decimal("241")
o, log = cycle(f, order(breach_count=2, breach_side="ceiling", breach_session="weekend", breach_last_at="2026-09-22T00:00:00Z"), session="weekend")
check("1g a ceiling sequence older than the 30-minute window starts again", o[0]["breach_count"] == 1, log[-1])
f = Fake(); f.price = Decimal("250")
o, log = cycle(f, order(floor_price_usd=None), session="open", dry=True)
check("1h ceiling only, no floor: fires on the ceiling with a ceiling-only hold/breach wording", o[0]["status"] == "triggered" and "at or above ceiling" in dec(log[-2]) and "floor" not in dec(log[-2]), dec(log[-2]))
f = Fake(); f.price = Decimal("230")
o, log = cycle(f, order(floor_price_usd=None), session="open")
check("1i ceiling only, inside: hold names the ceiling, not a floor", o[0]["status"] == "armed" and "below ceiling 240.00" in dec(log[-1]) and "floor" not in dec(log[-1]), dec(log[-1]))
f = Fake(); f.price = Decimal("199")
o, log = cycle(f, order(ceiling_price_usd=None), session="open", dry=True)
check("1j floor only (every existing order): unchanged wording and behaviour", o[0]["status"] == "triggered" and "at or below floor 200.00" in dec(log[-2]) and "ceiling" not in dec(log[-2]), dec(log[-2]))

print("=== 2. THE SELL PATH IS THE SAME PATH ===")
f = Fake(); f.price = Decimal("250"); f.sig_status = "confirmed"
o, log = cycle(f, order(), session="open")
fill = o[0]["fills"][0] if o[0]["fills"] else {}
check("2a take profit fills through the same execution and settlement, side recorded on the fill", o[0]["status"] == "filled" and fill.get("side") == "ceiling" and fill.get("kind") == "take profit" and f.builds[-1]["amount"] == 100000000, f"{log[-1]} | fill {fill}")
f = Fake(); f.price = Decimal("190"); f.sig_status = "confirmed"
o, log = cycle(f, order(), session="open")
check("2b stop fills record side floor, kind stop", o[0]["fills"][0].get("side") == "floor" and o[0]["fills"][0].get("kind") == "stop" and "(stop)" in log[-1], log[-1])
for sess, bps in (("open", 50), ("overnight", 150), ("weekend", 300)):
    f = Fake(); f.price = Decimal("250"); f.sig_status = "confirmed"
    o, log = cycle(f, order(breach_count=9, breach_side="ceiling", breach_session=sess, breach_last_at=k.now_iso()), session=sess)
    check(f"2c ceiling fill in {sess} uses the regime's {bps} bps", f.builds[-1]["slippage"] == bps, f"slippage_bps={f.builds[-1]['slippage']}")
f = Fake(); f.price = Decimal("250"); f.sig_status = "confirmed"; f.build_impact = "0.05"
o, log = cycle(f, order(breach_count=9, breach_side="ceiling", breach_session="weekend", breach_last_at=k.now_iso()), session="weekend")
check("2d weekend impact ladder applies to a take profit: full, half, quarter, eighth, refuse", [b["amount"] for b in f.builds] == [100000000, 50000000, 25000000, 12500000] and not f.sent, f"builds {[b['amount'] for b in f.builds]} | {log[-1]}")

print("=== 3. THE MINT GUARD AGAINST A CEILING (not symmetric with the floor) ===")
def change(f, mult): f.state = {**LIVE, "multiplier": mult}
# 3a forward split seen: price /10 confirmed by the reading -> both thresholds scale by 1/10; no false take profit or stop
f = Fake(); f.price = Decimal("220"); o, _ = cycle(f, order(), session="open")
change(f, "10.017011968010740"); o2, log2 = cycle(f, *o, session="open")
f.price = Decimal("22"); o3, log3 = cycle(f, *o2, session="open")
r = o3[0]["rebases"][-1]
check("3a 10-for-1 split seen in the price: floor 200 -> 20 and ceiling 240 -> 24, order still armed, nothing fired",
      r["kind"] == "split" and o3[0]["floor_price_usd"] == "20.000000" and o3[0]["ceiling_price_usd"] == "24.000000" and o3[0]["status"] == "armed" and o3[0]["breach_count"] == 0, f"{log2[-1]} || {log3[-1]}")
# 3b reverse split seen: price x10 -> a naive ceiling would fire; both thresholds scale by 10 and the change cycle evaluates nothing
f = Fake(); f.price = Decimal("220"); o, _ = cycle(f, order(), session="open")
change(f, "0.100170119680107"); f.price = Decimal("2200"); o2, log2 = cycle(f, *o, session="open")
check("3b reverse split, change cycle: price x10 looks like a ceiling breach but nothing is evaluated and the count is 0",
      o2[0]["breach_count"] == 0 and o2[0]["status"] == "armed" and "nothing evaluated" in log2[-1], log2[-1])
o3, log3 = cycle(f, *o2, session="open"); r = o3[0]["rebases"][-1]
check("3c reverse split confirmed on the next reading: ceiling 240 -> 2400 and floor 200 -> 2000, still armed at 2200",
      r["kind"] == "split" and o3[0]["ceiling_price_usd"] == "2400.000000" and o3[0]["floor_price_usd"] == "2000.000000" and o3[0]["status"] == "armed", f"{log3[-1]}")
# 3d accrual: multiplier +0.5%, price unchanged -> both thresholds byte-identical
f = Fake(); f.price = Decimal("220"); o, _ = cycle(f, order(), session="open")
change(f, "1.006709702785079"); o2, _ = cycle(f, *o, session="open"); o3, log3 = cycle(f, *o2, session="open"); r = o3[0]["rebases"][-1]
check("3d dividend accrual: floor and ceiling both left byte-identical", r["kind"] == "accrual" and o3[0]["floor_price_usd"] == "200.00" and o3[0]["ceiling_price_usd"] == "240.00", log3[-1])
# 3e the asymmetry: a split ASSUMED (no pre-change price) lowers the floor but must not lower the ceiling
f = Fake(); f.price = Decimal("22")
o, log = cycle(f, order(multiplier="1.001701196801074", multiplier_event={"detected_at": "2026-09-22T00:00:00Z", "old_multiplier": "1.001701196801074", "new_multiplier": "10.017011968010740", "pre_price_usd": None, "changes": 1}), session="open")
r = o[0]["rebases"][-1]
check("3e split assumed, not seen: floor lowered 200 -> 20 (can only make a sale less likely), ceiling left at 240 (lowering it on an assumption could sell), note recorded",
      r["kind"] == "split" and o[0]["floor_price_usd"] == "20.000000" and o[0]["ceiling_price_usd"] == "240.00" and "ceiling_note" in r and o[0]["status"] == "armed", f"{log[-1]} | {r.get('ceiling_note')}")
# 3f the assumed case in the reverse direction raises both (raising a ceiling can only make a sale less likely)
f = Fake(); f.price = Decimal("2200")
o, log = cycle(f, order(multiplier="1.001701196801074", multiplier_event={"detected_at": "2026-09-22T00:00:00Z", "old_multiplier": "1.001701196801074", "new_multiplier": "0.100170119680107", "pre_price_usd": None, "changes": 1}), session="open")
check("3f reverse split assumed: ceiling raised 240 -> 2400 (a sale less likely), floor raised too as before", o[0]["ceiling_price_usd"] == "2400.000000" and o[0]["floor_price_usd"] == "2000.000000", log[-1])
# 3g a ceiling sequence in progress when the multiplier changes is thrown away
f = Fake(); f.price = Decimal("241"); o, _ = cycle(f, order(), session="weekend"); o2, _ = cycle(f, *o, session="weekend")
change(f, "10.017011968010740"); o3, log3 = cycle(f, *o2, session="weekend")
check("3g a ceiling sequence at 2/3 is reset to 0 by a multiplier change, side cleared", o3[0]["breach_count"] == 0 and o3[0].get("breach_side") is None and o3[0].get("triggered_side") is None, log3[-1])
# 3h ceiling-only order through a seen split
f = Fake(); f.price = Decimal("220"); o, _ = cycle(f, order(floor_price_usd=None), session="open")
change(f, "10.017011968010740"); o2, _ = cycle(f, *o, session="open"); f.price = Decimal("22"); o3, log3 = cycle(f, *o2, session="open")
check("3h ceiling-only order, split seen: ceiling 240 -> 24, no floor invented", o3[0]["ceiling_price_usd"] == "24.000000" and o3[0]["floor_price_usd"] is None and o3[0]["rebases"][-1]["old_floor"] is None, log3[-1])

# 3i the case stated explicitly: a CONFIRMED 2-for-1 (price halves, next reading matches the ratio) rebases BOTH edges by the ratio
f = Fake(); f.price = Decimal("220"); o, _ = cycle(f, order(), session="open")
change(f, "2.003402393602148"); o2, log2 = cycle(f, *o, session="open")          # change cycle: nothing evaluated
f.price = Decimal("110"); o3, log3 = cycle(f, *o2, session="open"); r = o3[0]["rebases"][-1]   # price halved: confirmed split
check("3i confirmed 2-for-1: floor 200 -> 100 AND ceiling 240 -> 120, band width halves with the price, still armed at 110",
      r["kind"] == "split" and o3[0]["floor_price_usd"] == "100.000000" and o3[0]["ceiling_price_usd"] == "120.000000" and o3[0]["status"] == "armed" and "ceiling_note" not in r, f"{log3[-1]}")
f.price = Decimal("121"); o4, log4 = cycle(f, *o3, session="open", dry=True)
check("3i' after the confirmed 2-for-1 the rebased ceiling is live: 121 trips it (open, 1 confirmation)", o4[0]["status"] == "triggered" and o4[0]["triggered_side"] == "ceiling", dec(log4[-2]))

print(f"\n{sum(ok for _, ok in results)}/{len(results)} passed")
sys.exit(0 if all(ok for _, ok in results) else 1)
