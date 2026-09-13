#!/usr/bin/env python3
"""Gapless keeper: checks armed orders against live prices and executes regime-aware stops.

Runs on GitHub Actions every 5 minutes (best-effort cron). Reuses recorder.session_state() so the
keeper and the recorder can never disagree about what "weekend" means. Regime thresholds live in
regimes.json. Every order checked is logged with the decision and the reason.

Execution is one atomic v0 transaction signed only by the keeper (the SPL delegate):
  1. create keeper's xStock ATA + user's USDC ATA if missing (idempotent)
  2. transfer_checked the delegated amount from the user's token account to the keeper's ATA
  3. Jupiter swap (api.jup.ag/swap/v2/build) with destinationTokenAccount = the user's USDC ATA
If any step fails the whole transaction reverts, so the keeper never ends up holding the asset.
"""
import base64, json, os, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from recorder import PRICE_URL, session_state  # noqa: E402  single implementation of session state

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

RPC_URL = os.environ.get("RPC_URL", "https://api.mainnet-beta.solana.com")
REPO = os.environ.get("GITHUB_REPOSITORY", "Lideeyah/gapless")
GH_TOKEN = os.environ.get("GITHUB_TOKEN")
DRY_RUN = os.environ.get("KEEPER_DRY_RUN") == "1"          # build + simulate, never send
FORCE_SESSION = os.environ.get("KEEPER_FORCE_SESSION")     # test hook: override the regime
LOCAL_ORDERS = os.environ.get("KEEPER_ORDERS_FILE")        # test hook: local file instead of GitHub
AUTHOR = {"name": os.environ.get("GIT_AUTHOR_NAME", "Lydia Solomon"),
          "email": os.environ.get("GIT_AUTHOR_EMAIL", "lydiasolomon137@gmail.com")}

USDC = Pubkey.from_string("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v")
USDC_DECIMALS = 6
TOKEN_2022 = Pubkey.from_string("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb")
TOKEN = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
ATA_PROGRAM = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
SYSTEM = Pubkey.from_string("11111111111111111111111111111111")
COMPUTE_BUDGET = Pubkey.from_string("ComputeBudget111111111111111111111111111111")
BUILD_URL = "https://api.jup.ag/swap/v2/build"


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg):
    print(f"{now_iso()} {msg}", flush=True)


def http_json(url, body=None, headers=None, method=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"User-Agent": "gapless-keeper", "Content-Type": "application/json", **(headers or {})})
    for attempt in range(4):  # keyless Jupiter is 0.5 RPS; back off on 429 instead of failing the run
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
    meta = http_json(f"https://api.github.com/repos/{REPO}/contents/data/orders.json", headers=gh_headers())
    return json.loads(base64.b64decode(meta["content"])), meta["sha"]


def save_orders(store, sha, touched):
    body = json.dumps(store, indent=2) + "\n"
    if LOCAL_ORDERS:
        with open(LOCAL_ORDERS, "w") as f:
            f.write(body)
        return
    payload = {"message": f"keeper {now_iso()}", "content": base64.b64encode(body.encode()).decode(),
               "sha": sha, "committer": AUTHOR, "author": AUTHOR}
    try:
        http_json(f"https://api.github.com/repos/{REPO}/contents/data/orders.json", payload, gh_headers(), "PUT")
    except urllib.error.HTTPError as exc:
        if exc.code != 409:
            raise
        log("orders.json moved under us; reloading and re-applying this run's changes once")
        fresh, fresh_sha = load_orders()
        by_id = {o["id"]: o for o in touched}
        fresh["orders"] = [by_id.get(o["id"], o) for o in fresh["orders"]]
        payload["content"] = base64.b64encode((json.dumps(fresh, indent=2) + "\n").encode()).decode()
        payload["sha"] = fresh_sha
        http_json(f"https://api.github.com/repos/{REPO}/contents/data/orders.json", payload, gh_headers(), "PUT")


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
    """{mint: (usdPrice, uiMultiplier)} from Jupiter Price API v3 (same source as the recorder)."""
    data = http_json(PRICE_URL + ",".join(mints))
    return {m: (float(data[m]["usdPrice"]), float((data[m].get("scaledUiConfig") or {}).get("multiplier") or 1.0))
            for m in mints if m in data and "usdPrice" in data[m]}


