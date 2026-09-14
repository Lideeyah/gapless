import Timeline from "@/components/Timeline";
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

  return (
    <main className="page">
      <section className="hero-grid">
        <div>
          <h1 className="hero">Wall Street is closed.<br />Your <span className="green">stop loss</span> isn&rsquo;t.</h1>
          <div style={{ height: 48 }} />
          <p className="sub">The stock market is open 32.5 hours a week. Your money is exposed for the other 135.</p>
          <div style={{ height: 24 }} />
          <p className="sub">Gapless watches your stock for you. Pick a price you won&rsquo;t let it fall below. If it gets there, at 3am or on a Sunday, Gapless sells it and the cash lands in your wallet.</p>
          <div style={{ height: 48 }} />
          <p className="mono secondary">
            {s.closedShareOfMovement !== null
              ? <>{fmtShare(s.closedShareOfMovement)} of all price movement recorded so far happened while the NYSE was shut · {window} · {s.tickers.length} tickers · <a href={HISTORY_URL}>data/prices.csv</a></>
              : s.firstAt
                ? <>recording since {fmtTs(s.firstAt)} · {s.readings} readings · {s.closureCount} closure{s.closureCount === 1 ? "" : "s"} touched, {s.fullClosureCount} complete · <a href={HISTORY_URL}>data/prices.csv</a></>
                : <>no readings yet · <a href={HISTORY_URL}>data/prices.csv</a></>}
          </p>
        </div>
        <div className="hero-side">
          <a href="/app" className="btn btn-primary" style={{ display: "inline-block", textDecoration: "none" }}>Open Gapless</a>
          <p className="mono secondary" style={{ paddingTop: 16 }}>connect a wallet · set a floor · revoke any time</p>
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
                : <p className="body">This closure moved up, not down. A floor would not have triggered; the exposure ran the other way. It opened at {fmtUsd(gap.reopenPrice)}.</p>}
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

      <section className="pair">
        <p className="lede">Set a floor on a stock you own. If the price reaches it, Gapless sells, at any hour, including nights and weekends.</p>
        <p className="body">Your tokens never leave your wallet. Gapless holds a revocable delegation on one token account, capped at one amount, and can only act when your condition is met.</p>
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
