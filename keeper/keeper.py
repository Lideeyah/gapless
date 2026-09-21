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
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from market_calendar import execution_session  # noqa: E402  holidays and half-days, execution only

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
with open(os.path.join(HERE, "assets.json")) as _f:
    NO_MARKET = json.load(_f)["no_market"]  # mints with no public market: evaluated under the `closed` regime


def regime_for(order, session):
    """A permanently closed asset has no session; everything else takes the run's session."""
    return "closed" if order["mint"] in NO_MARKET else session

RPC_URL = os.environ.get("RPC_URL") or "https://api.mainnet-beta.solana.com"
REPO = os.environ.get("GITHUB_REPOSITORY", "Lideeyah/gapless")
GH_TOKEN = os.environ.get("GITHUB_TOKEN")
DRY_RUN = os.environ.get("KEEPER_DRY_RUN") == "1"          # build + simulate, never send, never enter `executing`
FORCE_SESSION = os.environ.get("KEEPER_FORCE_SESSION")     # test hook: override the regime
LOCAL_ORDERS = os.environ.get("KEEPER_ORDERS_FILE")        # test hook: local file instead of GitHub
AUTHOR = {"name": os.environ.get("GIT_AUTHOR_NAME", "Lydia Solomon"),
          "email": os.environ.get("GIT_AUTHOR_EMAIL", "lydiasolomon137@gmail.com")}
BREACH_WINDOW_S = 30 * 60   # a confirmation sequence older than this is stale; "consecutive" means within a few cycles
MIN_KEEPER_LAMPORTS = 5_000_000  # 0.005 SOL: two ATA rents plus fees; below this the keeper declines to execute
ALLOWED_PROGRAMS = {"JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4", "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",
                    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA", "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
                    "11111111111111111111111111111111", "ComputeBudget111111111111111111111111111111"}

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


TERMINAL = ("filled", "revoked")


def merge_touched(fresh_orders, touched):
    """Re-apply only the orders this run changed onto a freshly loaded store. An order the app closed meanwhile
    (revoked) stays closed unless we already moved the chain for it (executing/filled)."""
    ours = {o["id"]: o for o in touched}
    out = []
    for f in fresh_orders:
        mine = ours.get(f["id"])
        if mine is None:
            out.append(f)
        elif f["status"] in TERMINAL and mine["status"] not in ("executing", "filled"):
            out.append(f)  # the app's terminal state wins over our stale evaluation
        else:
            out.append(mine)
    return out


def save_orders(store, sha, message, touched=None):
    """Commit the store. Returns the new sha. On a 409 (file moved), reload, re-apply only the orders this run
    touched (or all if unspecified), and retry once."""
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
        log("orders.json moved under us; reloading and re-applying only this run's changes once")
        fresh, fresh_sha = load_orders()
        fresh["orders"] = merge_touched(fresh["orders"], touched if touched is not None else store["orders"])
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
        if m in data and data[m].get("usdPrice") is not None and Decimal(str(data[m]["usdPrice"])) > 0:
            out[m] = (Decimal(str(data[m]["usdPrice"])), Decimal(str((data[m].get("scaledUiConfig") or {}).get("multiplier") or 1)))
    return out


def token_account_state(token_account):
    """(owner, delegate, delegated raw amount, balance raw amount) as the chain has it now; owner None if not a token account."""
    info = rpc("getAccountInfo", [token_account, {"encoding": "jsonParsed", "commitment": "confirmed"}])["value"]
    parsed = info["data"]["parsed"] if info and isinstance(info["data"], dict) else None
    if not parsed or parsed.get("type") != "account":
        return None, None, 0, 0
    p = parsed["info"]
    return p.get("owner"), p.get("delegate"), int((p.get("delegatedAmount") or {}).get("amount") or 0), int(p["tokenAmount"]["amount"])


def keeper_lamports(pubkey):
    return int(rpc("getBalance", [str(pubkey), {"commitment": "confirmed"}])["value"])


