# Gapless

Gapless is a stop loss and take profit for tokenised US stocks on Solana: it sells an xStock to USDC when its on-chain price crosses a floor or a ceiling, at any hour, including the 135 hours a week the NYSE is closed.

## What is live right now

Read from Solana mainnet and this repository on 2026-09-22 at 12:10 UTC.

**A stop that filled on a Sunday.** On 2026-09-20 at 06:45:05 UTC, 35 hours after Friday's close, the keeper sold 450,595 raw NVDAx (0.00450595 displayed) for 0.995506 USDC at 220.556225 per token against a floor of 220.50, under weekend rules, after three consecutive readings at or below the floor at 06:35, 06:40 and 06:45. The transaction was signed only by the keeper and the USDC landed in the owner's wallet.
Transaction: https://solscan.io/tx/3MKesA6YBeXG8NtofcazcEr1XWXKmvMBJ3oHHqPQd85pySN7UrsVCajKfXYzfZf4uFxVVoTKKBtiaPgKq5LExbEB
Frozen record with hashes: [`evidence/weekend-fill-2026-09-20/`](evidence/weekend-fill-2026-09-20/README.md)

**An earlier stop, on a weekday evening.** On 2026-09-15 at 20:40:07 UTC the keeper sold 989,239 raw NVDAx for 2.103427 USDC at 212.269707 against a floor of 215.00, under overnight rules. Transaction: https://solscan.io/tx/2ogwEBppzC1ZA49yJEixPV5fbVhKJ8aQCkcq1uXtaWftMXsNzmhnNjugPVrP129XnppufjG1NZvr5rVTnsYHTs7y

Both historical fills are stops against a floor. No ceiling has fired yet.

**A band armed now.** 882,621 raw NVDAx on token account `9Pk9Jzs31Dby5btFUZyMmcbx8cyUKVbpqeEAyvZjN2XP`, floor 215 and ceiling 233, armed 2026-09-22 at 11:09:31 UTC under overnight rules. The keeper's reading at 12:10:01 UTC was 226.71, inside the band.
Delegation: https://solscan.io/tx/2W8hWqnyiRHm6f37kX16iB5vVatwBwGoFCdF3JobD35kygs3DJDGF43HgWUSmyv2CECWYVSJTA55cTXdk2gdzzSR
Order memo: https://solscan.io/tx/5rDdesSXqoCPggGhdfZ3EW5aHEaqNfNo11dzHjxSxHP3ZMKbzpBzCjnczddjokJqfZ4g6b4FNMKwYro996zdRhxG

**The app:** https://gapless-market.vercel.app/app. The landing page at https://gapless-market.vercel.app reads `data/prices.csv` on every request.

**The videos:** pitch, three minutes: https://youtu.be/ekEbz6FhG2g. Technical walkthrough, five minutes: https://youtu.be/SP3AsOalG5Y

**The keeper's address:** `HbJ7XbcY2VsunoeK9v6o3FwKGvwfTBQ7horU9d3fonKs`. Every decision it makes is a commit to `data/orders.json` in this repository.

## Check it yourself

From a fresh clone. No keys are needed for the first three.

Confirm the evidence bundle is byte for byte what its README lists:

```bash
python3 evidence/verify_hashes.py
```

Read the Sunday fill from the chain and compare it with the claims above (block time, sole signer, USDC delivered, NVDAx taken, keeper left holding nothing):

```bash
python3 evidence/verify_fill.py
```

Run the keeper's regression suites. They load the real keeper with only the network replaced by controlled fakes and print one line per check; an empty run exits non-zero rather than passing. The audit suite takes about four minutes because several checks wait out real timeouts.

```bash
python3 -m venv .venv && .venv/bin/pip install -q -r keeper/requirements.txt
.venv/bin/python keeper/audit_check.py        # 46 checks: money path, mint guard, regimes, dependency failure, concurrent writes
.venv/bin/python keeper/mint_guard_check.py   # 11 checks
.venv/bin/python keeper/ceiling_check.py      # 26 checks: the band, per-edge counting, the guard against a ceiling
```

The fourth suite, `keeper/pyth_check.py` (34 checks), reads the live Pyth feed and needs `PYTH_API_KEY` in the environment.

## How it works

