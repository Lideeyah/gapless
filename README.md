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

Reasoning: an Anchor program with PDA-held orders is the right answer in principle, but deploying
and hardening one reliably, with the SPL delegation and Jupiter CPI wired through, would have consumed
the build window and left the regime logic, the evidence pipeline and the interface half done. The
order record is the least security-sensitive part of the system, because the thing that actually
gives the keeper power over the user's tokens is the SPL `approve`, and that is on-chain regardless.

What is on-chain and verifiable:

- **Delegation.** The user signs an SPL Token-2022 `approveChecked` delegating up to the chosen
  quantity of one token account to the keeper's public key. Anyone can read the delegate and the
  delegated amount from the token account. Revoking is an SPL `revoke`, also signed by the user.
- **Order record.** The user signs a second transaction carrying a Memo with the order as JSON
  (mint, token account, quantity, floor, delegate, approve signature). The memo transaction's
  signature is the order id. It is timestamped by the chain and signed by the owner.
- **Execution.** One atomic transaction: delegated `transferChecked` from the user's account to the
  keeper's account, Jupiter swap to USDC with `destinationTokenAccount` set to the user's USDC
  account. If any step fails the whole transaction reverts. The keeper never ends a transaction
  holding the asset or the proceeds.

What is off-chain:

- The **index** of orders (`data/orders.json`), which the app writes through the GitHub Contents API
  after verifying on an RPC that the approve and memo transactions exist, succeeded, were signed by
  the connected wallet, and say what the app was told they say. The keeper writes the same file with
  its check results and fills. Every write is a public commit.
- The **enforcement**. A memo has no program logic; the keeper decides when to execute. The keeper
  re-reads the delegation from the chain before every execution, so if a user revokes on-chain
  through any wallet the order is closed even if the app never heard about it.

A production version would move the order record into a program-owned account so that the floor,
quantity and regime rules are enforced by the chain rather than by a cron job. This build does not do
that, and nothing in the interface claims otherwise.

## The keeper

`keeper/keeper.py` runs every five minutes on GitHub Actions, the same pattern as the recorder. Each
run loads open orders, fetches prices from Jupiter Price API v3 (the same source as the recorder),
computes the session with the recorder's function, applies the regime rules, and logs one line per
order with the decision and the reason, whether it fired or not. The last decision is also written
into the order so the app shows it on the row.

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
   set to read and write. Trigger each once by hand from the Actions tab; the cron takes over.

## Limitations

- **No holiday calendar.** US market holidays and early closes are labeled as if the market were
  open. Cross-reference the NYSE calendar when reading the data.
- **Cron drift.** GitHub Actions schedules are best-effort. The `timestamp_utc` column records when
  a reading was actually taken, so gaps and uneven spacing are visible in the data rather than
  hidden. Do not read this as five-minute precision, for the recorder or the keeper.
- **Scheduled workflows pause after 60 days of no repository activity.** The workflows' own commits
  count as activity, but if they ever stop, they must be re-enabled by hand.
- **Public RPC.** Holdings are read through the public Solana RPC from the browser, which rate-limits
  aggressively. If the holdings row reports an RPC error, reload, or set `NEXT_PUBLIC_RPC_URL`.
- **USDC account.** Execution creates the user's USDC associated token account if it does not exist,
  paid by the keeper.
- **Push races.** The recorder pushes with git; the keeper and the app write `data/orders.json`
  through the Contents API with optimistic concurrency and one retry. `data/prices.csv` uses git's
  `union` merge driver so two appended readings are both kept.

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
