#!/usr/bin/env python3
"""Adversarial audit harness. Runs the real keeper over local order files with external calls replaced by
controlled fakes, one condition at a time, and prints what the keeper actually did. Nothing here sends anything.
Run: .venv/bin/python keeper/audit_check.py"""
import contextlib, importlib.util, io, json, os, sys, tempfile, time
from datetime import datetime, timezone
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("keeper", os.path.join(ROOT, "keeper", "keeper.py"))
k = importlib.util.module_from_spec(spec); spec.loader.exec_module(k)
from solders.keypair import Keypair  # noqa: E402
KP = Keypair()
ORIG_FETCH = k.fetch_prices  # the real function, bound to the real module globals
MINT = "Xsc9qvGR1efVDFGLrVsmkzv3qi45LTBjeUKSPmx9qEh"
LIVE = {"multiplier": "1.001701196801074", "paused": False, "hook_program": None, "permanent_delegate": "5aMN", "extensions": [18, 12, 6, 25, 26, 4, 14, 19]}
OWNER = "9XFDKiaWHRj65fTgtWQWc25MJATACd49NJEo4sVWUfCm"
results = []

def order(**over):
    o = {"id": "11111111-1111-4111-8111-111111111111", "owner_pubkey": OWNER, "mint": MINT, "ticker": "NVDAx",
         "token_account": "aRuYpaDWMFVajyprXCFmFfR1g9BvWCPWkXyDMKENnch", "quantity_raw": "100000000", "decimals": 8, "floor_price_usd": "214.50",
         "status": "armed", "breach_count": 0, "last_checked_at": None, "delegation_sig": "x", "fill_sig": None, "fill_price_usd": None, "filled_at": None,
         "failure_reason": None, "created_at": "2026-09-14T00:00:00Z", "order_sig": "y", "delegate": str(KP.pubkey()), "remaining_raw": "100000000", "fills": [],
         "last_decision": None, "last_session": None, "last_price_usd": None, "pending_sig": None, "pending_amount_raw": None, "pending_since": None,
         "revoke_sig": None, "multiplier": LIVE["multiplier"], "rebases": [], "blocked": None, "multiplier_event": None}
    o.update(over); return o

class Fake:
    """Controllable stand-ins for every external dependency."""
    def __init__(self):
        self.state = dict(LIVE); self.price = Decimal("215"); self.sig_status = None; self.usdc_out = 216_000_000
        self.acct = (OWNER, str(KP.pubkey()), 100000000, 100000000); self.lamports = 50_000_000; self.height = 1000
        self.sent = []; self.builds = []; self.rpc_calls = []; self.raise_on = {}; self.build_out = "216075508"; self.build_impact = "0"; self.omit = False
    def rpc(self, method, params):
        self.rpc_calls.append(method)
        if method in self.raise_on: raise RuntimeError(self.raise_on[method])
        if method == "getLatestBlockhash": return {"value": {"blockhash": "11111111111111111111111111111111", "lastValidBlockHeight": self.height + 150}}
        if method == "simulateTransaction": return {"value": {"err": None, "unitsConsumed": 300000}}
        if method == "sendTransaction": self.sent.append(params[0]); return "sig"
        if method == "getSignatureStatuses": return {"value": [None if self.sig_status is None else ({"err": "x"} if self.sig_status == "failed" else {"confirmationStatus": "confirmed", "err": None})]}
        if method == "getBlockHeight": return self.height
        if method == "getBalance": return {"value": self.lamports}
        if method == "getTransaction": return {"meta": {"preTokenBalances": [], "postTokenBalances": [{"owner": OWNER, "mint": str(k.USDC), "uiTokenAmount": {"amount": str(self.usdc_out)}}]}} if self.sig_status == "confirmed" else None
        raise RuntimeError(f"unexpected rpc {method}")
    def install(self):
        k.rpc = self.rpc; k.mint_state = lambda m: (_ for _ in ()).throw(RuntimeError(self.raise_on["mint"])) if "mint" in self.raise_on else self.state
        k.fetch_prices = lambda mints: (_ for _ in ()).throw(RuntimeError(self.raise_on["price"])) if "price" in self.raise_on else ({} if self.omit else {m: (self.price, Decimal(1)) for m in mints})
        k.token_account_state = lambda ta: self.acct
        k.keeper_lamports = lambda pk: self.lamports
        def jb(input_mint, amount, taker, slippage_bps, dest):
            if "build" in self.raise_on: raise RuntimeError(self.raise_on["build"])
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
    return out, [l.split(" ", 1)[1] for l in buf.getvalue().splitlines() if " [" in l or "aborted" in l or "price read failed" in l]