**The band.** An order carries a floor, a ceiling, or both. A reading at or below the floor counts toward a stop; a reading at or above the ceiling counts toward a take profit; a reading inside the band resets the count to zero and re-arms. Confirmations are counted per edge: a breach of one edge resets the count on the other, so a reading above the ceiling followed by one below the floor is not two of anything. Source: `side_of`, `count_breach` and the evaluation step in [`keeper/keeper.py`](keeper/keeper.py).

**Four regimes.** Rules come from [`keeper/regimes.json`](keeper/regimes.json), read by the keeper and the app. Open (Mon to Fri 09:30 to 16:00 ET): 1 confirmation, 50 bps slippage. Overnight: 2 confirmations, 150 bps. Weekend: 3 confirmations, 300 bps, split on impact. Closed, for assets with no market session at all: the weekend numbers with the impact check on every execution. Consecutive means within one regime and no more than 30 minutes apart. Which regime is in force comes from Pyth's keyless market-hours flag with a hand calendar as fallback, and can only ever tighten the recorder's label ([`keeper/market_hours.py`](keeper/market_hours.py)).

**The impact check.** Before a weekend or closed-regime sale the keeper asks Jupiter for a quote at the intended size and reads the price impact back. Over tolerance it halves and asks again: full, half, quarter, eighth, then it refuses rather than sell into that book. Source: `build_execution` in [`keeper/keeper.py`](keeper/keeper.py).

### The mint guard

xStocks are Token-2022 mints, not plain SPL tokens. Decoded from the raw mint accounts on
2026-09-15, all three tracked mints carry exactly these extensions: MetadataPointer, PermanentDelegate,
DefaultAccountState (initialized), ScaledUiAmount, Pausable, ConfidentialTransferMint (not
auto-approving), TransferHook (no program set), TokenMetadata. Two of them can turn a routine
corporate action into a catastrophic sale if ignored.

- **Scaled UI amount.** The issuer publishes a multiplier. It changes for two different reasons, and
  they need opposite responses. At a split, the displayed price per token moves by the multiplier
  ratio while the position's value does not; a floor compared against that price would fire and sell
  the whole position at what looks like a 90% loss, so the floor must be rebased by the ratio. At a
  dividend accrual, which is routine (NVDAx sits at 1.0017 and SPYx at 1.0057 from accruals alone),
  each raw token becomes worth slightly more, the displayed balance ticks up, and the displayed price
  does not move at all; rebasing there would quietly loosen the user's floor at every dividend. The
  multiplier alone cannot tell the two apart. So the keeper reads the mint and the price together:
  the multiplier in force at arming is stored with the order; on any change it resets the breach
  count, records the change and evaluates nothing that cycle; on the next reading it compares the
  displayed price with the pre-change displayed price. If the price moved by the ratio it is a split
  and the floor is rebased, persisted and shown on the row as such. If the price stayed put it is an
  accrual, recorded on the row, and the floor is left exactly where the user set it. Changes under
  about 1% are treated as accruals without consulting the price. That is an assumption about issuer
  behaviour, not a law: it holds because no forward or reverse split is a fraction of a percent, and
  it is thinnest for reverse splits, which are the closest thing to a small ratio that exists. If an
  issuer ever did something in that range, the floor would stay put rather than move, which errs
  toward keeping the position. If no pre-change price is on record, a material change is treated as
  a split, again erring toward keeping the position rather than selling it. Prices in this system are per displayed token: Jupiter's
  price equals the raw-unit market price divided by the effective multiplier, verified against
  two-way swap quotes that settle in raw units. The keeper reads the *effective* multiplier from
  the raw extension bytes (a pending multiplier with a past effective timestamp), not the parser's
  "current" field, which on NVDAx and SPYx was stale when checked.
- **Pausable.** If the mint is paused, no route is requested and nothing is sold. The refusal and the
  reason are recorded on the order and shown as a distinct state.
- **Transfer hook.** Currently no program is set. If one ever is, transfers need extra accounts the
  keeper does not resolve, so it refuses to swap, records why, and shows it. Detecting and refusing
  is the correct behaviour here; guessing is not.
- **Unreadable state.** If the mint account cannot be read, the keeper fires nothing that cycle.

All of these run before the impact check and before any route is requested. Mint state is read from
the same RPC the keeper already uses and cached only within a single cycle.