def delegation_on_chain(token_account):
    """(delegate pubkey str or None, delegated raw amount) as the chain currently has it."""
    info = rpc("getAccountInfo", [token_account, {"encoding": "jsonParsed", "commitment": "confirmed"}])["value"]
    if not info:
        return None, 0
    parsed = info["data"]["parsed"] if isinstance(info["data"], dict) else None
    if not parsed or parsed.get("type") != "account":
        return None, 0
    p = parsed["info"]
    return p.get("delegate"), int((p.get("delegatedAmount") or {}).get("amount") or 0)


def jup_build(input_mint, amount, taker, slippage_bps, dest_usdc_ata):
    q = urllib.parse.urlencode({"inputMint": str(input_mint), "outputMint": str(USDC), "amount": str(amount),
                                "taker": str(taker), "slippageBps": str(slippage_bps),
                                "destinationTokenAccount": str(dest_usdc_ata)})
    return http_json(f"{BUILD_URL}?{q}")


def usdc_received(sig, owner):
    """Real USDC delta for the user's wallet from the confirmed transaction's token balances."""
    tx = rpc("getTransaction", [sig, {"encoding": "json", "maxSupportedTransactionVersion": 0, "commitment": "confirmed"}])
    if not tx:
        return None
    def total(entries):
        return sum(int(e["uiTokenAmount"]["amount"]) for e in entries
                   if e.get("owner") == str(owner) and e.get("mint") == str(USDC))
    return total(tx["meta"].get("postTokenBalances", [])) - total(tx["meta"].get("preTokenBalances", []))