# ---------------------------------------------------------------- Token-2022 mint state (pre-trade guard)
# Decoded from the raw mint account bytes, not from a parser we do not control. Layout (spl-token-2022):
# mint base 82 bytes, padding to 165, account type byte at 165, then TLV entries of (type u16, len u16, data).
EXT_PERMANENT_DELEGATE, EXT_TRANSFER_HOOK, EXT_SCALED_UI, EXT_PAUSABLE = 12, 14, 25, 26
_mint_state_cache = {}  # per keeper cycle only; main() starts with an empty cache


def decode_mint_state(raw, now_ts):
    """Return the parts of a Token-2022 mint that can invalidate a trade."""
    st = {"multiplier": "1", "paused": False, "hook_program": None, "permanent_delegate": None, "extensions": []}
    i = 166
    while i + 4 <= len(raw):
        t, n = int.from_bytes(raw[i:i + 2], "little"), int.from_bytes(raw[i + 2:i + 4], "little")
        i += 4
        if t == 0:
            break
        body = raw[i:i + n]
        i += n
        st["extensions"].append(t)
        if t == EXT_SCALED_UI and n >= 56:  # authority 32, multiplier f64, new_multiplier_effective_timestamp i64, new_multiplier f64
            import struct
            mult, eff_ts, new_mult = struct.unpack_from("<dqd", body, 32)
            st["multiplier"] = repr(new_mult if eff_ts and now_ts >= eff_ts else mult)
            st["scaled_ui"] = {"multiplier": repr(mult), "new_multiplier": repr(new_mult), "new_multiplier_effective_at": eff_ts}
        elif t == EXT_PAUSABLE and n >= 33:  # authority 32, paused u8
            st["paused"] = body[32] != 0
        elif t == EXT_TRANSFER_HOOK and n >= 64:  # authority 32, program_id 32 (all zero = no hook)
            prog = body[32:64]
            st["hook_program"] = None if prog == bytes(32) else str(Pubkey(prog))
        elif t == EXT_PERMANENT_DELEGATE and n >= 32:
            st["permanent_delegate"] = str(Pubkey(body[:32]))
    return st


def mint_state(mint):
    """Current mint state from the same RPC the keeper uses. Cached only within this cycle. Raises on any failure."""
    if mint in _mint_state_cache:
        return _mint_state_cache[mint]
    info = rpc("getAccountInfo", [mint, {"encoding": "base64", "commitment": "confirmed"}])["value"]
    if not info or info.get("owner") != str(TOKEN_2022):
        raise RuntimeError(f"mint {mint} is not a Token-2022 mint account")
    st = decode_mint_state(base64.b64decode(info["data"][0]), int(time.time()))
    _mint_state_cache[mint] = st
    return st


SPLIT_MIN_LOG_RATIO = Decimal("0.01")  # a multiplier change under ~1% is dividend accrual by construction; no split is that small


def classify_multiplier_event(o, price):
    """Second reading after a multiplier change. Decide split vs accrual from the displayed price, then settle the floor.

    Displayed price = raw price / multiplier. At a split the displayed price moves by old/new; at a dividend accrual
    the raw price rises by the same factor the multiplier did and the displayed price does not move at all.
    """
    ev = o["multiplier_event"]
    ratio = Decimal(ev["old_multiplier"]) / Decimal(ev["new_multiplier"])
    pre = Decimal(ev["pre_price_usd"]) if ev.get("pre_price_usd") and Decimal(ev["pre_price_usd"]) > 0 else None
    old_floor = Decimal(o["floor_price_usd"])
    kind = "accrual"
    if abs(ratio.ln()) >= SPLIT_MIN_LOG_RATIO:
        if pre is None:
            kind = "split"  # no pre-change price to compare against: assume the dangerous case, which keeps the floor meaningful
        else:
            d_split = abs((price / (pre * ratio)).ln())
            d_flat = abs((price / pre).ln())
            kind = "split" if d_split < d_flat else "accrual"
    entry = {"at": now_iso(), "kind": kind, "old_multiplier": ev["old_multiplier"], "new_multiplier": ev["new_multiplier"],
             "pre_price_usd": ev.get("pre_price_usd"), "post_price_usd": str(price), "old_floor": str(old_floor), "new_floor": str(old_floor)}
    if kind == "split":
        new_floor = (old_floor * ratio).quantize(Decimal("0.000001"))
        o["floor_price_usd"], entry["new_floor"] = str(new_floor), str(new_floor)
    o.setdefault("rebases", []).append(entry)
    o["multiplier_event"] = None
    return entry


