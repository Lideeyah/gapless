# Gapless

A small recorder that logs the on-chain USD price of three tokenized US stocks (xStocks on Solana)
every few minutes, around the clock, and commits each reading to this repository so the history is
publicly timestamped by GitHub.

## What it records and why

US equities trade 09:30-16:00 ET on weekdays. xStocks trade on Solana 24/7. This repo is evidence of
what those tokens actually traded at during the hours the stock market was closed. Every row is
collected live at the moment of the commit and appended to `data/prices.csv`. Nothing is backfilled,
interpolated, or synthesized. If a fetch fails, the failure is recorded as a row with an empty price.

Tracked tokens (Token-2022 mints, verified on [Solscan](https://solscan.io) and against the on-chain
token metadata via public RPC):

| Ticker | Name          | Mint |
|--------|---------------|------|
| NVDAx  | NVIDIA xStock | `Xsc9qvGR1efVDFGLrVsmkzv3qi45LTBjeUKSPmx9qEh` |
| TSLAx  | Tesla xStock  | `XsDoVfqeBukxuZHWhdvWHBhgEHjGNst4MLodqsJHzoB` |
| SPYx   | SP500 xStock  | `XsoCS1TfEyfFhfvj8EtZ528L3CaKBDBRqRapnBbDF2W` |

## CSV format

```
timestamp_utc,ticker,mint,price_usd,session,source
2026-09-13T15:29:00Z,NVDAx,Xsc9qvGR1efVDFGLrVsmkzv3qi45LTBjeUKSPmx9qEh,216.5143089894378,weekend,jupiter-price-v3
```

- `timestamp_utc`: ISO 8601, UTC, `Z` suffix. All three tickers in one run share a timestamp.
- `price_usd`: Jupiter's `usdPrice` for the mint, or empty when the fetch failed.
- `source`: `jupiter-price-v3`, or `error` when the price is missing.

The file is append-only. Existing rows are never rewritten.

## Price source

[Jupiter Price API v3](https://developers.jup.ag/docs/price): `GET https://api.jup.ag/price/v3?ids=<mint>,<mint>,<mint>`.
The response is a JSON object keyed by mint with a single `usdPrice` field per token, derived from the
last swapped price on Solana. Jupiter allows keyless requests on `api.jup.ag` at 0.5 requests per
second, which is far more than one request every five minutes, so no API key or secret is needed.
(The older `lite-api.jup.ag` host is being phased out, so this recorder uses the main host.)
Jupiter served all three mints when this was built, so the Solana RPC pool-price fallback was not needed
and is not implemented. Tokens Jupiter has no reliable price for are omitted from its response; the
recorder writes an `error` row for those.

## Session state

Computed from the run's UTC time converted to US Eastern with DST handled by the `America/New_York`
zone in the Python standard library.

- `open`: Monday-Friday, 09:30 <= time < 16:00 ET.
- `overnight`: Monday-Friday at any other time. This includes Friday 16:00 to midnight ET and Monday
  before 09:30 ET. There is no separate `premarket` state.
- `weekend`: Saturday and Sunday (ET).

## Known limitations

- **No holiday calendar.** US market holidays and early closes are labeled as if the market were open.
  Cross-reference the NYSE calendar when reading the data.
- **Cron drift.** GitHub Actions schedules are best-effort. Runs are frequently delayed by several
  minutes under load and are sometimes skipped entirely. The `timestamp_utc` column records when the
  reading was actually taken, so gaps and uneven spacing are visible in the data rather than hidden.
  Do not read this as five-minute precision.
- **Scheduled workflows pause after 60 days of no repository activity.** GitHub disables cron-triggered
  workflows on inactive repositories. The recorder's own commits count as activity, but if the workflow
  ever stops, it must be re-enabled by hand.
- **Push races.** If a push is rejected because the branch moved, the workflow rebases and retries once.
  `data/prices.csv` uses git's `union` merge driver so two appended readings are both kept.

## Running locally

```bash
python3 recorder.py
```

Appends three rows to `data/prices.csv` and prints them. Python 3.9+ with no third-party packages.
`python3 smoke_test.py` runs the single smoke check (script runs, rows are well-formed) against a
temporary file.
