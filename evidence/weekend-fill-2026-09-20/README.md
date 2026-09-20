# Weekend fill, 2026-09-20 06:45:05 UTC (Sunday)

A stop-loss fill executed on Solana mainnet while the NYSE was closed for the weekend. Everything in this
directory was captured on 2026-09-20T17:04:37Z from the chain and from this repository's own history, and is frozen
here so that no later branch work, merge or rebase can change it. Nothing is interpreted; the files are
the raw records.

| file | what it is |
|---|---|
| `transaction.json` | `getTransaction` for the fill, finalized, as the RPC returned it: one v0 transaction signed only by the keeper |
| `order.json` | the order's final record from `data/orders.json` |
| `keeper-log.json` | every version of the order the keeper committed from arming (2026-09-19 09:43 UTC) to the fill, one entry per commit (257 entries, every keeper cycle across the window), with the decision it wrote |
| `recorder-nvdax-…csv` | the recorder's NVDAx rows from Friday 19:30 UTC (before the close) through Sunday 17:00 UTC, verbatim from `data/prices.csv` |
| `wallet.json` | the owner's token balances before and after, from the transaction's own balance deltas, plus the live balances at capture time |

## The sequence, from `keeper-log.json`

| keeper commit | reading | decision |
|---|---|---|
| 2026-09-20T06:30:01Z | 220.6214 | hold: 220.6215 is above floor 220.50 |
| 2026-09-20T06:35:01Z | 220.4223 | breach 1/3 in weekend: 220.4223 at or below floor; waiting for confirmation |
| 2026-09-20T06:40:00Z | 220.4941 | breach 2/3 in weekend: 220.4942 at or below floor; waiting for confirmation |
| 2026-09-20T06:45:03Z | 220.4719 | triggered: breach 3/3 in weekend, 220.4720 at or below floor |
| 2026-09-20T06:45:05Z | 220.4719 | executing 450595 raw, pending 3MKesA6YBeXG8NtofcazcEr1XWXKmvMBJ3oHHqPQd85pySN7UrsVCajKfXYz |
| 2026-09-20T06:45:08Z | 220.4719 | filled: 450595 raw sold at 220.556225 USDC, signature 3MKesA6YBeXG8NtofcazcEr1XWXKmvMBJ3oH |

Regime: `weekend` (three consecutive readings at or below the floor, 300 bps slippage cap, split on impact).
Floor 220.50 USDC. Sold 450595 raw units (0.00450595 NVDAx displayed) for 995506 raw USDC (0.995506), a
fill price of 220.556225 USDC per displayed token. Proceeds landed in the owner's USDC account and nowhere else.

The recorder's own 06:35 row reads 220.60, above the floor, because it read Jupiter ten seconds after the
keeper's 06:35 read of 220.42; the keeper's reads are the ones the decision rests on, and each is committed
with its decision. This is stated here so that the two files are read together, not against each other.

Signature: `3MKesA6YBeXG8NtofcazcEr1XWXKmvMBJ3oHHqPQd85pySN7UrsVCajKfXYzfZf4uFxVVoTKKBtiaPgKq5LExbEB`
Owner: `8iHARjAovzK2LGpXzADqUTUE4ocdssxzPRfgT3CuTuvf`
Keeper (signer): `HbJ7XbcY2VsunoeK9v6o3FwKGvwfTBQ7horU9d3fonKs`

## Hashes (SHA-256)

- `keeper-log.json` `c9ef920a92aa38cf1d4309a11fbdabd34c1f7ab71ef65f608ca31fcdec56b8be`
- `order.json` `5828d55964c2f6591bdd279fffc931656195ac00752d13bb37c837fb6c358323`
- `recorder-nvdax-2026-09-18T19-30Z_to_2026-09-20T17-00Z.csv` `6d67576e165684f8df58bbd594e72c64f70be9859c15c3f57717ee14f116710e`
- `transaction.json` `6c8c69c0755809da45cac8e16947a31349e029868db0e2ed23b95dd5d599fe97`
- `wallet.json` `e24e64828326aaa1a0eb654813fd2dc1e05c2b4f18398201513e74ca205a040d`