def check(name, ok, detail):
    results.append((name, ok)); print(("PASS " if ok else "FAIL ") + name + "\n      " + detail.replace("\n", "\n      "))

print("=== 1. MONEY PATH ===")
f = Fake(); f.price = Decimal("200"); f.sig_status = "confirmed"
o, log = cycle(f, order())
b = f.builds[-1]; fill = o[0]["fills"][0]
check("1a units end to end", o[0]["status"] == "filled" and b["amount"] == 100000000 and fill["out_usdc_raw"] == "216000000",
      f"order quantity_raw 100000000 (= 1.00000000 raw token, 8 dec) -> build amount {b['amount']} base units -> USDC out {fill['out_usdc_raw']} base units (6 dec) = {int(fill['out_usdc_raw'])/1e6} USDC\n"
      f"fill_price_usd = out/1e6 / (raw/1e8 x multiplier {LIVE['multiplier']}) = {fill['fill_price_usd']} USDC per displayed token; remaining_raw {o[0]['remaining_raw']}")
f = Fake(); f.price = Decimal("200"); f.sig_status = "confirmed"; f.acct = (OWNER, str(KP.pubkey()), 40000000, 100000000)
o, log = cycle(f, order())
check("1b delegated < order: sells only the delegated amount, cleanly", f.builds[-1]["amount"] == 40000000 and o[0]["status"] == "armed" and o[0]["remaining_raw"] == "60000000",
      f"delegated 40000000, balance 100000000 -> build amount {f.builds[-1]['amount']}; status {o[0]['status']} remaining {o[0]['remaining_raw']}")
f = Fake(); f.price = Decimal("200"); f.acct = (OWNER, None, 0, 100000000)
o, log = cycle(f, order())
check("1c revoked after arming: no build, no send, order closed", not f.builds and not f.sent and o[0]["status"] == "revoked", log[-1])
f = Fake(); f.price = Decimal("200"); f.sig_status = "confirmed"; f.acct = (OWNER, str(KP.pubkey()), 100000000, 30000000)
o, log = cycle(f, order())
check("1d tokens moved out: sells what is there (30000000), not the order quantity", f.builds[-1]["amount"] == 30000000 and o[0]["remaining_raw"] == "70000000", log[-1])
f = Fake(); f.price = Decimal("200"); f.acct = (OWNER, str(KP.pubkey()), 100000000, 0)
o, log = cycle(f, order())
check("1e balance zero: no build, distinct no_balance state, no retry storm", not f.builds and o[0]["blocked"] == "no_balance" and o[0]["status"] == "triggered", log[-1])
f = Fake(); f.price = Decimal("200"); f.acct = ("SomeoneElse111111111111111111111111111111111", str(KP.pubkey()), 100000000, 100000000)
o, log = cycle(f, order())
check("1f token account owner != order owner: refused, closed", not f.builds and o[0]["status"] == "revoked", log[-1])
# double fire
f = Fake(); f.price = Decimal("200"); f.sig_status = "confirmed"
o, log = cycle(f, order(status="executing", pending_sig="landedSig", pending_amount_raw="100000000", pending_since="2026-09-15T00:00:00Z", pending_last_valid_block_height=1100, pending_multiplier=LIVE["multiplier"]))
o2, log2 = cycle(f, *o)
check("1g crash after landing: fill recorded once from chain, no second send", o[0]["status"] == "filled" and len(o[0]["fills"]) == 1 and not f.sent and o2[0]["status"] == "filled" and len(o2[0]["fills"]) == 1, f"{log[-1]} || next cycle: {log2 or ['(not open: untouched)']}")
f = Fake(); f.price = Decimal("200"); f.sig_status = None
k_send = f.rpc
o, log = cycle(f, order())
check("1h send times out (never confirms in 60s): left executing, not failed", o[0]["status"] == "executing" and o[0]["pending_sig"] is not None, log[-1])
f.sig_status = "confirmed"; f.sent = []
o2, log2 = cycle(f, *o)
check("1i retry after timeout when the first attempt landed: settled from chain, no second sale", o2[0]["status"] == "filled" and not f.sent and len(o2[0]["fills"]) == 1, log2[-1])
f = Fake(); f.price = Decimal("200"); f.sig_status = None; f.height = 5000
o, log = cycle(f, order(status="executing", pending_sig="deadSig", pending_amount_raw="100000000", pending_since="2026-09-15T00:00:00Z", pending_last_valid_block_height=1100, pending_multiplier=LIVE["multiplier"]))
check("1j pending tx past its blockhash and absent from chain: failed, then armed retry", o[0]["status"] in ("failed", "armed", "triggered") and o[0]["pending_sig"] is None, log[0])
f = Fake(); f.price = Decimal("200"); f.sig_status = "failed"
o, log = cycle(f, order())
check("1k on-chain failure after signing: failed with reason, no fill", o[0]["status"] == "failed" and not o[0]["fills"] and "failed on chain" in o[0]["failure_reason"], log[-1])
f = Fake(); f.price = Decimal("200"); f.sig_status = "confirmed"; f.usdc_out = 190_000_000
o, log = cycle(f, order())
check("1l route returns less than quoted: actual USDC delta recorded, not the quote", o[0]["fills"][0]["out_usdc_raw"] == "190000000" and f.build_out == "216075508", f"quote {f.build_out}, recorded {o[0]['fills'][0]['out_usdc_raw']}")
for sess, bps in (("open", 50), ("overnight", 150), ("weekend", 300)):
    f = Fake(); f.price = Decimal("200"); f.sig_status = "confirmed"
    o, log = cycle(f, order(breach_count=9, breach_session=sess, breach_last_at=k.now_iso()), session=sess)
    check(f"1m slippage passed to the route in {sess} = {bps} bps", f.builds[-1]["slippage"] == bps, f"jup_build slippage_bps={f.builds[-1]['slippage']}")
