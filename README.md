# Gapless

A stop loss that works when the stock market is closed.

Tokenized US stocks (xStocks) trade continuously on Solana. The NYSE is open 32.5 hours a week. For
the other 135 hours a holder cannot be protected by an exchange stop order, because a stop can only fill
at prices the market actually traded, and a closed market trades nothing. On Solana the price keeps
moving through those hours. Gapless lets a holder set a floor on an xStock they own and sells it to
USDC automatically if the price reaches that floor, at any hour, using SPL token delegation. The
tokens never leave the holder's wallet until the moment of execution, and the USDC lands in the
holder's own wallet.

This repository holds three things:

1. **The recorder** (`recorder.py`, `.github/workflows/record.yml`): logs the on-chain price of
   NVDAx, TSLAx and SPYx every five minutes, around the clock, into `data/prices.csv`. Every commit
   is timestamped by GitHub. Nothing is backfilled. See the recorder section at the bottom.
2. **The app** (`app/`, `components/`, `lib/`): a single-screen Next.js app at `/app`, deployed on
   Vercel. Connect Phantom, see your xStock balances, set a floor, revoke it, watch it fire.
3. **The keeper** (`keeper/keeper.py`, `.github/workflows/keeper.yml`): a GitHub Actions cron that
   checks armed orders against live prices and executes them under regime-aware rules.

## Honest positioning

Jupiter already offers limit orders on any SPL token, so a crude version of this exists today. What
Gapless adds is regime awareness: an order that fires identically at 2pm Tuesday and 3am Sunday is
firing into two different markets. During US hours arbitrage keeps xStocks pegged and books are deep.
Overnight, futures still price the underlying but on-chain books thin out. On weekends the issuer's
mint and redeem desk is closed and market makers quote wide and small because they cannot hedge until
Monday. A market sell into a thin weekend book can fill far below the floor, and protection that
executes badly at the moment it is needed is not protection. Binance Research (11 September 2026)
found on-chain tokenized stock prices captured a median 92% of the move that appeared at Monday's open
across seven weekends, which is the premise the product rests on: closed-hours movement is price
discovery, not noise.

## Regime-aware execution

Thresholds live in one place, `keeper/regimes.json`, read by the keeper and shown by the app.

| Regime | Hours | Confirmation required | Slippage tolerance |
|---|---|---|---|
| `open` | Mon–Fri 09:30–16:00 ET | fire on first breach | 50 bps |
| `overnight` | Mon–Fri outside those hours | 2 consecutive keeper readings | 150 bps |
| `weekend` | Sat–Sun | 3 consecutive keeper readings | 300 bps |

In `weekend`, before firing, the keeper asks Jupiter for a quote at the intended size. If price impact
exceeds the tolerance it halves the size (up to three times) and executes that portion, leaving the
rest armed for the next run, rather than dumping into a thin book.

The session state function is the recorder's `session_state()` and nothing else. The keeper imports
it. The app never recomputes it: the timeline's session bands come from the `session` column the
recorder wrote, and the "regime now" line shows the session of the recorder's latest reading. Two
definitions of "weekend" that drift apart would poison every number on the site, so there is one.

## Architecture decision: off-chain order record, on-chain delegation and execution

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

## Pre-trade mint state guard

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

## The keeper

`keeper/keeper.py` runs on GitHub Actions in the same pattern as the recorder: one job checks orders
every five minutes for about five hours forty minutes, then dispatches the next job of itself, with
a twice-hourly cron only as a backstop (see "Why continuous runs" below). Each check, in order:

1. Load `data/orders.json`. If nothing is armed, triggered, executing or failed, log and exit.
2. Compute session state with the recorder's function.
3. One batched price read for every distinct mint. If it fails, no breach count changes and nothing
   fires: a failed read is a gap in observation, not evidence either way.
4. For each order: recover anything left `executing` from the chain; reset `failed` to `armed`;
   compare price to floor; `breach_count` increments only on a successful read at or below the
   floor and resets to zero on any read above it; mark `triggered` when the regime threshold is met.