# ---------------------------------------------------------------- execution
def execute(order, keeper, rules, multiplier):
    mint = Pubkey.from_string(order["mint"])
    owner = Pubkey.from_string(order["owner"])
    remaining = int(order["remainingRaw"])
    amount = remaining
    user_usdc = ata(owner, USDC, TOKEN)
    keeper_ata = ata(keeper.pubkey(), mint, TOKEN_2022)

    build = None
    for _ in range(4):  # weekend: shrink size until price impact is within tolerance, execute the rest later
        build = jup_build(mint, amount, keeper.pubkey(), rules["slippageBps"], user_usdc)
        impact_bps = float(build.get("priceImpactPct") or 0) * 10000
        if not rules["splitOnImpact"] or impact_bps <= rules["slippageBps"] or amount <= max(1, remaining // 8):
            break
        log(f"  price impact {impact_bps:.0f}bps exceeds {rules['slippageBps']}bps; halving size {amount} -> {amount // 2}")
        amount //= 2

    ixs = [from_jup_ix(i) for i in build["computeBudgetInstructions"]]
    ixs += [ix_create_ata_idempotent(keeper.pubkey(), keeper.pubkey(), mint, TOKEN_2022),
            ix_create_ata_idempotent(keeper.pubkey(), owner, USDC, TOKEN),
            ix_transfer_checked_as_delegate(Pubkey.from_string(order["tokenAccount"]), mint, keeper_ata,
                                            keeper.pubkey(), amount, int(order["decimals"]))]
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
        return base64.b64encode(bytes(VersionedTransaction(msg, [keeper]))).decode()

    raw = compile_and_sign(ixs)
    sim = rpc("simulateTransaction", [raw, {"encoding": "base64", "commitment": "confirmed"}])["value"]
    if sim.get("err"):
        raise RuntimeError(f"simulation failed: {sim['err']} | {' / '.join((sim.get('logs') or [])[-4:])}")
    units = int(sim.get("unitsConsumed") or 400_000)
    raw = compile_and_sign([ix_compute_limit(min(1_400_000, int(units * 1.2) + 20_000))] + ixs)
    log(f"  built tx: size={len(base64.b64decode(raw))}B units~{units} route={[s['swapInfo']['label'] for s in build['routePlan']]} "
        f"quote out={int(build['outAmount']) / 10**USDC_DECIMALS:.4f} USDC")
    if DRY_RUN:
        return None

    sig = rpc("sendTransaction", [raw, {"encoding": "base64", "skipPreflight": False, "maxRetries": 3}])
    log(f"  sent {sig}")
    for _ in range(30):
        time.sleep(2)
        st = rpc("getSignatureStatuses", [[sig]])["value"][0]
        if st and st.get("confirmationStatus") in ("confirmed", "finalized"):
            if st.get("err"):
                raise RuntimeError(f"transaction {sig} failed on chain: {st['err']}")
            break
    else:
        raise RuntimeError(f"transaction {sig} not confirmed within 60s")

    out_raw = usdc_received(sig, owner)
    if out_raw is None:
        out_raw = int(build["outAmount"])
    ui_qty = amount / 10 ** int(order["decimals"]) * multiplier
    return {"at": now_iso(), "signature": sig, "inRaw": str(amount), "outUsdcRaw": str(out_raw),
            "fillPriceUsd": (out_raw / 10 ** USDC_DECIMALS) / ui_qty if ui_qty else 0}


# ---------------------------------------------------------------- main loop
def main():
    session = FORCE_SESSION or session_state(datetime.now(timezone.utc))
    rules = REGIMES[session]
    store, sha = load_orders()
    open_orders = [o for o in store["orders"] if o["status"] in ("armed", "partial")]
    log(f"run session={session}{' (forced)' if FORCE_SESSION else ''} confirmations={rules['confirmations']} "
        f"slippage={rules['slippageBps']}bps split={rules['splitOnImpact']} open_orders={len(open_orders)} dry_run={DRY_RUN}")
    if not open_orders:
        log("nothing to check")
        return

    keeper = load_keeper()
    prices = fetch_prices(sorted({o["mint"] for o in open_orders}))
    touched = []
    for o in open_orders:
        tag = f"[{o['id'][:8]} {o['ticker']} floor={o['floorUsd']}]"
        check = {"at": now_iso(), "session": session, "price": None, "decision": ""}
        try:
            if o["mint"] not in prices:
                o["lastCheck"] = {**check, "decision": "no price from Jupiter this run; nothing changed"}
                log(f"{tag} {o['lastCheck']['decision']}")
                continue
            price, multiplier = prices[o["mint"]]
            check["price"] = price
            delegate, delegated = delegation_on_chain(o["tokenAccount"]) if keeper else (None, 0)
            if keeper and (delegate != str(keeper.pubkey()) or delegated < int(o["remainingRaw"])):
                o["status"] = "cancelled"
                o["lastCheck"] = {**check, "decision": f"delegation no longer on chain (delegate={delegate}, delegated={delegated}); order closed"}
                log(f"{tag} {o['lastCheck']['decision']}")
                continue
            if price > float(o["floorUsd"]):
                o["breachCount"] = 0
                o["lastCheck"] = {**check, "decision": f"hold: {price:.4f} is above floor {o['floorUsd']}"}
                log(f"{tag} {o['lastCheck']['decision']}")
                continue
            o["breachCount"] = int(o.get("breachCount", 0)) + 1
            need = rules["confirmations"]
            if o["breachCount"] < need:
                o["lastCheck"] = {**check, "decision": f"breach {o['breachCount']}/{need} in {session}: {price:.4f} at or below floor; waiting for confirmation"}
                log(f"{tag} {o['lastCheck']['decision']}")
                continue
            if keeper is None:
                o["lastCheck"] = {**check, "decision": f"breach {o['breachCount']}/{need} confirmed but KEEPER_SECRET_KEY is not configured; cannot execute"}
                log(f"{tag} {o['lastCheck']['decision']}")
                continue
            log(f"{tag} breach {o['breachCount']}/{need} confirmed in {session}: {price:.4f} <= floor; executing")
            fill = execute(o, keeper, rules, multiplier)
            if fill is None:
                o["lastCheck"] = {**check, "decision": f"dry run: would execute at ~{price:.4f}"}
                log(f"{tag} {o['lastCheck']['decision']}")
                continue
            o.setdefault("fills", []).append({**fill, "session": session})
            o["remainingRaw"] = str(int(o["remainingRaw"]) - int(fill["inRaw"]))
            o["status"] = "fired" if int(o["remainingRaw"]) == 0 else "partial"
            o["breachCount"] = 0
            o["lastCheck"] = {**check, "decision": f"{o['status']}: filled {fill['inRaw']} raw at {fill['fillPriceUsd']:.4f} USDC, sig {fill['signature']}"}
            log(f"{tag} {o['lastCheck']['decision']}")
        except Exception as exc:  # noqa: BLE001 - one bad order must not stop the others
            o["lastCheck"] = {**check, "decision": f"error: {exc}"}
            log(f"{tag} {o['lastCheck']['decision']}")
        finally:
            touched.append(o)

    save_orders(store, sha, touched)
    log(f"saved {len(touched)} order(s)")


if __name__ == "__main__":
    main()