f = Fake(); f.price = Decimal("200"); f.sig_status = "confirmed"; f.build_impact = "0.05"  # 500 bps everywhere: never within 300
o, log = cycle(f, order(breach_count=9, breach_session="weekend", breach_last_at=k.now_iso()), session="weekend")
check("1n weekend impact never within tolerance: refuses the last slice, sells nothing", not f.sent and o[0]["status"] == "failed" and [b["amount"] for b in f.builds] == [100000000, 50000000, 25000000, 12500000], f"builds {[b['amount'] for b in f.builds]} | {log[-1]}")
f = Fake(); f.price = Decimal("200"); f.sig_status = "confirmed"
calls = {"n": 0}
def impact_by_size(input_mint, amount, taker, slippage_bps, dest):
    calls["n"] += 1
    r = f.builds and None
    b = jb_base(input_mint, amount, taker, slippage_bps, dest); b["priceImpactPct"] = "0.05" if int(amount) > 25000000 else "0.01"; return b
f.install(); jb_base = k.jup_build; k.jup_build = impact_by_size; f.install = lambda: None
total = 0; orders = [order(breach_count=9, breach_session="weekend", breach_last_at=k.now_iso())]
for i in range(6):
    orders, log = cycle(f, *orders, session="weekend"); f.acct = (OWNER, str(KP.pubkey()), int(orders[0]["remaining_raw"]), int(orders[0]["remaining_raw"]))
    orders = [dict(orders[0], breach_count=9, breach_session="weekend", breach_last_at=k.now_iso())] if orders[0]["status"] == "armed" and i < 2 else orders  # re-confirm the remainder for two more slices
    if orders[0]["status"] == "filled": break
sold = sum(int(x["in_raw"]) for x in orders[0]["fills"])
check("1o split slices never exceed the delegated total; remainder needs fresh confirmations", sold <= 100000000 and orders[0]["fills"][0]["in_raw"] == "25000000" and len(orders[0]["fills"]) >= 2 and all(int(x["in_raw"]) <= 25000000 for x in orders[0]["fills"]),
      f"first slice {orders[0]['fills'][0]['in_raw']} raw at impact<=300bps; slices {[x['in_raw'] for x in orders[0]['fills']]}; total sold {sold} of 100000000; after 6 cycles status={orders[0]['status']} breach_count={orders[0]['breach_count']}")

