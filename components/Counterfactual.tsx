import { fmtPct, fmtShare, fmtTs, fmtUsd } from "@/lib/format";
import type { Gap, Stats } from "@/lib/csv";

export default function Counterfactual({ stats, gap }: { stats: Stats | null; gap: Gap | null }) {
  if (!stats) return <p className="secondary">Reading data/prices.csv…</p>;
  const window = stats.firstAt && stats.lastAt ? `${fmtTs(stats.firstAt)} to ${fmtTs(stats.lastAt)}` : "no window yet";
  return (
    <div>
      <h2>The counterfactual</h2>
      <div style={{ height: 24 }} />
      {gap ? (
        <div>
          <p>
            Largest gap in the recorded window, {gap.ticker}, across a {gap.session} closure:
          </p>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 16, paddingTop: 24 }}>
            <Figure label={`last reading before close · ${fmtTs(gap.closeAt)}`} value={fmtUsd(gap.closePrice)} />
            <Figure label={`extreme during closure · ${fmtTs(gap.extremeAt)} · ${fmtPct(gap.extremePct)}`} value={fmtUsd(gap.extremePrice)} />
            <Figure label={`first reading after reopen · ${fmtTs(gap.reopenAt)} · ${fmtPct(gap.movePct)}`} value={fmtUsd(gap.reopenPrice)} />
          </div>
          <p className="secondary" style={{ paddingTop: 24 }}>
            Every price between the close and the reopen was tradable on Solana and untradable on the exchange. A floor between {fmtUsd(gap.closePrice)} and {fmtUsd(gap.extremePrice)} would have been reachable during closed hours, at the regime’s confirmation and slippage rules.
          </p>
        </div>
      ) : (
        <div>
          <p>
            The window is still accumulating. No full closure has been recorded yet, so no gap can be named.
          </p>
          <p className="secondary" style={{ paddingTop: 24 }}>
            Recording began {stats.firstAt ? fmtTs(stats.firstAt) : "—"}{stats.currentSession && stats.currentSession !== "open" ? `, inside a ${stats.currentSession} closure` : ""}.
            {stats.inClosureSince ? ` The current closure has ${stats.closureReadings} reading${stats.closureReadings === 1 ? "" : "s"} so far.` : ""}
            {" "}A gap needs an open-hours reading on both sides of a closure. The first one the recorder can capture starts at the next Friday 16:00 ET close and completes at the following Monday 09:30 ET open.
          </p>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 16, paddingTop: 24 }}>
            {stats.tickers.map((tk) => {
              const p = stats.perTicker[tk];
              return <Figure key={tk} label={`${tk} · closed-hours low / high so far`} value={p.minClosed === null ? "—" : `${fmtUsd(p.minClosed)} / ${fmtUsd(p.maxClosed)}`} small />;
            })}
          </div>
        </div>
      )}
      <div style={{ height: 48 }} />
      <h2>Provenance</h2>
      <div style={{ height: 24 }} />
      <div className="row head mono secondary" style={{ gridTemplateColumns: "repeat(4, minmax(0,1fr))" }}><span>window</span><span>readings</span><span>closed-hours share of readings</span><span>closed-hours share of movement</span></div>
      <div className="row" style={{ gridTemplateColumns: "repeat(4, minmax(0,1fr))" }}>
        <span className="mono">{window}</span>
        <span><span className="num" style={{ fontSize: 28 }}>{stats.rows}</span> <span className="secondary">rows, {stats.readings} timestamps{stats.errorRows ? `, ${stats.errorRows} error rows` : ""}</span></span>
        <span><span className="num" style={{ fontSize: 28 }}>{fmtShare(stats.closedShareOfReadings)}</span> <span className="secondary">of {stats.rows} rows fell outside 09:30–16:00 ET</span></span>
        <span>{stats.closedShareOfMovement === null
          ? <span className="secondary">not computable until the window contains open-hours readings</span>
          : <><span className="num" style={{ fontSize: 28 }}>{fmtShare(stats.closedShareOfMovement)}</span> <span className="secondary">of summed absolute price movement, across {stats.tickers.length} tickers, holes excluded</span></>}
        </span>
      </div>
      <p className="mono faint" style={{ paddingTop: 8 }}>
        {stats.closureCount} closure{stats.closureCount === 1 ? "" : "s"} touched, {stats.fullClosureCount} complete · figures recomputed from data/prices.csv at {fmtTs(stats.computedAt)} · no interpolation, holes stay holes
      </p>
    </div>
  );
}

function Figure({ label, value, small }: { label: string; value: string; small?: boolean }) {
  return (
    <div>
      <div className="num" style={{ fontSize: small ? 28 : 40, lineHeight: "48px" }}>{value}</div>
      <div className="mono secondary" style={{ lineHeight: "24px" }}>{label}</div>
    </div>
  );
}