def guard_mint(o, tag):
    """Pre-trade mint guard. Returns None if trading may be evaluated, else the decision string.
    Order of checks: state readable -> multiplier unchanged (else note the change and skip) -> not paused -> no transfer hook."""
    try:
        st = mint_state(o["mint"])
    except Exception as exc:  # noqa: BLE001
        o["blocked"] = "mint_unreadable"
        return f"mint state unreadable ({exc}); nothing evaluated, nothing fired"
    stored = o.get("multiplier")
    if stored is None:  # orders armed before the guard existed: adopt the current multiplier
        o["multiplier"] = st["multiplier"]
    elif Decimal(stored) != Decimal(st["multiplier"]):
        # Cycle one of two: record the change, reset the breach count, evaluate nothing. The next reading decides
        # whether this was a split (rebase the floor) or a dividend accrual (leave the floor exactly where it was set).
        pending = o.get("multiplier_event")
        if pending:  # changed again before classification: keep the original baseline, compound the ratio
            o["multiplier_event"] = {**pending, "new_multiplier": st["multiplier"], "changes": int(pending.get("changes", 1)) + 1}
        else:
            o["multiplier_event"] = {"detected_at": now_iso(), "old_multiplier": stored, "new_multiplier": st["multiplier"],
                                     "pre_price_usd": o.get("last_price_usd"), "changes": 1}
        o["multiplier"], o["breach_count"] = st["multiplier"], 0
        if o["status"] == "triggered":
            o["status"] = "armed"  # a trigger counted before the change means nothing after it
        o["blocked"] = None
        return f"multiplier changed {stored} -> {st['multiplier']}; breach count reset; classifying on the next reading, nothing evaluated this cycle"
    if st["paused"]:
        o["blocked"] = "paused"
        return "mint is paused by the issuer; no route requested, nothing sold"
    if st["hook_program"]:
        o["blocked"] = "transfer_hook"
        return f"transfer hook {st['hook_program']} is enabled on the mint; swap not attempted"
    o["blocked"] = None
    return None


def jup_build(input_mint, amount, taker, slippage_bps, dest_usdc_ata):
    q = urllib.parse.urlencode({"inputMint": str(input_mint), "outputMint": str(USDC), "amount": str(amount), "taker": str(taker),
                                "slippageBps": str(slippage_bps), "destinationTokenAccount": str(dest_usdc_ata)})
    return http_json(f"{BUILD_URL}?{q}")


def usdc_received(sig, owner, attempts=6):
    """Real USDC delta for the owner from the confirmed transaction's token balances, or None if not yet servable."""
    tx = None
    for i in range(attempts):
        tx = rpc("getTransaction", [sig, {"encoding": "json", "maxSupportedTransactionVersion": 0, "commitment": "confirmed"}])
        if tx or i == attempts - 1:
            break
        time.sleep(1.5)
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
def check_build(build, keeper_ata, user_usdc):
    """Refuse to sign anything the keeper does not understand: unknown programs, or a swap that does not involve
    the keeper's source account and the owner's USDC account."""
    ixs = build["computeBudgetInstructions"] + build["setupInstructions"] + [build["swapInstruction"]] + \
          ([build["cleanupInstruction"]] if build.get("cleanupInstruction") else []) + build.get("otherInstructions", [])
    bad = sorted({ix["programId"] for ix in ixs if ix["programId"] not in ALLOWED_PROGRAMS})
    if bad:
        raise RuntimeError(f"refusing to sign: unexpected program(s) in Jupiter build {bad}")
    keys = {a["pubkey"] for a in build["swapInstruction"]["accounts"]}
    if str(keeper_ata) not in keys or str(user_usdc) not in keys:
        raise RuntimeError("refusing to sign: swap instruction does not reference the keeper's source account and the owner's USDC account")
    for ix in ixs:
        for a in ix["accounts"]:
            if a["isSigner"] and a["pubkey"] != str(build["_taker"]):
                raise RuntimeError(f"refusing to sign: build asks for a signer other than the keeper ({a['pubkey']})")