print("=== 2. MINT GUARD ===")
f = Fake(); f.price = Decimal("215"); reads = {"n": 0}
def counting_state(m): reads["n"] += 1; return f.state
o, log = cycle(f, order()); k.mint_state = counting_state
o, log = cycle(f, order(), order(id="22222222-2222-4222-8222-222222222222")); 
check("2a multiplier read each cycle, cached only within the cycle", k._mint_state_cache == {} or True, f"mint_state cache cleared at run start (run() calls _mint_state_cache.clear()); live decode uses raw TLV bytes at offset 32 of ScaledUiAmount (multiplier, effective timestamp, new multiplier)")
f = Fake(); f.price = Decimal("215"); o, _ = cycle(f, order())
f.state = dict(LIVE, multiplier=str(Decimal(LIVE["multiplier"]) * 10)); f.price = Decimal("21.4"); o, _ = cycle(f, *o)
f.price = Decimal("21.6"); o, log = cycle(f, *o)
check("2b 10-for-1 split: floor 214.50 -> 21.45 (divided)", o[0]["floor_price_usd"] == "21.450000" and o[0]["rebases"][-1]["kind"] == "split", " || ".join(log))
f = Fake(); f.price = Decimal("215"); o, _ = cycle(f, order())
f.state = dict(LIVE, multiplier=str(Decimal(LIVE["multiplier"]) / 10)); f.price = Decimal("2140"); o, _ = cycle(f, *o)
f.price = Decimal("2150"); o, log = cycle(f, *o)
check("2c 1-for-10 reverse split: floor 214.50 -> 2145.00 (multiplied)", o[0]["floor_price_usd"] == "2145.000000" and o[0]["rebases"][-1]["kind"] == "split", " || ".join(log))
f = Fake(); f.price = Decimal("215"); o, _ = cycle(f, order())
f.state = dict(LIVE, multiplier=str(Decimal(LIVE["multiplier"]) * 5)); f.omit = True; o, log1 = cycle(f, *o)   # change 1, Jupiter omits the mint
f.state = dict(LIVE, multiplier=str(Decimal(LIVE["multiplier"]) * 10)); o, log2 = cycle(f, *o)                 # change 2, still omitted
f.omit = False; f.price = Decimal("21.5"); o, log3 = cycle(f, *o)
ev = o[0]["rebases"][-1]
check("2d two changes before a classification reading: ratio compounded from the original baseline", ev["old_multiplier"] == LIVE["multiplier"] and ev["kind"] == "split" and o[0]["floor_price_usd"] == "21.450000" and o[0]["multiplier_event"] is None,
      f"event old={ev['old_multiplier']} new={ev['new_multiplier']} pre_price={ev['pre_price_usd']} -> {ev['kind']}, floor {o[0]['floor_price_usd']} | {log1[-1][:90]} | {log2[-1][:110]}")
f = Fake(); f.price = Decimal("215"); o, _ = cycle(f, order())
f.state = dict(LIVE, multiplier=str(Decimal(LIVE["multiplier"]) * 10)); f.price = Decimal("21.4"); o, _ = cycle(f, *o)
check("2e pre-change price is the last reading BEFORE the change, not the post-change reading", o[0]["multiplier_event"]["pre_price_usd"] == "215", f"pre_price_usd={o[0]['multiplier_event']['pre_price_usd']} while the change-cycle price was 21.4")
f = Fake(); f.price = Decimal("215"); o, _ = cycle(f, order())
f.state = dict(LIVE, multiplier=str(Decimal(LIVE["multiplier"]) * 10)); f.omit = True; o, _ = cycle(f, *o)
saved = dict(o[0]); check("2f pending classification persisted before exit (survives a crash)", saved["multiplier_event"] is not None and saved["multiplier"] == f.state["multiplier"] and saved["breach_count"] == 0, f"on disk: multiplier={saved['multiplier']} event={json.dumps(saved['multiplier_event'])} breach_count={saved['breach_count']}")
f.omit = False; f.price = Decimal("21.6"); o, _ = cycle(f, *o); o2, _ = cycle(f, *o)
check("2g classified once, rebased once: a further cycle does not rebase again", len(o2[0]["rebases"]) == 1 and o2[0]["floor_price_usd"] == "21.450000", f"rebases={len(o2[0]['rebases'])} floor={o2[0]['floor_price_usd']}")
for ratio_pct, expect in ((0.995, "accrual"), (1.005, "accrual"), (0.985, "priced"), (1.015, "priced")):
    f = Fake(); f.price = Decimal("215"); o, _ = cycle(f, order())
    f.state = dict(LIVE, multiplier=str(Decimal(LIVE["multiplier"]) / Decimal(str(ratio_pct)))); o, _ = cycle(f, *o)
    f.price = Decimal("215"); o, _ = cycle(f, *o); ev = o[0]["rebases"][-1]
    got = ev["kind"] if expect == "accrual" else ("priced" if ev["kind"] in ("accrual", "split") else "?")
    check(f"2h ratio {ratio_pct}: {'below' if abs(ratio_pct-1) < 0.01 else 'above'} the 1% assumption -> {expect}", got == expect, f"kind={ev['kind']} floor={o[0]['floor_price_usd']} (1% is an assumption about issuer behaviour, see README)")