On a confirmed split both edges rebase by the ratio, so the band keeps its meaning. On an assumed split (no pre-change price on record) the floor is lowered but the ceiling is never lowered, because lowering a ceiling on an assumption could cause a sale; the rebase entry records the note. Tested in [`keeper/ceiling_check.py`](keeper/ceiling_check.py), section 3.

### Pyth: market hours and a second witness

Two uses of Pyth, weighted in this order.

**Market hours, keyless, all assets.** Pyth's public feed list carries, per US equity feed, a live
`market_hours.is_open` flag and the exchange's trading schedule for the coming twelve months,
holidays and early closes included:

```
America/New_York;0930-1600,0930-1600,0930-1600,0930-1600,0930-1600,C,C;0907/C,1126/C,1127/0930-1300,1224/0930-1300,1225/C,...
```

The keeper now takes its execution session from that (`keeper/market_hours.py`) instead of the
hand-typed table in `keeper/market_calendar.py`, which the audit flagged as a real defect: it had to
be extended every year and an unlisted holiday fell through to `open`. The live reading can only ever
tighten the recorder's label: `open` becomes `weekend` on a weekday closure and `overnight` on a
half-day afternoon; nothing can turn a closed label into `open`. If the flag is unreadable the hand
calendar decides, exactly as before, and the run line says which source decided. No key, no expiry,
no dependency: a Hermes outage degrades to the previous behaviour.

**A second price witness, refusal-only, trial-scoped.** Pyth publishes a regular equity feed
(`Equity.US.TSLA/USD`) and an xStock feed (`Crypto.TSLAX/USD`) for the same underlying. Before a
triggered order is executed, after the mint guard and before any route is requested, the keeper reads
Pyth's feed for the asset and compares it with the price it is about to act on. Beyond
`max_divergence_bps` (100) it refuses and records both values and the gap. A feed the key is entitled
to that is unreadable, or older than `max_age_s` (120) while the session is `open`, refuses too:
unknown is not agreement. Jupiter stays the execution price and the only decision input; a Pyth
reading can stop a sale and can never start one. `keeper/pyth_check.py` proves that by inspection
(checks 6a to 6d: the function writes only `blocked` and `pyth_check`, it has one call site in the
execution step, and the evaluation step never reads it).

What the key covers decides how much of this is live, and that was established from the API, not the
documentation. The Pyth Terminal demo trial is a fixed bundle of 21 feeds; of the six this product
needs, only `Equity.US.TSLA/USD` is in it (the other five answer `403 Not entitled`, per feed, with
the asset class named). So:

| asset | reference feed | witness state |
|---|---|---|
| TSLAx | `Equity.US.TSLA/USD` | live: compared when the print is fresh; `market_closed` when it is frozen outside NYSE hours |
| NVDAx, SPYx | none entitled | `not_entitled`, recorded, never blocks |

The trial lapses on **2026-10-02**, inside the judging window. From that moment every feed reads
`key_rejected`, which the keeper treats like `not_entitled`: recorded, never blocking. The interface
says so next to the regime line from the start, so the lapse looks like what it is rather than a
fault. Access that is absent is not a reading; only access that is present and fails can refuse.

Observed, not assumed: the equity feed keeps publishing through extended hours (a print one second
old at 21:03 UTC, an hour after the 20:00 UTC close, confidence 0.012% of price), so "fresh" rather
than "market open" is the test for whether the equity print is a like-for-like reference. Whether it
freezes after 20:00 ET and over the weekend is what `data/pyth.csv` records next. Rate limits: 40
back-to-back reads and a sustained 1 request per second returned 200 every time with no limit headers;
the recorder makes one call per five minutes.

**Recorded.** The recorder writes `data/pyth.csv` next to `data/prices.csv` at the same stamps: per
ticker, the Jupiter price the primary series recorded, the market flag, and both Pyth feeds with
price, confidence, publish time and status. `prices.csv` is untouched; its columns keep their meaning.

Rows whose equity status reads `backfilled` were not taken live. The collector for the first night
(2026-09-18 21:10 to 2026-09-19 07:40 UTC) ran on a laptop that slept, so those stamps were filled
afterwards from Hermes' historical endpoint (`/v2/updates/price/{publish_time}`) at the exact stamps
the primary series recorded, with the Jupiter price copied from `data/prices.csv` at the same stamp.
Before the feed froze at 20:00 ET each row carries the print Hermes served for that second; after it,
the frozen print the live endpoint was still serving. They are appended out of order and are
identifiable by their status; everything since runs on GitHub Actions and is live. The collector
never depends on a laptop again.

