import Timeline from "@/components/Timeline";
import PythChart from "@/components/PythChart";
import { fetchPythCsv, parsePythCsv, witness } from "@/lib/pyth";
import pythCfg from "@/keeper/pyth.json";
import { fetchCsv, parseCsv, stats, largestGap, floorExample, closureMoves } from "@/lib/csv";
import { commitForReading, HISTORY_URL } from "@/lib/commits";
import { fmtPct, fmtShare, fmtTs, fmtUsd } from "@/lib/format";

export const dynamic = "force-dynamic"; // every request re-reads data/prices.csv
const REPO_URL = "https://github.com/Lideeyah/gapless";

export default async function Landing() {
  let rows: ReturnType<typeof parseCsv> = [];
  let err: string | null = null;
  try { rows = parseCsv(await fetchCsv()); } catch (e) { err = (e as Error).message; }
  const s = stats(rows);
  const gap = largestGap(rows);
  const example = gap ? floorExample(rows, gap) : null;
  const moves = closureMoves(rows);
  const commit = gap ? await commitForReading(gap.extremeAt) : null;
  const ticker = gap?.ticker ?? (s.tickers.includes("NVDAx") ? "NVDAx" : s.tickers[0] ?? "NVDAx");
  const window = s.firstAt && s.lastAt ? `${fmtTs(s.firstAt)} to ${fmtTs(s.lastAt)}` : "no readings yet";
  let pythRows: ReturnType<typeof parsePythCsv> = [];
  let pythErr: string | null = null;
  try { pythRows = parsePythCsv(await fetchPythCsv()); } catch (e) { pythErr = (e as Error).message; }
  const wit = witness(pythRows, "TSLAx");
  const witRows = pythRows.filter((r) => r.ticker === "TSLAx").map((r) => ({ t: r.t, jupiter: r.jupiter, equity: r.equity, marketOpen: r.marketOpen }));

  return (
    <main className="page">
      <section className="hero-grid">
        <div>
          <h1 className="hero">Wall Street is closed.<br />Your <span className="green">stop loss</span> isn&rsquo;t.</h1>
          <div style={{ height: 48 }} />
          <p className="sub">The stock market is open 32.5 hours a week. Your money is exposed for the other 135.</p>
          <div style={{ height: 24 }} />
          <p className="sub">Gapless watches your stock for you. Pick a price you won&rsquo;t let it fall below. If it gets there, at 3am or on a Sunday, Gapless sells it and the cash lands in your wallet.</p>
        </div>
        <div className="hero-side">
          <a href="/app" className="btn btn-primary" style={{ display: "inline-block", textDecoration: "none" }}>Open Gapless</a>
          <p className="mono secondary" style={{ paddingTop: 16 }}>connect a wallet · set a floor · revoke any time</p>
          <div style={{ height: 120 }} />
          <div style={{ display: "grid", gap: 24 }}>
            {s.closedShareOfMovement !== null ? (
              <div><div className="fig">{fmtShare(s.closedShareOfMovement)}</div><div className="mono secondary">of recorded price movement happened<br />while the NYSE was shut · {s.tickers.length} tickers</div></div>
            ) : (
              <div><div className="fig">{s.closureCount}</div><div className="mono secondary">closure{s.closureCount === 1 ? "" : "s"} touched, {s.fullClosureCount} complete</div></div>
            )}
            <div><div className="fig">{s.readings}</div><div className="mono secondary">readings, five minutes apart, since<br />{s.firstAt ? fmtTs(s.firstAt) : "—"}</div></div>
            <p className="mono secondary"><a href={HISTORY_URL}>data/prices.csv</a> · every reading is a commit</p>
          </div>
        </div>
      </section>

      <div className="rule" />
      <div style={{ height: 72 }} />

      <section className="pair">
        <p className="lede">A gap is not a fast move. It is the absence of a move.</p>
        <div>
          <p className="body">Price does not travel from Friday&rsquo;s close to Monday&rsquo;s open. It reappears at the new level, and every price in between was never available to trade. That is why a stop loss cannot protect you overnight. There is nothing for it to fill against.</p>
          <div style={{ height: 24 }} />
          <p className="body">Tokenized stocks on Solana never stopped trading. The price does travel. Every price in between is available.</p>
        </div>
      </section>

      <div style={{ height: 96 }} />
      <div className="rule" />
      <div style={{ height: 72 }} />

      <section>
        <p className="lede">We have been recording this the whole time.</p>
        <div style={{ height: 48 }} />
        {err ? <p className="secondary">Could not read data/prices.csv: {err}</p> : (
          <Timeline rows={rows.map((r) => ({ t: r.t, ticker: r.ticker, price: r.price, session: r.session }))} ticker={ticker} floor={example?.floor ?? null} firstAt={s.firstAt} lastAt={s.lastAt} minReadings={2} />
        )}
        <div style={{ height: 48 }} />
        {gap ? (
          <div className="pair">
            <div>
              <p className="body">Largest gap across {moves.complete} complete closure{moves.complete === 1 ? "" : "s"} in the window, {gap.ticker}, a {gap.session} closure:</p>
              <div style={{ height: 24 }} />
              {example
                ? <p className="body">A floor set at {fmtUsd(example.floor)} would have filled at {fmtUsd(example.price)} during the closure, at the reading of {fmtTs(example.at)}. It opened at {fmtUsd(gap.reopenPrice)}.</p>
                : gap.lowPrice < gap.closePrice
                  ? <p className="body">This closure ended higher than it began. Its low was {fmtUsd(gap.lowPrice)} at {fmtTs(gap.lowAt)}, {fmtPct(gap.lowPct)} from the close. A floor set between {fmtUsd(gap.lowPrice)} and {fmtUsd(gap.closePrice)} would have filled there, during the closure, and missed the reopen at {fmtUsd(gap.reopenPrice)}. That is the premium on the insurance, and it is the same mechanism.</p>
                  : <p className="body">This closure never traded below its close. A floor would not have triggered. It opened at {fmtUsd(gap.reopenPrice)}.</p>}
              <div style={{ height: 24 }} />
              <p className="mono secondary">
                {commit ? <><a href={commit}>commit that wrote the {fmtTs(gap.extremeAt)} reading</a> · </> : null}
                <a href={HISTORY_URL}>every reading, as committed</a> · {moves.belowClose} of {moves.complete} complete closure{moves.complete === 1 ? "" : "s"} traded below the pre-close price at some point
              </p>
            </div>
            <div style={{ display: "grid", gap: 24 }}>
              <div><div className="fig">{fmtUsd(gap.closePrice)}</div><div className="mono secondary">last reading before close · {fmtTs(gap.closeAt)}</div></div>
              <div><div className="fig">{fmtUsd(gap.extremePrice)}</div><div className="mono secondary">{gap.extremePrice < gap.closePrice ? "lowest" : "highest"} during closure · {fmtPct(gap.extremePct)} · {fmtTs(gap.extremeAt)}</div></div>
              <div><div className="fig">{fmtUsd(gap.reopenPrice)}</div><div className="mono secondary">first reading after reopen · {fmtPct(gap.movePct)} · {fmtTs(gap.reopenAt)}</div></div>
            </div>
          </div>
        ) : (
          <div className="pair">
            <p className="body">The window is still accumulating. No complete closure has been recorded yet, so there is no gap to name, and none will be invented.</p>
            <div>
              <p className="body">{s.readings} reading{s.readings === 1 ? "" : "s"} exist{s.readings === 1 ? "s" : ""} across {s.tickers.length} tickers{s.firstAt ? `, from ${fmtTs(s.firstAt)} to ${fmtTs(s.lastAt)}` : ""}. The first gap the recorder can measure needs an open-hours reading on both sides of a closure.</p>
              <div style={{ height: 24 }} />
              <p className="mono secondary"><a href={HISTORY_URL}>every reading, as committed</a></p>
            </div>
          </div>
        )}
        <div style={{ height: 48 }} />
        <div className="row head mono secondary" style={{ gridTemplateColumns: "repeat(3, minmax(0,1fr))" }}><span>recording since</span><span>readings</span><span>closed-hours share of readings</span></div>
        <div className="row" style={{ gridTemplateColumns: "repeat(3, minmax(0,1fr))" }}>
          <span className="mono">{s.firstAt ? fmtTs(s.firstAt) : "—"}</span>
          <span><span className="fig" style={{ fontSize: 28 }}>{s.rows}</span> <span className="secondary">rows, {s.readings} timestamps, {s.closureCount} closure{s.closureCount === 1 ? "" : "s"} touched</span></span>
          <span><span className="fig" style={{ fontSize: 28 }}>{fmtShare(s.closedShareOfReadings)}</span> <span className="secondary">of {s.rows} rows fell outside 09:30–16:00 ET, over {window}</span></span>
        </div>
        <p className="mono faint" style={{ paddingTop: 8 }}>recomputed from <a href={HISTORY_URL}>data/prices.csv</a> at {fmtTs(s.computedAt)} · nothing interpolated, holes stay holes</p>
      </section>

      <div style={{ height: 96 }} />
      <div className="rule" />
      <div style={{ height: 72 }} />

      <section>
        <p className="lede">A second witness, from outside.</p>
        <div style={{ height: 24 }} />
        <div className="pair">
          <p className="body">The claim above rests on our own recorder. Pyth publishes a regular equity feed for the same underlying, and its keyless feed list says, per exchange, whether the market is open right now. The keeper takes its trading hours from that flag, holidays and half-days included, instead of a calendar typed in by hand.</p>
          <p className="body">The ink line is Pyth&rsquo;s {wit.equityFeed || "Equity.US.TSLA/USD"}. The green line is our own TSLAx series. When the NYSE shuts, one of them stops.</p>
        </div>
        <div style={{ height: 48 }} />
        {pythErr ? <p className="secondary">Could not read data/pyth.csv: {pythErr}</p> : (
          <PythChart rows={witRows} ticker="TSLAx" equityFeed={wit.equityFeed} />
        )}
        <div style={{ height: 48 }} />
        {wit.reopen ? (
          <div className="pair">
            <p className="body">Across the last complete closure Pyth&rsquo;s equity print moved {fmtPct(wit.reopen.equityGapPct)} between its last regular-hours print and its first after reopen, in one step. At that first reading our TSLAx price stood {fmtPct(wit.reopen.jupiterVsEquityPct)} from it.{wit.maxClosedDivergence ? ` The widest gap during the closure was ${fmtPct(wit.maxClosedDivergence.pct)}, at ${fmtTs(wit.maxClosedDivergence.at)}.` : ""}</p>
            <div>
              <div><div className="fig">{fmtUsd(wit.reopen.equityBefore)}</div><div className="mono secondary">Pyth, last regular-hours print before the closure</div></div>
              <div style={{ height: 16 }} />
              <div><div className="fig">{fmtUsd(wit.reopen.equityAfter)}</div><div className="mono secondary">Pyth, first print after reopen · {fmtPct(wit.reopen.equityGapPct)} · {fmtTs(wit.reopen.at)}</div></div>
              <div style={{ height: 16 }} />
              <div><div className="fig">{fmtUsd(wit.reopen.jupiterAfter)}</div><div className="mono secondary">TSLAx, our reading at the same stamp · {fmtPct(wit.reopen.jupiterVsEquityPct)} from Pyth</div></div>
            </div>
          </div>
        ) : (
          <div className="pair">
            <p className="body">{wit.readings === 0 ? "No witness readings yet. The recorder writes data/pyth.csv alongside data/prices.csv once the branch carrying it runs." : `${wit.readings} witness readings exist, ${wit.equityReadings} with a Pyth price, ${wit.closedReadings} of them while Pyth's flag said the NYSE was shut. The first reopen figure needs a full closure with regular-hours readings on both sides.`}</p>
            {wit.closedReadings > 0 && (
              <p className="body">During closed hours so far Pyth&rsquo;s equity feed produced {wit.closedDistinctPrints} distinct print{wit.closedDistinctPrints === 1 ? "" : "s"} across {wit.closedReadings} readings{wit.maxClosedDivergence ? `, while our TSLAx line drifted as far as ${fmtPct(wit.maxClosedDivergence.pct)} from it at ${fmtTs(wit.maxClosedDivergence.at)}` : ""}.{wit.confOpenPct !== null && wit.confClosedPct !== null ? ` Its confidence band averaged ${wit.confOpenPct.toFixed(4)}% of price in regular hours and ${wit.confClosedPct.toFixed(4)}% outside them.` : ""}</p>
            )}
          </div>
        )}
        <p className="mono faint" style={{ paddingTop: 24 }}>
          market hours: Pyth market_hours flag, keyless, all assets · price witness: {pythCfg.entitled.join(", ")} on the Pyth Terminal demo trial, TSLAx only, lapses {pythCfg.trial_expires}; after that the witness reads not_entitled and the keeper carries on without it · Pyth can refuse a sale, never cause one · recomputed from <a href={`${REPO_URL}/blob/main/data/pyth.csv`}>data/pyth.csv</a>
        </p>
      </section>

      <div style={{ height: 96 }} />
      <div className="rule" />
      <div style={{ height: 72 }} />

      <section className="pair">
        <p className="lede">Set a floor on a stock you own. If the price reaches it, Gapless sells, at any hour, including nights and weekends.</p>
        <div>
          <p className="body">Your tokens never leave your wallet. Gapless holds a revocable delegation on one token account, capped at one amount, and can only act when your condition is met.</p>
          <div style={{ height: 24 }} />
          <p className="body">That is the whole of what Gapless holds, and you can revoke it at any time. It is not the whole picture. xStocks are issued with a permanent delegate: the issuer can transfer or burn these tokens from any wallet without the holder&rsquo;s permission. Gapless does not control that delegation and cannot remove it.</p>
        </div>
      </section>

      <div style={{ height: 72 }} />
      <div className="rule" />
      <div style={{ height: 72 }} />

      <section className="pair">
        <p className="lede">Build what makes owning and using them better than today&rsquo;s brokerage app.</p>
        <p className="body">Not better. Possible at all. A brokerage cannot honour a floor at 3am because the market it routes to does not exist at 3am. This is not a faster version of something Wall Street does. It is something Wall Street cannot do.</p>
      </section>

      <div style={{ height: 96 }} />
      <div className="rule" />
      <div style={{ height: 72 }} />

      <section className="hero-grid">
        <p className="hero" style={{ fontSize: 44, lineHeight: "48px" }}>The market never closes.<br />Neither does your <span className="green">floor</span>.</p>
        <div className="hero-side"><a href="/app" className="btn btn-primary" style={{ display: "inline-block", textDecoration: "none" }}>Open Gapless</a></div>
      </section>

      <div style={{ height: 96 }} />
      <div className="rule" />
      <div style={{ height: 24 }} />
      <p className="mono secondary" style={{ display: "flex", justifyContent: "space-between", flexWrap: "wrap", gap: 16 }}>
        <span><a href={REPO_URL}>GitHub</a> · <a href={HISTORY_URL}>data/prices.csv</a></span>
        <span>Gapless</span>
      </p>
    </main>
  );
}