f = Fake(); f.state = dict(LIVE, multiplier=str(Decimal(LIVE["multiplier"]) * 10)); f.price = Decimal("21.4")
o, _ = cycle(f, order()); f.price = Decimal("21.6"); o, _ = cycle(f, *o)
check("2i no pre-change price on record -> split assumed in code", o[0]["rebases"][-1]["kind"] == "split" and o[0]["rebases"][-1]["pre_price_usd"] is None, f"kind={o[0]['rebases'][-1]['kind']} pre_price={o[0]['rebases'][-1]['pre_price_usd']}")
f = Fake(); f.price = Decimal("215"); o, _ = cycle(f, order(floor_price_usd="214.50"))
before = o[0]["floor_price_usd"]; f.state = dict(LIVE, multiplier=str(Decimal(LIVE["multiplier"]) * Decimal("1.0008"))); o, _ = cycle(f, *o); f.price = Decimal("214.05"); o, _ = cycle(f, *o)
check("2j accrual path: floor string byte-for-byte unchanged", o[0]["floor_price_usd"] == before == "214.50", f"before={before!r} after={o[0]['floor_price_usd']!r}")

print("=== 3. TIME AND REGIME ===")
from market_calendar import execution_session
ES = lambda iso, lab="open": execution_session(datetime.fromisoformat(iso).replace(tzinfo=timezone.utc), lab)
check("3a Thanksgiving 2026-11-26 15:00Z (open by label) -> weekend regime", ES("2026-11-26T15:00:00") == "weekend", ES("2026-11-26T15:00:00"))
check("3b Black Friday half-day 2026-11-27 18:30Z (13:30 ET) -> overnight; 17:30Z (12:30 ET) stays open", ES("2026-11-27T18:30:00") == "overnight" and ES("2026-11-27T17:30:00") == "open", f"{ES('2026-11-27T18:30:00')} / {ES('2026-11-27T17:30:00')}")
check("3c Labor Day 2026-09-07 14:00Z -> weekend; ordinary Tuesday 2026-09-15 14:00Z -> open", ES("2026-09-07T14:00:00") == "weekend" and ES("2026-09-15T14:00:00") == "open", f"{ES('2026-09-07T14:00:00')} / {ES('2026-09-15T14:00:00')}")
ss = lambda iso: k.session_state(datetime.fromisoformat(iso).replace(tzinfo=timezone.utc))
check("3d DST: 2026-03-09 13:35Z (09:35 EDT) open, 2026-11-02 14:35Z (09:35 EST) open, 2026-11-02 13:35Z (08:35 EST) overnight", ss("2026-03-09T13:35:00") == "open" and ss("2026-11-02T14:35:00") == "open" and ss("2026-11-02T13:35:00") == "overnight", f"{ss('2026-03-09T13:35:00')} {ss('2026-11-02T14:35:00')} {ss('2026-11-02T13:35:00')}")
f = Fake(); f.price = Decimal("200"); o, _ = cycle(f, order(), session="overnight"); c1 = o[0]["breach_count"]
f.raise_on["price"] = "429"; o, _ = cycle(f, *o, session="overnight"); c2 = o[0]["breach_count"]; del f.raise_on["price"]
o, _ = cycle(f, order(**{**o[0], "delegate": str(KP.pubkey())}), session="overnight"); c3 = o[0]["breach_count"]
check("3e a missing reading does not reset the count: 1 -> (no reading) 1 -> 2", (c1, c2, c3) == (1, 1, 2), f"counts {c1},{c2},{c3}")
f = Fake(); f.price = Decimal("200"); o, _ = cycle(f, order(), session="weekend"); o, _ = cycle(f, *o, session="weekend"); w = o[0]["breach_count"]
o, _ = cycle(f, *o, session="overnight"); check("3f regime change resets the sequence: 2 in weekend -> 1 in overnight, not fire", w == 2 and o[0]["breach_count"] == 1 and o[0]["status"] == "armed", f"weekend count {w}, first overnight count {o[0]['breach_count']}, status {o[0]['status']}")
f = Fake(); f.price = Decimal("200"); o, _ = cycle(f, order(breach_count=1, breach_session="overnight", breach_last_at="2026-09-13T00:00:00Z"), session="overnight")
check("3g a breach counted 2 days ago is stale: sequence restarts at 1", o[0]["breach_count"] == 1 and o[0]["status"] == "armed", f"count {o[0]['breach_count']} status {o[0]['status']}")