**Shown.** The landing page draws Pyth's TSLA equity line (ink) over our own recorded TSLAx line
(green), with the shaded closed hours taken from Pyth's flag rather than from our labels, and states
the reopen figures once a full closure with regular-hours readings on both sides exists. One line is a
third party's, the other is ours and verifiable in `data/prices.csv`; when the NYSE shuts, one stops.

Setup: `PYTH_API_KEY` as a repository secret (both workflows pass it through) and in `.env.local`
for local runs. Without it every price cell reads `no_key` and the market-hours integration still
works in full.

### Custody: off-chain order record, on-chain delegation and execution

**Chosen: Option B.** Orders are held off-chain, in `data/orders.json` in this repository.

Reasoning: an Anchor program with PDA-held orders is the right answer in principle, because the
program could verify the price before permitting the transfer. It needs a trusted on-chain price
source for xStocks mints, which is an unverified dependency, and deploying and hardening it in this
window would have consumed the build. The order record is the least security-sensitive part of the
system: what gives the keeper power over the user's tokens is the SPL `approve`, and that is on-chain
regardless.

**What the user signs.** An SPL Token-2022 `approveChecked` naming the keeper's public key as
delegate on one specific token account, with a fixed maximum in raw token units. Native token
program behaviour, not custom code. Then a Memo transaction carrying the order as JSON (uuid, mint,
token account, quantity, floor, delegate, approve signature), signed by the same wallet.

**What that grants.** The ability to transfer up to that amount, from that one account, until
revoked or exhausted. Nothing else: not other mints, not other accounts, not amounts beyond the cap.

**What it does not grant, stated plainly.** SPL delegation does not enforce the price condition.
The token program has no concept of a floor. The condition is enforced by the keeper's logic, so a
compromised keeper key could sell a delegated position at a price the user did not authorise, capped
at the delegated amount, on the delegated account only. **This system relies on the keeper's honesty
for the price condition.** The honest claim is that the blast radius is capped by the delegation and
the user can revoke at any time, with one `revoke` instruction, independent of Gapless being online.
The revoke control sits on the same screen as the order.

**What Gapless does not control.** xStocks mints carry a Token-2022 permanent delegate held by the
issuer (`5aMNNLQJwAEeoemTEMkv5NVjqKwvvefRYCQ5Z67HFvEq` on all three tracked mints). That authority can
transfer or burn tokens from any holder's account without the holder's permission. It is uncapped,
it predates any Gapless delegation, and Gapless can neither control it nor remove it. The app says
so wherever it explains custody.

**On-chain, verifiable:** the approve, the revoke, the memo (its signature is stored with the order;
the order id is a uuid embedded in the memo), and execution as one atomic transaction: delegated
`transferChecked` from the user's account to the keeper's account, Jupiter swap with
`destinationTokenAccount` set to the user's USDC account. If any step fails the whole transaction
reverts, so proceeds land in the owner's wallet and nowhere else, and the keeper never ends a
transaction holding the asset.

**Off-chain:** the order index and its state, written by the app through the GitHub Contents API
after it reads the approve and memo transactions back from an RPC and checks the signer, the
delegate, the amount and the memo fields. The keeper writes the same file. Every write is a public
commit under the repository owner's name. The app holds a fine-grained GitHub token scoped to this
repository's contents for those writes; it holds no signing key and never signs anything.

### Order state machine

```
armed --breach threshold met--> triggered --> executing --> filled
  |                                              |
  |                                              +--> failed --> armed (next run retries)
  +--user revoke--> revoked
```

`triggered` and `executing` are committed before the next step, so a keeper run that dies
mid-execution is recoverable: the next run finds `executing`, looks the pending signature up on
chain, and records the fill if it landed, or marks it failed if it cannot land any more. It never
decides from memory. All token amounts in the order record are raw integer strings; no floating
point arithmetic touches an amount anywhere in the system.

### The recorder

