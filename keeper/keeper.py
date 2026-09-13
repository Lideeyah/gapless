#!/usr/bin/env python3
"""Gapless keeper: evaluates armed orders against live prices and executes regime-aware stops.

Runs continuously on GitHub Actions (one reading every 5 minutes per run). Session state comes from
recorder.session_state(), the single implementation. Regime thresholds live in regimes.json.

State machine (data/orders.json):
  armed --breach threshold met--> triggered --> executing --> filled
                                                   |
                                                   +--> failed --> armed (next run retries)
  armed --user revoke--> revoked
`triggered` and `executing` are persisted (committed) before the next step so a run that dies
mid-execution is recoverable: a later run finds `executing` and checks the chain for the pending
signature before deciding anything.

Execution is one atomic v0 transaction signed only by the keeper (the SPL delegate):
  create keeper's xStock ATA + user's USDC ATA if missing -> transfer_checked the delegated amount from
  the user's account to the keeper's -> Jupiter swap with destinationTokenAccount = user's USDC ATA.
If any step fails the whole transaction reverts. Proceeds land in the owner's wallet and nowhere else.

All token amounts are raw integer strings. No floating point touches an amount.
"""
import base64, json, os, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from recorder import PRICE_URL, session_state  # noqa: E402  the one session-state implementation

from solders.address_lookup_table_account import AddressLookupTableAccount  # noqa: E402
from solders.hash import Hash  # noqa: E402
from solders.instruction import AccountMeta, Instruction  # noqa: E402
from solders.keypair import Keypair  # noqa: E402
from solders.message import MessageV0  # noqa: E402
from solders.pubkey import Pubkey  # noqa: E402
from solders.transaction import VersionedTransaction  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "regimes.json")) as _f:
    REGIMES = {k: v for k, v in json.load(_f).items() if not k.startswith("_")}

RPC_URL = os.environ.get("RPC_URL") or "https://api.mainnet-beta.solana.com"
REPO = os.environ.get("GITHUB_REPOSITORY", "Lideeyah/gapless")
GH_TOKEN = os.environ.get("GITHUB_TOKEN")
DRY_RUN = os.environ.get("KEEPER_DRY_RUN") == "1"          # build + simulate, never send, never enter `executing`
FORCE_SESSION = os.environ.get("KEEPER_FORCE_SESSION")     # test hook: override the regime
LOCAL_ORDERS = os.environ.get("KEEPER_ORDERS_FILE")        # test hook: local file instead of GitHub
AUTHOR = {"name": os.environ.get("GIT_AUTHOR_NAME", "Lydia Solomon"),
          "email": os.environ.get("GIT_AUTHOR_EMAIL", "lydiasolomon137@gmail.com")}
EXECUTING_TIMEOUT_S = 180  # a blockhash is dead well before this; after it, an unseen pending tx cannot land

USDC = Pubkey.from_string("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v")
USDC_DECIMALS = 6
TOKEN_2022 = Pubkey.from_string("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb")
TOKEN = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
ATA_PROGRAM = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
SYSTEM = Pubkey.from_string("11111111111111111111111111111111")
COMPUTE_BUDGET = Pubkey.from_string("ComputeBudget111111111111111111111111111111")
BUILD_URL = "https://api.jup.ag/swap/v2/build"
OPEN = ("armed", "triggered", "executing", "failed")


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg):
    print(f"{now_iso()} {msg}", flush=True)


def http_json(url, body=None, headers=None, method=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"User-Agent": "gapless-keeper", "Content-Type": "application/json", **(headers or {})})
    for attempt in range(4):  # keyless Jupiter is 0.5 RPS; back off on 429
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < 3:
                time.sleep(2 + 2 * attempt)
                continue
            raise RuntimeError(f"HTTP {exc.code} from {url.split('?')[0]}: {exc.read()[:300].decode(errors='replace')}") from None


