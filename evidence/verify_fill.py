#!/usr/bin/env python3
"""Read the weekend fill from Solana mainnet and compare it with what this repository claims. Stdlib only, no key.
Exits non-zero on any mismatch."""
import json, os, sys, urllib.request, datetime
D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "weekend-fill-2026-09-20")
order = json.load(open(f"{D}/order.json")); fill = order["fills"][0]
sig, owner, keeper = fill["signature"], order["owner_pubkey"], order["delegate"]
RPC = os.environ.get("RPC_URL", "https://api.mainnet-beta.solana.com")
body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "getTransaction", "params": [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0, "commitment": "finalized"}]}).encode()
tx = json.load(urllib.request.urlopen(urllib.request.Request(RPC, body, {"Content-Type": "application/json"})))["result"]
if not tx: print("FAIL transaction not found on chain:", sig); sys.exit(1)
ok = True
def check(name, cond, detail):
    global ok; ok &= bool(cond); print(("OK   " if cond else "FAIL "), name, "·", detail)
when = datetime.datetime.fromtimestamp(tx["blockTime"], datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
check("landed without error", tx["meta"]["err"] is None, f"slot {tx['slot']}")
check("block time is Sunday 2026-09-20 06:45 UTC", when.startswith("2026-09-20T06:45"), when)
signers = [a["pubkey"] for a in tx["transaction"]["message"]["accountKeys"] if a["signer"]]
check("signed only by the keeper", signers == [keeper], f"signers {signers}")
def bal(lst): return {(x["owner"], x["mint"]): int(x["uiTokenAmount"]["amount"]) for x in lst}
pre, post = bal(tx["meta"]["preTokenBalances"]), bal(tx["meta"]["postTokenBalances"])
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
got = post.get((owner, USDC), 0) - pre.get((owner, USDC), 0)
check("USDC delivered to the owner equals the recorded fill", got == int(fill["out_usdc_raw"]), f"{got} raw = {got/1e6:.6f} USDC")
sold = pre.get((owner, order["mint"]), 0) - post.get((owner, order["mint"]), 0)
check("NVDAx taken from the owner equals the recorded quantity", sold == int(fill["in_raw"]), f"{sold} raw")
check("keeper ended holding no USDC and no NVDAx", post.get((keeper, USDC), 0) == 0 and post.get((keeper, order["mint"]), 0) == 0, "keeper token balances after: 0 and 0")
print("ALL OK" if ok else "MISMATCH"); sys.exit(0 if ok else 1)