`recorder.py` fetches the USD price of each tracked mint from Jupiter Price API v3
(`GET https://api.jup.ag/price/v3?ids=...`, keyless at 0.5 requests per second, response keyed by
mint with a single `usdPrice`), computes the session from the run's UTC time in `America/New_York`,
and appends one row per ticker to `data/prices.csv`:

```
timestamp_utc,ticker,mint,price_usd,session,source
```

`open` is Mon–Fri 09:30 ≤ time < 16:00 ET; `overnight` is any other Mon–Fri time, including Friday
after 16:00 and Monday before 09:30; `weekend` is Saturday and Sunday. A failed fetch writes rows with
an empty price and `source=error` and exits zero so the schedule never breaks. The file is
append-only.

Tracked mints, verified on [Solscan](https://solscan.io) and against on-chain token metadata:

| Ticker | Name | Mint |
|---|---|---|
| NVDAx | NVIDIA xStock | `Xsc9qvGR1efVDFGLrVsmkzv3qi45LTBjeUKSPmx9qEh` |
| TSLAx | Tesla xStock | `XsDoVfqeBukxuZHWhdvWHBhgEHjGNst4MLodqsJHzoB` |
| SPYx | SP500 xStock | `XsoCS1TfEyfFhfvj8EtZ528L3CaKBDBRqRapnBbDF2W` |

`python3 recorder.py` appends three rows locally; `python3 smoke_test.py` is the single smoke check.

### Why continuous runs instead of cron

The recorder and keeper were first written as plain five-minute crons. GitHub fired the recorder's
cron three times in seven hours on this repository. So each workflow run now does its job on a
five-minute loop for the whole job limit, committing every reading as it is taken, and hands off to
a fresh run of itself before it ends. Cron at :07/:37 (recorder) and :09/:39 (keeper) only restarts
a chain if a hand-off ever fails, and a run checks for an already-queued successor so chains never
multiply. The hand-off itself costs about a minute of readings every five and a half hours; that
hole is real and stays visible in the data.

## What the keeper cannot do

**One capability.** The keeper holds a revocable SPL delegation over one token account, capped at one amount, and can do exactly one thing with it: transfer that amount to itself and swap it to USDC delivered to the owner's own USDC account, in a single transaction it signs alone. Nothing else: no other mints, no other accounts, no amount beyond the cap, no destination other than the owner. See `build_execution` and `check_build` in [`keeper/keeper.py`](keeper/keeper.py).

**Seventeen conditions under which it refuses to trade,** each recorded on the order with its reason:

1. The mint account cannot be read.
2. The mint's multiplier changed this cycle (nothing is evaluated until the change is classified).
3. The mint is paused by the issuer.
4. A transfer hook program is set on the mint.
5. The token account balance is zero.
6. The token account's owner on chain is not the order's owner.
7. The delegation is no longer on chain or is for zero.
8. The keeper wallet is below its 0.005 SOL fee reserve.
9. Jupiter's build references a program outside the allowlist (Jupiter v6, the two token programs, the associated token program, system, compute budget).
10. The swap instruction does not reference both the keeper's source account and the owner's USDC account.
11. The build asks for any signer other than the keeper.
12. The transaction exceeds 1,232 bytes.
13. Simulation fails.
14. In weekend or closed rules, price impact is still over tolerance at one eighth of the position.
15. The asset is a PreStocks mint, whose 50 bps transfer fee the fill path does not yet size for.
16. Pyth's entitled feed for the asset is unreadable, or stale while the market is open (unknown is not agreement).
17. Pyth's reading and the execution price diverge by more than 100 bps.

**The issuer's uncapped permanent delegation.** Every xStock mint carries a Token-2022 permanent delegate held by the issuer, `5aMNNLQJwAEeoemTEMkv5NVjqKwvvefRYCQ5Z67HFvEq` on all three tracked mints. The issuer can transfer or burn these tokens from any wallet without the holder's permission. It is uncapped, it predates any Gapless delegation, and Gapless can neither control it nor remove it. The arming screen says so before the user signs.

## Limits and open items

- **Execution on PreStocks assets is refused.** Those mints charge 50 bps on every transfer, so a delegated transfer delivers less than a swap built for the full amount, and the swap would fail. The keeper records and evaluates these orders and declines to execute them, with the reason on the row. This is the same discipline as the impact refusal: a trade it cannot execute correctly is not executed. Lifting it needs the swap sized to the post-fee amount.
- **GitHub Actions scheduling is best effort.** Both the recorder and the keeper run as continuous loops that hand off to themselves because the cron alone fired three times in seven hours on this repository. A stalled run still costs readings: on 2026-09-19 the recorder lost 46 minutes and then 3 hours 14 minutes to hung pushes before every network call was put on a clock. The keeper did not miss a cycle in either window, but the primary series has those two holes and nothing can fill them.
- **The Pyth price witness is trial scoped.** The key covers one feed, Equity.US.TSLA/USD, and lapses on 2026-10-02. After that the witness reads not entitled and never blocks. The market-hours flag is keyless and unaffected.
- **Order data is public.** `data/orders.json` lives in this repository and `/api/orders?owner=` returns any address's orders.
- **The regime thresholds were chosen.** 50, 150 and 300 bps and the 1, 2, 3 confirmations came from the design, not from a depth measurement. The book is measured at the moment of each trade; the tolerance it is measured against is a chosen ceiling.
- **The recorder labels holidays as open.** Its session labels are evidence and deliberately simple. The keeper does not trade on them; it takes hours from Pyth's flag.
- **Public RPC.** Browser reads go through a same-origin relay to the public Solana RPC, which rate limits.
- **What a compromised keeper key could do.** The keeper key holds an SPL delegation on every armed position, capped per order at the quantity the owner approved, and it never holds anyone's tokens or proceeds between cycles: the keeper's own transaction moves the delegated amount to itself and swaps it to USDC paid into the owner's account in one atomic step. That protection lives in the keeper's code, not in the delegation. A Token-2022 delegate can transfer the delegated amount to any account, so whoever holds the key could, with their own transaction, take up to the approved quantity from every armed position and send it anywhere; and it could trigger a sale on every armed position at once, at whatever the book gives at that moment. What bounds it: each order's cap, only accounts that have approved this keeper, and the fee reserve of SOL the key itself holds. Nothing in the keeper limits how many positions it can sell in one cycle; on a trigger it executes every triggered order in turn. What a holder can do about it: revoke the delegation at any time, in the app or from any wallet, and the keeper can then do nothing to that account.

### PreStocks: tokenised pre-IPO shares

A tokenised public equity has a market that is open 32.5 hours a week. A tokenised pre-IPO share has
no public market at all: no opening bell to correct a mispricing, no closing auction, no venue to
exit on a schedule. Every hour is a closed hour, so a floor on one is protection for all 168 hours
of the week rather than 135. The thin-book handling built for weekend xStocks (impact check, split
across runs) applies unchanged.

**What was found, on chain, 2026-09-18.** All eight assets listed by `https://prestocks.com/api/prestocks`
are Solana Token-2022 mints with 9 decimals, one issuer authority (`WV9PJN7XTmTLVwbutCLFxp8TyePee6Xq5mRq6Fti5Wc`),
and the same extension set as xStocks plus two more: MetadataPointer, PermanentDelegate, DefaultAccountState
(initialized), **TransferFeeConfig**, ConfidentialTransferMint, ConfidentialTransferFeeConfig, TransferHook
(no program), **ScaledUiAmount**, Pausable (not paused), TokenMetadata.

| Ticker | Mint | Effective multiplier |
|---|---|---|
| ANDURIL | `PresTj4Yc2bAR197Er7wz4UUKSfqt6FryBEdAriBoQB` | 1 |
| ANTHROPIC | `Pren1FvFX6J3E4kXhJuCiAD5aDmGEb7qJRncwA8Lkhw` | 1 |
| FIGUREAI | `PreZad18qfPtbxNpMtMuAuX2zVpvkEU8DnJx56faCWd` | 1 |
| KALSHI | `PreLWGkkeqG1s4HEfFZSy9moCrJ7btsHuUtfcCeoRua` | 1 |
| NEURALINK | `PrekqLJvJ3qVdXmBGDiexvwUTF4rLFDa6HWS4HJbw9S` | 1 |
| OPENAI | `PreweJYECqtQwBtpxHL171nL2K6umo692gTm7Q3rpgF` | 1.4861347 (since 2026-07-17) |
| POLYMARKET | `Pre8AREmFPtoJFT8mQSXQLh56cwJmM7CFDRuoGBZiUP` | 1 |
| SPACEX | `PreANxuXjsy2pvisWWMNB6YaJNzr7681wJJr2rHsfTh` | 5 (since 2026-06-08) |

**Corporate actions.** PreStocks uses the same ScaledUiAmount mechanism as xStocks: SpaceX's
multiplier is already 5 and OpenAI's 1.486, so the price-per-displayed-token collapse the mint guard
exists to catch is not hypothetical here. The existing guard covers these mints without change: the
multiplier is decoded from the same raw extension bytes, the split-versus-accrual classifier runs on
the same two readings, and pause and hook refusals apply as they do to xStocks. Jupiter's price for
these mints is per displayed unit, as for xStocks (a 10 USDC sell quote on SPACEX returns five times
the displayed-unit price per raw token, matching the multiplier of 5).

**Routing.** Every asset routes both ways at 10 USDC through the keeper's own path (`/swap/v2/build`,
300 bps): sells route through Manifest, Meteora DLMM, Whirlpool, Raydium CLMM and others at 0.00% to
1.41% price impact; buys at 0.00% to 1.34%. All eight are supported for recording and evaluation.