5. Commit the evaluation.
6. For each `triggered` order, re-read the delegation from the chain, size the execution (in
   `weekend`, halve until Jupiter's price impact is within tolerance and leave the rest armed),
   build and simulate, commit `executing` with the pending signature, send, confirm.
7. Record the fill from the transaction's real USDC balance change, or mark `failed` with the
   reason. Commit.
8. Log every order examined and why it did or did not fire. The log is a deliverable.

The run exits zero on any data or network failure. A broken schedule is worse than a missed check.

What "consecutive readings" means in code: consecutive keeper readings at or below the floor, within
one regime, with no more than 30 minutes between counted readings. A reading Jupiter omits or a read
that fails does not count as a non-breach and does not reset the sequence; a regime change or a
30-minute gap does. A sequence started in `overnight` cannot be completed by the first `open` reading.

Execution guards, in order, each of which stops the trade: the order's delegate must be this
keeper's key; the mint guard must pass; the token account's owner on chain must be the order's
owner (proceeds are paid to that owner); the keeper must still be the delegate; the account must
have a balance (an emptied account is shown as "nothing left to sell" rather than retried forever);
the keeper wallet must hold at least 0.005 SOL for rent and fees; the Jupiter build may reference
only known programs (Jupiter v6, the token programs, the associated-token program, System, Compute
Budget), must route from the keeper's source account to the owner's USDC account, and may ask for
no signer but the keeper; the transaction must fit in 1232 bytes and simulate cleanly. The amount is
never more than the delegated amount and never more than the balance. In `weekend`, if price impact
is still above tolerance at one eighth of the position, nothing is sold that cycle.

Settlement is decided from the chain, never from memory. `executing` is committed together with the
signature, the amount, the multiplier in force and the blockhash's last valid block height before the
send. If confirmation is not seen within 60 seconds the order stays `executing`; each later cycle asks
the chain: confirmed means the fill is recorded from the real USDC balance change (and the order waits
if that change is not yet servable), a failed status means `failed`, and "never landed" is declared
only after the blockhash has expired and the transaction is still absent. A run that finds an order
settled at the top of a cycle does not evaluate it again in that cycle.

**The hot key.** The keeper signs execution transactions with a keypair stored as the GitHub Actions
secret `KEEPER_SECRET_KEY`. This is a hackathon-grade arrangement. What the key can do: act as SPL
delegate on token accounts whose owners explicitly approved it, up to the approved amount, and pay
transaction fees from its own SOL balance. What it cannot do: touch any account that has not
approved it, move more than the approved amount, move anything after a revoke, or redirect proceeds,
because the swap's destination is the user's own USDC account inside the same atomic transaction.
A production version would use a distributed keeper network. GitHub Actions cron is best-effort:
runs drift under load, sometimes by many minutes, and are occasionally skipped. Nothing here claims
sub-minute response.

## Use of the recorded data

Every figure derived from `data/prices.csv` is computed on the server at request time from a fresh
fetch of the file on `main` (`/api/history`, `dynamic = "force-dynamic"`, `cache: "no-store"`). No
number is baked in at build time, so the site keeps up with the recorder without a redeploy. GitHub's
raw file CDN can hold a copy for up to about five minutes, which is the only caching in the path.

Where the data appears, and the window each figure covers:

- **Timeline** (top): the recorder's price line for the selected ticker, over the full recorded
  window, against session bands taken from the recorder's own `session` column. Holes in the
  recording (missing price, or more than 15 minutes between readings) break the line. Under 20 priced
  readings the timeline says so instead of drawing.
- **Order row**: distance of the current price from the floor, and the closed-hours low of the
  selected ticker over the recorded window, stated as having reached or stayed above the floor.
- **Regime now**: the session of the recorder's latest reading, with its timestamp.
- **Counterfactual** (bottom): the largest gap across a complete closure in the window: the last
  open-hours reading before the close, the extreme during the closure, and the first open-hours
  reading after reopen. If the window contains no complete closure, it says the window is still
  accumulating and shows what does exist (closed-hours low and high per ticker so far).
- **Provenance**: recording start and end, row and timestamp counts, share of rows that fell outside
  09:30–16:00 ET, share of summed absolute price movement that happened outside those hours (shown
  only once the window contains open-hours readings, since before that the figure is trivially
  100%), and the number of closures touched and completed.

Nothing is interpolated, smoothed, or filled. Rows with `source=error` carry no price and are
excluded from price figures but counted in provenance.

## Interface

One screen, `/app`. Paper `#F4F1EA`, ink `#14161A`, green `#1F4D3D` for anything the user acted on
or can act on, oxblood `#7A2B2B` for fired stops and destructive confirms only. Letterpress technique:
no cards, no boxes, no shadows, hairlines at 12% for structure, paper grain at 3.5% over the whole
surface, a 24px baseline grid, 2px radius. Serif for numbers that matter (Bodoni Moda), serif for
text (Newsreader), monospace for timestamps, addresses and signatures (IBM Plex Mono). No sans-serif.
The price line draws on load; state changes cross-fade over 400ms; nothing bounces. The timeline owns
the upper field and actions sit below it on bare paper, so a solid green button never lands on a dark
weekend band. Where the line crosses dark bands it carries a paper halo rather than changing colour.

Every committing action moves through confirm, in flight, terminal. Disabled buttons state their
precondition. No display renders `undefined`, `NaN` or `null`; every number passes through
`lib/format.ts`.

## Setup

Free tiers only. No paid services.

1. **Keeper key.** Run `python3 keeper/genkey.py` (needs `pip install solders`). It writes
   `keeper/keeper-key.json` locally (gitignored) and prints the public key. Paste the file's contents
   into the repository secret `KEEPER_SECRET_KEY` (Settings → Secrets and variables → Actions). Fund
   the public key with a small amount of SOL for transaction fees and rent (0.05 SOL is plenty).
2. **Vercel environment.** `NEXT_PUBLIC_KEEPER_PUBKEY` = the public key from step 1.
   `GITHUB_TOKEN` = a fine-grained personal access token with Contents read and write on this
   repository only, used by the app to write `data/orders.json`. `GITHUB_REPO` = `Lideeyah/gapless`.
   Optional: `NEXT_PUBLIC_RPC_URL` and `RPC_URL` to use an RPC other than the public mainnet-beta
   endpoint, which is rate-limited.
3. **Workflows.** The `record` and `keeper` workflows need Actions enabled and workflow permissions
   set to read and write. Trigger each once by hand from the Actions tab to start its chain.

## Why continuous runs instead of cron

The recorder and keeper were first written as plain five-minute crons. GitHub fired the recorder's
cron three times in seven hours on this repository. So each workflow run now does its job on a
five-minute loop for the whole job limit, committing every reading as it is taken, and hands off to
a fresh run of itself before it ends. Cron at :07/:37 (recorder) and :09/:39 (keeper) only restarts
a chain if a hand-off ever fails, and a run checks for an already-queued successor so chains never
multiply. The hand-off itself costs about a minute of readings every five and a half hours; that
hole is real and stays visible in the data.

## Verified before building

- Mint addresses: checked on Solscan and against on-chain token metadata; they match the recorder.
- Jupiter price endpoint and response: reused from the recorder (`recorder.PRICE_URL`).
- Jupiter routing at sub-dollar size: `GET /swap/v2/build` returned routes for roughly one dollar in
  both directions on all three mints (USDC to and from NVDAx, TSLAx, SPYx) with price impact below
  0.05%.
- A real fill, end to end, on 2026-09-15: a floor of 215.00 armed on 0.00990921 NVDAx at 20:2x UTC in
  the `overnight` regime; the keeper counted breach 1/2 at 20:35:01 UTC (212.04) and executed at
  20:40:01 UTC, selling 989,239 raw units for 2.103427 USDC delivered to the owner's wallet, fill
  price 212.269707 per displayed token, signature
  `2ogwEBppzC1ZA49yJEixPV5fbVhKJ8aQCkcq1uXtaWftMXsNzmhnNjugPVrP129XnppufjG1NZvr5rVTnsYHTs7y`. The
  keeper was the only signer, ended the transaction holding no NVDAx and no USDC, and the owner's
  delegation was consumed to zero.

## Deliberately out of scope for v1

- On-chain order records and program-enforced price conditions.
- Take-profit or trailing floors. One primitive, executed well.
- Multiple orders per token account. One delegation, one order: a newer approve on the same account
  supersedes the older order.
- Partial-fill UX beyond recording it. The weekend split is a safety mechanism, not a feature.
- US market holidays. The recorder does not model them.
- Notifications.
- Any token that is not an xStock.

## Limitations

- **Holiday calendar, two behaviours.** The recorder's `session_state()` has no calendar, so
  `data/prices.csv` labels NYSE holidays and the afternoons of half-days as `open`; cross-reference
  the NYSE calendar when reading the data. The keeper does not trade on those labels: it layers
  `keeper/market_calendar.py` (NYSE full closures and 13:00 ET early closes for 2026 and 2027) on top
  and executes a weekday holiday under `weekend` rules and a half-day afternoon under `overnight`
  rules. When the two disagree the keeper's log says so on the run line. The table must be extended
  each year; an unlisted holiday falls back to the recorder's label, which is `open`.
- **Cron drift.** GitHub Actions schedules are best-effort. The `timestamp_utc` column records when
  a reading was actually taken, so gaps and uneven spacing are visible in the data rather than
  hidden. Do not read this as five-minute precision, for the recorder or the keeper.
- **Scheduled workflows pause after 60 days of no repository activity.** The workflows' own commits
  count as activity, but if they ever stop, they must be re-enabled by hand.
- **Order data is public.** `data/orders.json` lives in this public repository and `/api/orders?owner=`
  returns any address's orders. Nothing in an order is secret (every field is also on chain), but the
  association of a wallet with a floor is visible to anyone. A production version would keep the
  index private.
- **The demo capture replays the recorder's labels.** Local capture mode drives the interface with the
  `session` column of `data/prices.csv`, which has no holiday calendar. If a recorded window ever
  spans a US market holiday, the capture would show `open` where the live keeper would apply `weekend`
  rules. No holiday falls in the current window.
- **Public RPC.** Holdings are read through the public Solana RPC from the browser, which rate-limits
  aggressively. If the holdings row reports an RPC error, reload, or set `NEXT_PUBLIC_RPC_URL`.
- **USDC account.** Execution creates the user's USDC associated token account if it does not exist,
  paid by the keeper.
- **Push races.** The recorder pushes with git and rebases once if the branch moved; the keeper and
  the app write `data/orders.json` through the Contents API with optimistic concurrency and one
  retry. The two never write the same file.

## The recorder

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