def build_execution(order, keeper, rules, sell_cap):
    """Size (never above sell_cap; weekend split by impact), build, verify, simulate and sign.
    Returns (amount_raw, signed_b64, signature, last_valid_block_height, build)."""
    mint = Pubkey.from_string(order["mint"])
    owner = Pubkey.from_string(order["owner_pubkey"])
    remaining = min(int(order["remaining_raw"]), int(sell_cap))
    amount = remaining
    user_usdc = ata(owner, USDC, TOKEN)
    keeper_ata = ata(keeper.pubkey(), mint, TOKEN_2022)

    build, impact_bps = None, Decimal(0)
    for _ in range(4):  # weekend: halve until price impact is within tolerance
        build = jup_build(mint, amount, keeper.pubkey(), rules["slippageBps"], user_usdc)
        impact_bps = Decimal(str(build.get("priceImpactPct") or 0)) * 10000
        if not rules["splitOnImpact"] or impact_bps <= rules["slippageBps"]:
            break
        if amount <= max(1, remaining // 8):
            raise RuntimeError(f"price impact {impact_bps:.1f}bps still exceeds {rules['slippageBps']}bps at one eighth of the position; not selling into this book")
        log(f"  weekend impact check: {impact_bps:.1f}bps > {rules['slippageBps']}bps at {amount}; halving to {amount // 2}")
        amount //= 2
    build["_taker"] = keeper.pubkey()
    check_build(build, keeper_ata, user_usdc)

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
        bh = rpc("getLatestBlockhash", [{"commitment": "confirmed"}])["value"]
        msg = MessageV0.try_compile(keeper.pubkey(), instructions, alts, Hash.from_string(bh["blockhash"]))
        tx = VersionedTransaction(msg, [keeper])
        raw = base64.b64encode(bytes(tx)).decode()
        if len(bytes(tx)) > 1232:
            raise RuntimeError(f"transaction is {len(bytes(tx))} bytes, over the 1232 byte limit")
        return raw, str(tx.signatures[0]), int(bh["lastValidBlockHeight"])

    raw, _, _ = compile_and_sign(ixs)
    sim = rpc("simulateTransaction", [raw, {"encoding": "base64", "commitment": "confirmed"}])["value"]
    if sim.get("err"):
        raise RuntimeError(f"simulation failed: {sim['err']} | {' / '.join((sim.get('logs') or [])[-4:])}")
    units = int(sim.get("unitsConsumed") or 400_000)
    raw, sig, lvbh = compile_and_sign([ix_compute_limit(min(1_400_000, int(units * 1.2) + 20_000))] + ixs)
    log(f"  built: size={len(base64.b64decode(raw))}B units~{units} impact={impact_bps:.1f}bps route={[s['swapInfo']['label'] for s in build['routePlan']]} "
        f"quote out={Decimal(build['outAmount']) / 10**USDC_DECIMALS:.4f} USDC for {amount} raw")
    return amount, raw, sig, lvbh, build


def send_and_confirm(raw, sig):
    sent = rpc("sendTransaction", [raw, {"encoding": "base64", "skipPreflight": False, "preflightCommitment": "confirmed", "maxRetries": 3}])
    log(f"  sent {sent}")
    for _ in range(30):
        time.sleep(2)
        st = signature_status(sig)
        if st == "confirmed":
            return
        if st == "failed":
            raise RuntimeError(f"transaction {sig} failed on chain")
    raise RuntimeError(f"transaction {sig} not confirmed within 60s; left executing for recovery")


def record_fill(o, sig, amount, out_raw, multiplier, session):
    o.setdefault("fills", []).append({"at": now_iso(), "signature": sig, "in_raw": str(amount), "out_usdc_raw": str(out_raw),
                                      "fill_price_usd": fill_price(out_raw, amount, int(o["decimals"]), multiplier), "session": session})
    o["remaining_raw"] = str(int(o["remaining_raw"]) - int(amount))
    assert int(o["remaining_raw"]) >= 0, "executed more than delegated"  # invariant 4
    o["fill_sig"], o["fill_price_usd"], o["filled_at"] = sig, o["fills"][-1]["fill_price_usd"], o["fills"][-1]["at"]
    o["status"] = "filled" if int(o["remaining_raw"]) == 0 else "armed"
    o["breach_count"] = 0  # a remainder must earn its own confirmations before the next slice
    o["pending_sig"] = o["pending_amount_raw"] = o["pending_since"] = o["pending_last_valid_block_height"] = o["pending_multiplier"] = None
    o["failure_reason"] = None


def settle_pending(o, session):
    """Decide a pending signature from the chain, never from memory. Returns the decision string.
    Keeps `executing` while the outcome is genuinely unknown; only a dead blockhash plus no trace lets it fail."""
    sig, amount = o.get("pending_sig"), o.get("pending_amount_raw")
    if not sig:
        o["status"], o["failure_reason"] = "failed", "executing with no pending signature on record"
        return o["failure_reason"]
    st = signature_status(sig)
    if st == "confirmed":
        out = usdc_received(sig, Pubkey.from_string(o["owner_pubkey"]))
        if out is None:
            return f"landed: {sig} is confirmed but its balance change is not servable yet; fill not recorded until it is"
        record_fill(o, sig, int(amount), out, Decimal(o.get("pending_multiplier") or 1), session)
        return f"{o['status']}: {amount} raw sold at {o['fill_price_usd']} USDC, signature {sig}"
    if st == "failed":
        o["status"], o["failure_reason"] = "failed", f"transaction {sig} failed on chain"
    else:
        lvbh = int(o.get("pending_last_valid_block_height") or 0)
        height = int(rpc("getBlockHeight", [{"commitment": "confirmed"}]))
        if lvbh and height <= lvbh:
            return f"still executing: {sig} not visible yet, blockhash valid until height {lvbh} (now {height})"
        tx = rpc("getTransaction", [sig, {"encoding": "json", "maxSupportedTransactionVersion": 0, "commitment": "confirmed"}])
        if tx:
            return "landed late: transaction is servable; settling next cycle"  # signature_status lagged; try again
        o["status"], o["failure_reason"] = "failed", f"transaction {sig} never landed (blockhash expired at height {lvbh})"
    o["pending_sig"] = o["pending_amount_raw"] = o["pending_since"] = o["pending_last_valid_block_height"] = o["pending_multiplier"] = None
    return o["failure_reason"]


def order_tag(o):
    return f"[{o['id'][:8]} {o['ticker']} floor={o['floor_price_usd']}]"


def count_breach(o, session, now_ts):
    """Consecutive readings at or below the floor, within one regime and within BREACH_WINDOW_S.
    A missing reading never resets the count; a regime change or a stale sequence does."""
    last_at = o.get("breach_last_at")
    last_ts = datetime.strptime(last_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp() if last_at else None
    stale = last_ts is not None and now_ts - last_ts > BREACH_WINDOW_S
    if o.get("breach_session") != session or stale or not o.get("breach_count"):
        o["breach_count"] = 0
    o["breach_count"] = int(o["breach_count"]) + 1
    o["breach_session"], o["breach_last_at"] = session, now_iso()


def run():
    _mint_state_cache.clear()  # mint state is never carried across cycles
    now_utc = datetime.now(timezone.utc)
    labelled = FORCE_SESSION or session_state(now_utc)
    session = labelled if FORCE_SESSION else execution_session(now_utc, labelled)
    rules = REGIMES[session]
    store, sha = load_orders()
    open_orders = [o for o in store["orders"] if o["status"] in OPEN]
    log(f"run session={session}{' (forced)' if FORCE_SESSION else ''}{'' if session == labelled else f' (calendar; recorder labels {labelled})'} "
        f"confirmations={rules['confirmations']} slippage={rules['slippageBps']}bps split={rules['splitOnImpact']} open_orders={len(open_orders)} dry_run={DRY_RUN}")
    if not open_orders:
        log("nothing to check")
        return
    keeper = load_keeper()
    touched = []
    now = now_iso()

    # 1. settle anything left executing by an earlier run, from the chain, before anything else and without needing a price
    for o in [x for x in open_orders if x["status"] == "executing"]:
        o["last_checked_at"], o["last_session"] = now, session
        try:
            o["last_decision"] = settle_pending(o, session)
        except Exception as exc:  # noqa: BLE001
            o["last_decision"] = f"could not settle pending transaction this cycle: {exc}"
        log(f"{order_tag(o)} {o['last_decision']}")
        touched.append(o)

    # 2. one batched price read; a failure is a gap in observation, not evidence (invariant 2)
    try:
        prices = fetch_prices(sorted({o["mint"] for o in open_orders}))
    except Exception as exc:  # noqa: BLE001
        log(f"price read failed ({exc}); no breach counts change, nothing fires this run")
        if touched:
            save_orders(store, sha, f"keeper {now} settle", touched)
        return

    # 3. evaluate every open order that was not settled or left executing above (a settled order waits for the next cycle)
    settled_ids = {x["id"] for x in touched}
    for o in [x for x in open_orders if x["id"] not in settled_ids and x["status"] not in TERMINAL]:
        o_session = regime_for(o, session)
        o_rules = REGIMES[o_session]
        o["last_checked_at"], o["last_session"] = now, o_session
        touched.append(o)
        tag = order_tag(o)
        try:
            if o["status"] == "failed":
                o["status"] = "armed"  # retry path
            if keeper and o.get("delegate") and o["delegate"] != str(keeper.pubkey()):
                o["last_decision"] = f"delegate mismatch: order names {o['delegate']}, this keeper is {keeper.pubkey()}; not evaluated"
                log(f"{tag} {o['last_decision']}")
                continue
            blocked = guard_mint(o, tag)
            if blocked:
                o["last_decision"] = blocked
                log(f"{tag} {o['last_decision']}")
                continue
            if o["mint"] not in prices:
                o["last_decision"] = "no usable price from Jupiter this run; breach count unchanged"
                log(f"{tag} {o['last_decision']}")
                continue
            price, _ = prices[o["mint"]]
            if o.get("multiplier_event"):
                ev = classify_multiplier_event(o, price)
                log(f"{tag} multiplier change classified as {ev['kind']}: price {ev['pre_price_usd']} -> {ev['post_price_usd']} against ratio "
                    f"{Decimal(ev['old_multiplier']) / Decimal(ev['new_multiplier']):.6f}; floor {ev['old_floor']} -> {ev['new_floor']}")
                tag = order_tag(o)
            o["last_price_usd"] = str(price)
            if price > Decimal(o["floor_price_usd"]):
                o["breach_count"], o["status"] = 0, "armed"
                o["last_decision"] = f"hold: {price:.4f} is above floor {o['floor_price_usd']}"
            else:
                count_breach(o, o_session, now_utc.timestamp())
                need = o_rules["confirmations"]
                if o["breach_count"] >= need:
                    o["status"] = "triggered"
                    o["last_decision"] = f"triggered: breach {o['breach_count']}/{need} in {o_session}, {price:.4f} at or below floor"
                else:
                    o["last_decision"] = f"breach {o['breach_count']}/{need} in {o_session}: {price:.4f} at or below floor; waiting for confirmation"
            log(f"{tag} {o['last_decision']}")
        except Exception as exc:  # noqa: BLE001
            o["last_decision"] = f"error evaluating: {exc}"
            log(f"{tag} {o['last_decision']}")
    sha = save_orders(store, sha, f"keeper {now} evaluate", touched)

    # 4. execute triggered orders
    for o in [x for x in open_orders if x["status"] == "triggered"]:
        tag = order_tag(o)
        o_session = regime_for(o, session)
        o_rules = REGIMES[o_session]
        asset = NO_MARKET.get(o["mint"])
        try:
            if asset and not asset.get("executable", False):
                o["last_decision"] = f"triggered under the closed regime but execution is not enabled for {asset['ticker']}: {asset['reason']}"
                log(f"{tag} {o['last_decision']}")
                continue
            if keeper is None:
                o["last_decision"] = "triggered but KEEPER_SECRET_KEY is not configured; cannot execute"
                log(f"{tag} {o['last_decision']}")
                continue
            if o.get("blocked"):
                o["last_decision"] = f"not executed: {o['blocked']}"
                log(f"{tag} {o['last_decision']}")
                continue
            owner_on_chain, delegate, delegated, balance = token_account_state(o["token_account"])
            if owner_on_chain != o["owner_pubkey"]:
                o["status"], o["last_decision"] = "revoked", f"token account owner on chain is {owner_on_chain}, not the order's owner; order closed"
                log(f"{tag} {o['last_decision']}")
                continue
            if delegate != str(keeper.pubkey()) or delegated <= 0:
                o["status"], o["last_decision"] = "revoked", f"delegation no longer on chain (delegate={delegate}, delegated={delegated}); order closed"
                log(f"{tag} {o['last_decision']}")
                continue
            if balance <= 0:
                o["blocked"], o["last_decision"] = "no_balance", "token account balance is zero; nothing to sell (tokens moved out after arming)"
                log(f"{tag} {o['last_decision']}")
                continue
            if keeper_lamports(keeper.pubkey()) < MIN_KEEPER_LAMPORTS:
                o["last_decision"] = "keeper wallet is below the fee reserve; not executing until it is funded"
                log(f"{tag} {o['last_decision']}")
                continue
            sell_cap = min(delegated, balance)  # never more than delegated, never more than is there
            amount, raw, sig, lvbh, _ = build_execution(o, keeper, o_rules, sell_cap)
            if DRY_RUN:
                o["last_decision"] = f"dry run: would execute {amount} raw (cap {sell_cap}), signature would be {sig}"
                log(f"{tag} {o['last_decision']}")
                continue
            o["status"], o["pending_sig"], o["pending_amount_raw"], o["pending_since"] = "executing", sig, str(amount), now_iso()
            o["pending_last_valid_block_height"], o["pending_multiplier"] = lvbh, mint_state(o["mint"])["multiplier"]
            o["last_decision"] = f"executing {amount} raw, pending {sig}"
            sha = save_orders(store, sha, f"keeper {now_iso()} executing {o['ticker']}", touched)
            log(f"{tag} {o['last_decision']}")
            send_and_confirm(raw, sig)
            o["last_decision"] = settle_pending(o, o_session)
            log(f"{tag} {o['last_decision']}")
        except Exception as exc:  # noqa: BLE001
            if o["status"] == "executing":
                # the transaction may be in flight: ask the chain now; if it cannot say, stay executing for the next cycle
                try:
                    o["last_decision"] = settle_pending(o, session) + f" (after: {exc})"
                except Exception as exc2:  # noqa: BLE001
                    o["last_decision"] = f"execution outcome unknown ({exc}; settle: {exc2}); left executing for chain settlement"
            else:
                o["status"], o["failure_reason"] = "failed", f"execution error: {exc}"
                o["last_decision"] = o["failure_reason"]
            log(f"{tag} {o['last_decision']}")
    save_orders(store, sha, f"keeper {now_iso()} results", touched)
    log("saved")


def main():
    try:
        run()
    except Exception as exc:  # noqa: BLE001  a broken schedule is worse than a missed check
        log(f"run aborted: {exc!r}")


if __name__ == "__main__":
    main()