**The one thing the existing machinery does not cover: the transfer fee.** Each mint charges 50 bps
on every transfer (TransferFeeConfig, effective since epoch 1032; the chain was at epoch 1037 when
checked). The keeper's fill path moves the delegated amount into the keeper's account and then
swaps that amount; with the fee withheld, the keeper's account receives 99.5% of it and a swap built
for the full amount would fail simulation. Making the fill path fee-aware is a change to the fill
path, which this pass was not allowed to make, so `keeper/assets.json` marks every PreStocks mint
`executable: false` with that reason. Recording and evaluation run; an order that triggers is
refused before any route is requested, with the reason on its row. Lifting the flag needs one
change: size the swap to the post-fee amount (Token-2022 `TransferChecked` withholds
`ceil(amount × 50 / 10000)`).

**The fourth regime.** `closed`, in `keeper/regimes.json`, reuses the weekend numbers exactly: three
consecutive readings, 300 bps, impact check on every execution. Nothing new to justify. The recorder
labels these rows `closed` instead of a NYSE session; the app draws them in their own band labelled
"no market session"; and every NYSE-session figure on the site (closures, the counterfactual, the
closed-hours shares) is computed from session-bearing assets only, so pre-IPO rows never masquerade
as weekend readings.

**Eligibility note.** No pre-IPO asset from any issuer other than PreStocks exists in this repository.