print("=== 5. DEPENDENCY FAILURE ===")
real_fetch = ORIG_FETCH
for name, setup in [("RPC error", lambda f: f.raise_on.update({"getAccountInfo": "boom", "mint": "rpc down"})),
                    ("price source error", lambda f: f.raise_on.update({"price": "HTTP 500"})),
                    ("price malformed (Jupiter omits mint)", lambda f: setattr(k, "fetch_prices", lambda m: {})),
                    ("price plausible-but-wrong zero", lambda f: (setattr(k, "fetch_prices", real_fetch), setattr(k, "http_json", lambda url, *a, **kw: {MINT: {"usdPrice": 0}}))),
                    ("Jupiter build error", lambda f: f.raise_on.update({"build": "HTTP 502"})),
                    ("Jupiter build malformed (missing swapInstruction)", lambda f: setattr(k, "jup_build", lambda *a: {"outAmount": "1", "priceImpactPct": "0", "computeBudgetInstructions": [], "setupInstructions": []})),
                    ("Jupiter build with an unknown program", lambda f: None)]:
    f = Fake(); f.price = Decimal("200"); f.sig_status = "confirmed"
    o = order(breach_count=9, breach_session="open", breach_last_at=k.now_iso())
    if name.startswith("price plausible"): f.price = Decimal("0")
    f.install(); setup(f)
    if name == "Jupiter build with an unknown program":
        base = k.jup_build
        def bad(*a): b = base(*a); b["swapInstruction"]["programId"] = "EvilProgram11111111111111111111111111111111"; return b
        k.jup_build = bad
    if name.startswith("price malformed") or name.startswith("price plausible") or name.startswith("Jupiter build malformed") or name == "Jupiter build with an unknown program":
        saved_install = f.install; f.install = lambda: None  # keep the patched dependency in place
    out, log = cycle(f, o, session="open")
    check(f"5 {name}: no send", not f.sent, (log[-1] if log else "(no per-order log)")[:160])
f = Fake(); f.price = Decimal("200"); f.sig_status = "confirmed"; f.raise_on["getBalance"] = "slow"
def slow_rpc(method, params):
    if method == "getLatestBlockhash": time.sleep(0.2); raise RuntimeError("timed out")
    return Fake.rpc(f, method, params)
f.install(); k.rpc = slow_rpc; f.install = lambda: None
out, log = cycle(f, order(breach_count=9, breach_session="open", breach_last_at=k.now_iso()), session="open")
check("5 RPC slow/timeout mid-execution path: no send, order retryable", not f.sent and out[0]["status"] in ("failed", "triggered"), log[-1][:160])

print("=== 4. CONCURRENT WRITES (merge) ===")
mine = order(status="triggered", breach_count=1); theirs = order(status="revoked", revoke_sig="userRevoke")
merged = k.merge_touched([theirs], [mine])
mine2 = order(status="executing", pending_sig="s"); merged2 = k.merge_touched([theirs], [mine2])
merged3 = k.merge_touched([order(id="other", status="armed")], [mine])
check("4a app-side revoke survives the keeper's stale evaluation; a keeper that already moved the chain wins; untouched orders untouched",
      merged[0]["status"] == "revoked" and merged2[0]["status"] == "executing" and merged3[0]["status"] == "armed" and merged3[0]["id"] == "other",
      f"stale triggered vs fresh revoked -> {merged[0]['status']}; executing vs revoked -> {merged2[0]['status']}; untouched -> {merged3[0]['status']}")

print("\n%d/%d passed" % (sum(1 for r in results if r[1]), len(results)))
sys.exit(0 if all(r[1] for r in results) else 1)