def rpc(method, params):
    out = http_json(RPC_URL, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    if "error" in out:
        raise RuntimeError(f"rpc {method}: {out['error']}")
    return out["result"]


# ---------------------------------------------------------------- order store (data/orders.json)
def gh_headers():
    return {"Authorization": f"Bearer {GH_TOKEN}", "Accept": "application/vnd.github+json"}


def load_orders():
    if LOCAL_ORDERS:
        with open(LOCAL_ORDERS) as f:
            return json.load(f), None
    meta = http_json(f"https://api.github.com/repos/{REPO}/contents/data/orders.json?ref=main", headers=gh_headers())
    return json.loads(base64.b64decode(meta["content"])), meta["sha"]


def save_orders(store, sha, message):
    """Commit the store. Returns the new sha. On a 409 (file moved), reload, re-apply our orders by id, retry once."""
    body = json.dumps(store, indent=2) + "\n"
    if LOCAL_ORDERS:
        with open(LOCAL_ORDERS, "w") as f:
            f.write(body)
        return None
    payload = {"message": message, "content": base64.b64encode(body.encode()).decode(), "sha": sha, "committer": AUTHOR, "author": AUTHOR}
    try:
        return http_json(f"https://api.github.com/repos/{REPO}/contents/data/orders.json", payload, gh_headers(), "PUT")["content"]["sha"]
    except RuntimeError as exc:
        if "HTTP 409" not in str(exc):
            raise
        log("orders.json moved under us; reloading and re-applying this run's state once")
        fresh, fresh_sha = load_orders()
        ours = {o["id"]: o for o in store["orders"]}
        fresh["orders"] = [ours.get(o["id"], o) for o in fresh["orders"]] + [o for o in store["orders"] if o["id"] not in {x["id"] for x in fresh["orders"]}]
        store["orders"] = fresh["orders"]
        payload["content"] = base64.b64encode((json.dumps(fresh, indent=2) + "\n").encode()).decode()
        payload["sha"] = fresh_sha
        return http_json(f"https://api.github.com/repos/{REPO}/contents/data/orders.json", payload, gh_headers(), "PUT")["content"]["sha"]


# ---------------------------------------------------------------- chain helpers
def load_keeper():
    raw = os.environ.get("KEEPER_SECRET_KEY", "").strip()
    if not raw:
        return None
    return Keypair.from_json(raw) if raw.startswith("[") else Keypair.from_base58_string(raw)


def ata(owner, mint, token_program):
    return Pubkey.find_program_address([bytes(owner), bytes(token_program), bytes(mint)], ATA_PROGRAM)[0]


def ix_create_ata_idempotent(payer, owner, mint, token_program):
    return Instruction(ATA_PROGRAM, bytes([1]), [
        AccountMeta(payer, True, True), AccountMeta(ata(owner, mint, token_program), False, True),
        AccountMeta(owner, False, False), AccountMeta(mint, False, False),
        AccountMeta(SYSTEM, False, False), AccountMeta(token_program, False, False)])


def ix_transfer_checked_as_delegate(source, mint, dest, delegate, amount, decimals):
    data = bytes([12]) + int(amount).to_bytes(8, "little") + bytes([decimals])
    return Instruction(TOKEN_2022, data, [AccountMeta(source, False, True), AccountMeta(mint, False, False),
                                          AccountMeta(dest, False, True), AccountMeta(delegate, True, False)])


def ix_compute_limit(units):
    return Instruction(COMPUTE_BUDGET, bytes([2]) + int(units).to_bytes(4, "little"), [])


def from_jup_ix(ix):
    return Instruction(Pubkey.from_string(ix["programId"]), base64.b64decode(ix["data"]),
                       [AccountMeta(Pubkey.from_string(a["pubkey"]), a["isSigner"], a["isWritable"]) for a in ix["accounts"]])


def fetch_prices(mints):
    """{mint: (Decimal usdPrice, Decimal uiMultiplier)} from Jupiter Price API v3, one batched call."""
    data = http_json(PRICE_URL + ",".join(mints))
    out = {}
    for m in mints:
        if m in data and data[m].get("usdPrice") is not None:
            out[m] = (Decimal(str(data[m]["usdPrice"])), Decimal(str((data[m].get("scaledUiConfig") or {}).get("multiplier") or 1)))
    return out


def delegation_on_chain(token_account):
    info = rpc("getAccountInfo", [token_account, {"encoding": "jsonParsed", "commitment": "confirmed"}])["value"]
    parsed = info["data"]["parsed"] if info and isinstance(info["data"], dict) else None
    if not parsed or parsed.get("type") != "account":
        return None, 0
    p = parsed["info"]
    return p.get("delegate"), int((p.get("delegatedAmount") or {}).get("amount") or 0)


def jup_build(input_mint, amount, taker, slippage_bps, dest_usdc_ata):
    q = urllib.parse.urlencode({"inputMint": str(input_mint), "outputMint": str(USDC), "amount": str(amount), "taker": str(taker),
                                "slippageBps": str(slippage_bps), "destinationTokenAccount": str(dest_usdc_ata)})
    return http_json(f"{BUILD_URL}?{q}")


def usdc_received(sig, owner):
    """Real USDC delta for the owner from the confirmed transaction's token balances, or None if unavailable."""
    tx = rpc("getTransaction", [sig, {"encoding": "json", "maxSupportedTransactionVersion": 0, "commitment": "confirmed"}])
    if not tx:
        return None
    def total(entries):
        return sum(int(e["uiTokenAmount"]["amount"]) for e in entries if e.get("owner") == str(owner) and e.get("mint") == str(USDC))
    return total(tx["meta"].get("postTokenBalances", [])) - total(tx["meta"].get("preTokenBalances", []))


def signature_status(sig):
    """'confirmed' | 'failed' | None (not seen)."""
    st = rpc("getSignatureStatuses", [[sig], {"searchTransactionHistory": True}])["value"][0]
    if not st:
        return None
    if st.get("err"):
        return "failed"
    return "confirmed" if st.get("confirmationStatus") in ("confirmed", "finalized") else None


def fill_price(out_usdc_raw, in_raw, decimals, multiplier):
    """USDC per UI token, as a decimal string. A price, not an amount, so Decimal is fine here."""
    ui_qty = Decimal(in_raw) / (Decimal(10) ** decimals) * multiplier
    if ui_qty == 0:
        return "0"
    return str((Decimal(out_usdc_raw) / (Decimal(10) ** USDC_DECIMALS) / ui_qty).quantize(Decimal("0.000001"), ROUND_DOWN))


# ---------------------------------------------------------------- execution
def build_execution(order, keeper, rules):
    """Size (weekend split), build, simulate and sign. Returns (amount_raw, signed_b64, signature, build)."""
    mint = Pubkey.from_string(order["mint"])
    owner = Pubkey.from_string(order["owner_pubkey"])
    remaining = int(order["remaining_raw"])
    amount = remaining
    user_usdc = ata(owner, USDC, TOKEN)
    keeper_ata = ata(keeper.pubkey(), mint, TOKEN_2022)

    build = None
    for _ in range(4):  # weekend: halve until price impact is within tolerance; the rest stays armed
        build = jup_build(mint, amount, keeper.pubkey(), rules["slippageBps"], user_usdc)
        impact_bps = Decimal(str(build.get("priceImpactPct") or 0)) * 10000
        if not rules["splitOnImpact"] or impact_bps <= rules["slippageBps"] or amount <= max(1, remaining // 8):
            break
        log(f"  weekend impact check: {impact_bps:.1f}bps > {rules['slippageBps']}bps at {amount}; halving to {amount // 2}")
        amount //= 2

    ixs = [from_jup_ix(i) for i in build["computeBudgetInstructions"]]
    ixs += [ix_create_ata_idempotent(keeper.pubkey(), keeper.pubkey(), mint, TOKEN_2022),
            ix_create_ata_idempotent(keeper.pubkey(), owner, USDC, TOKEN),
            ix_transfer_checked_as_delegate(Pubkey.from_string(order["token_account"]), mint, keeper_ata, keeper.pubkey(), amount, int(order["decimals"]))]
    ixs += [from_jup_ix(i) for i in build["setupInstructions"]]
    ixs.append(from_jup_ix(build["swapInstruction"]))
    if build.get("cleanupInstruction"):
        ixs.append(from_jup_ix(build["cleanupInstruction"]))
    ixs += [from_jup_ix(i) for i in build.get("otherInstructions", [])]
    alts = [AddressLookupTableAccount(Pubkey.from_string(k), [Pubkey.from_string(a) for a in v])
            for k, v in (build.get("addressesByLookupTableAddress") or {}).items()]

    def compile_and_sign(instructions):
        blockhash = rpc("getLatestBlockhash", [{"commitment": "confirmed"}])["value"]["blockhash"]
        msg = MessageV0.try_compile(keeper.pubkey(), instructions, alts, Hash.from_string(blockhash))
        tx = VersionedTransaction(msg, [keeper])
        return base64.b64encode(bytes(tx)).decode(), str(tx.signatures[0])

    raw, _ = compile_and_sign(ixs)
    sim = rpc("simulateTransaction", [raw, {"encoding": "base64", "commitment": "confirmed"}])["value"]
    if sim.get("err"):
        raise RuntimeError(f"simulation failed: {sim['err']} | {' / '.join((sim.get('logs') or [])[-4:])}")
    units = int(sim.get("unitsConsumed") or 400_000)
    raw, sig = compile_and_sign([ix_compute_limit(min(1_400_000, int(units * 1.2) + 20_000))] + ixs)
    log(f"  built: size={len(base64.b64decode(raw))}B units~{units} route={[s['swapInfo']['label'] for s in build['routePlan']]} "
        f"quote out={Decimal(build['outAmount']) / 10**USDC_DECIMALS:.4f} USDC for {amount} raw")
    return amount, raw, sig, build


def send_and_confirm(raw, sig):
    sent = rpc("sendTransaction", [raw, {"encoding": "base64", "skipPreflight": False, "maxRetries": 3}])
    log(f"  sent {sent}")
    for _ in range(30):
        time.sleep(2)
        st = signature_status(sig)
        if st == "confirmed":
            return
        if st == "failed":
            raise RuntimeError(f"transaction {sig} failed on chain")
    raise RuntimeError(f"transaction {sig} not confirmed within 60s")


def record_fill(o, sig, amount, out_raw, multiplier, session):
    o.setdefault("fills", []).append({"at": now_iso(), "signature": sig, "in_raw": str(amount), "out_usdc_raw": str(out_raw),
                                      "fill_price_usd": fill_price(out_raw, amount, int(o["decimals"]), multiplier), "session": session})
    o["remaining_raw"] = str(int(o["remaining_raw"]) - int(amount))
    assert int(o["remaining_raw"]) >= 0, "executed more than delegated"  # invariant 4
    o["fill_sig"], o["fill_price_usd"], o["filled_at"] = sig, o["fills"][-1]["fill_price_usd"], o["fills"][-1]["at"]
    o["status"] = "filled" if int(o["remaining_raw"]) == 0 else "armed"
    o["pending_sig"] = o["pending_amount_raw"] = o["pending_since"] = None
    o["failure_reason"] = None


# ---------------------------------------------------------------- run
def recover_executing(o, prices, session):
    """A previous run died after persisting `executing`. Decide from the chain, never from memory."""
    sig, amount = o.get("pending_sig"), o.get("pending_amount_raw")
    st = signature_status(sig) if sig else None
    if st == "confirmed":
        out = usdc_received(sig, Pubkey.from_string(o["owner_pubkey"]))
        mult = prices.get(o["mint"], (None, Decimal(1)))[1]
        record_fill(o, sig, int(amount), out if out is not None else 0, mult, session)
        return f"recovered: pending {sig} landed on chain; {o['status']}"
    if st == "failed":
        o["status"], o["failure_reason"] = "failed", f"pending transaction {sig} failed on chain"
    else:
        since = datetime.strptime(o.get("pending_since") or "1970-01-01T00:00:00Z", "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - since).total_seconds() < EXECUTING_TIMEOUT_S:
            return f"still executing: pending {sig} not visible yet; waiting"
        o["status"], o["failure_reason"] = "failed", f"pending transaction {sig} never landed"
    o["pending_sig"] = o["pending_amount_raw"] = o["pending_since"] = None
    return o["failure_reason"]


def run():
    session = FORCE_SESSION or session_state(datetime.now(timezone.utc))
    rules = REGIMES[session]
    store, sha = load_orders()
    open_orders = [o for o in store["orders"] if o["status"] in OPEN]
    log(f"run session={session}{' (forced)' if FORCE_SESSION else ''} confirmations={rules['confirmations']} "
        f"slippage={rules['slippageBps']}bps split={rules['splitOnImpact']} open_orders={len(open_orders)} dry_run={DRY_RUN}")
    if not open_orders:
        log("nothing to check")
        return
    keeper = load_keeper()

    # 3. one batched price read; a failure is a gap in observation, not evidence (invariant 2)
    try:
        prices = fetch_prices(sorted({o["mint"] for o in open_orders}))
    except Exception as exc:  # noqa: BLE001
        log(f"price read failed ({exc}); no breach counts change, nothing fires this run")
        return

    now = now_iso()
    # 4. evaluate every open order
    for o in open_orders:
        tag = f"[{o['id'][:8]} {o['ticker']} floor={o['floor_price_usd']}]"
        o["last_checked_at"], o["last_session"] = now, session
        try:
            if o["status"] == "executing":
                o["last_decision"] = recover_executing(o, prices, session)
                log(f"{tag} {o['last_decision']}")
                continue
            if o["status"] == "failed":
                o["status"] = "armed"  # retry path
            if o["mint"] not in prices:
                o["last_decision"] = "no price from Jupiter this run; breach count unchanged"
                log(f"{tag} {o['last_decision']}")
                continue
            price, _ = prices[o["mint"]]
            o["last_price_usd"] = str(price)
            if price > Decimal(o["floor_price_usd"]):
                o["breach_count"], o["status"] = 0, "armed"
                o["last_decision"] = f"hold: {price:.4f} is above floor {o['floor_price_usd']}"
            else:
                o["breach_count"] = int(o.get("breach_count", 0)) + 1
                need = rules["confirmations"]
                if o["breach_count"] >= need:
                    o["status"] = "triggered"
                    o["last_decision"] = f"triggered: breach {o['breach_count']}/{need} in {session}, {price:.4f} at or below floor"
                else:
                    o["last_decision"] = f"breach {o['breach_count']}/{need} in {session}: {price:.4f} at or below floor; waiting for confirmation"
            log(f"{tag} {o['last_decision']}")
        except Exception as exc:  # noqa: BLE001
            o["last_decision"] = f"error evaluating: {exc}"
            log(f"{tag} {o['last_decision']}")
    sha = save_orders(store, sha, f"keeper {now} evaluate")  # dry run still records evaluation; it only never sends

    # 6/7. execute triggered orders
    for o in [x for x in open_orders if x["status"] == "triggered"]:
        tag = f"[{o['id'][:8]} {o['ticker']} floor={o['floor_price_usd']}]"
        try:
            if keeper is None:
                o["last_decision"] = "triggered but KEEPER_SECRET_KEY is not configured; cannot execute"
                log(f"{tag} {o['last_decision']}")
                continue
            delegate, delegated = delegation_on_chain(o["token_account"])
            if delegate != str(keeper.pubkey()) or delegated < int(o["remaining_raw"]):
                o["status"], o["last_decision"] = "revoked", f"delegation no longer on chain (delegate={delegate}, delegated={delegated}); order closed"
                log(f"{tag} {o['last_decision']}")
                continue
            amount, raw, sig, _ = build_execution(o, keeper, rules)
            if DRY_RUN:
                o["last_decision"] = f"dry run: would execute {amount} raw, signature would be {sig}"
                log(f"{tag} {o['last_decision']}")
                continue
            o["status"], o["pending_sig"], o["pending_amount_raw"], o["pending_since"] = "executing", sig, str(amount), now_iso()
            o["last_decision"] = f"executing {amount} raw, pending {sig}"
            sha = save_orders(store, sha, f"keeper {now_iso()} executing {o['ticker']}")
            log(f"{tag} {o['last_decision']}")
            send_and_confirm(raw, sig)
            out = usdc_received(sig, Pubkey.from_string(o["owner_pubkey"]))
            record_fill(o, sig, amount, out if out is not None else 0, prices[o["mint"]][1], session)
            o["last_decision"] = f"{o['status']}: {amount} raw sold at {o['fill_price_usd']} USDC, signature {sig}"
            log(f"{tag} {o['last_decision']}")
        except Exception as exc:  # noqa: BLE001  an execution failure returns the order to the retry path
            if o["status"] == "executing":
                o["last_decision"] = recover_executing(o, prices, session)  # decide from the chain, not from the exception
                if o["status"] == "executing":
                    o["status"], o["failure_reason"] = "failed", f"execution error: {exc}"
            else:
                o["status"], o["failure_reason"] = "failed", f"execution error: {exc}"
            o["last_decision"] = o.get("failure_reason") or o["last_decision"]
            log(f"{tag} {o['last_decision']}")
    save_orders(store, sha, f"keeper {now_iso()} results")
    log("saved")


def main():
    try:
        run()
    except Exception as exc:  # noqa: BLE001  a broken schedule is worse than a missed check
        log(f"run aborted: {exc!r}")


if __name__ == "__main__":
    main()