## Build and run

```bash
npm install && npm run dev            # the app at http://localhost:3000, /app is the screen
python3 recorder.py                   # one recorder reading appended to data/prices.csv
.venv/bin/python keeper/keeper.py     # one keeper pass; KEEPER_DRY_RUN=1 builds and simulates without sending
```

Secrets: `KEEPER_SECRET_KEY` (repository secret, the keeper's key from `keeper/genkey.py`), `GITHUB_TOKEN` and `NEXT_PUBLIC_KEEPER_PUBKEY` on Vercel, `PYTH_API_KEY` optional. Workflow commits are authored by the repository owner. Vercel deploys are made from the CLI; git-triggered deploys are off in `vercel.json`.

## Repository layout

| path | what |
|---|---|
| `recorder.py`, `.github/workflows/record.yml` | the price recorder and its continuous run |
| `keeper/keeper.py`, `.github/workflows/keeper.yml` | the keeper and its continuous run |
| `keeper/regimes.json`, `keeper/assets.json`, `keeper/pyth.json` | regime rules, PreStocks assets, Pyth witness config |
| `keeper/market_hours.py`, `keeper/market_calendar.py` | live market hours from Pyth, hand calendar fallback |
| `keeper/audit_check.py`, `mint_guard_check.py`, `ceiling_check.py`, `pyth_check.py` | the four regression suites |
| `pyth.py` | Hermes client, feed ids, witness rows |
| `app/`, `components/`, `lib/` | the Next.js app, API routes, chain verification |
| `data/prices.csv`, `data/pyth.csv`, `data/orders.json` | the recorded series and the order store |
| `evidence/weekend-fill-2026-09-20/` | the frozen record of the Sunday fill, with `verify_hashes.py` and `verify_fill.py` beside it |

## Licence

MIT. See [`LICENSE`](LICENSE). Copyright 2026 Lydia Solomon.
