// Reads the recorder's data/pyth.csv (the Pyth witness series) at request time and derives every figure
// the interface shows about it. Nothing is interpolated; a missing reading is a hole.
//
// Each row carries, for one ticker at one stamp: the Jupiter price the primary series recorded (our own
// line, independently verifiable in data/prices.csv), Pyth's keyless market_hours flag for the NYSE, and
// both Pyth feeds with a status. Today the Terminal demo trial entitles the key to Equity.US.TSLA/USD
// only, so the equity side is populated for TSLAx and the other cells read `not_entitled`.

export type PythRow = {
  t: string; ms: number; ticker: string; jupiter: number | null; marketOpen: boolean | null;
  equityFeed: string; equity: number | null; equityConf: number | null; equityPublishMs: number | null; equityStatus: string;
  xstockFeed: string; xstock: number | null; xstockConf: number | null; xstockPublishMs: number | null; xstockStatus: string;
};

const RAW_URL = process.env.PYTH_CSV_URL ?? "https://raw.githubusercontent.com/Lideeyah/gapless/main/data/pyth.csv";

export async function fetchPythCsv(): Promise<string> {
  const res = await fetch(`${RAW_URL}?t=${Date.now()}`, { cache: "no-store", headers: { "User-Agent": "gapless-web" } });
  if (res.status === 404) return ""; // the file appears with the first recorder run on a branch that carries pyth.py
  if (!res.ok) throw new Error(`pyth.csv fetch failed: HTTP ${res.status}`);
  return res.text();
}

const num = (s: string | undefined): number | null => { const n = s === undefined || s === "" ? NaN : Number(s); return Number.isFinite(n) ? n : null; };
const when = (s: string | undefined): number | null => { const n = s ? Date.parse(s) : NaN; return Number.isFinite(n) ? n : null; };

export function parsePythCsv(text: string): PythRow[] {
  const lines = text.trim().split("\n");
  const out: PythRow[] = [];
  for (const line of lines.slice(1)) {
    const c = line.split(",");
    const ms = Date.parse(c[0]);
    if (!c[1] || !Number.isFinite(ms)) continue;
    out.push({ t: c[0], ms, ticker: c[1], jupiter: num(c[2]), marketOpen: c[3] === "1" ? true : c[3] === "0" ? false : null,
      equityFeed: c[4] ?? "", equity: num(c[5]), equityConf: num(c[6]), equityPublishMs: when(c[7]), equityStatus: c[8] ?? "",
      xstockFeed: c[9] ?? "", xstock: num(c[10]), xstockConf: num(c[11]), xstockPublishMs: when(c[12]), xstockStatus: c[13] ?? "" });
  }
  return out.sort((a, b) => a.ms - b.ms);
}

export type ClosedRun = { startMs: number; endMs: number; readings: number; full: boolean };

/** Contiguous runs of readings where Pyth's market flag says the NYSE is shut. `full` = open readings on both sides. */
export function closedRuns(rows: PythRow[]): ClosedRun[] {
  const out: ClosedRun[] = [];
  let cur: ClosedRun | null = null, seenOpen = false;
  for (const r of rows) {
    if (r.marketOpen === null) continue;
    if (r.marketOpen) { if (cur) { cur.full = seenOpen; out.push(cur); cur = null; } seenOpen = true; }
    else if (!cur) cur = { startMs: r.ms, endMs: r.ms, readings: 1, full: false };
    else { cur.endMs = r.ms; cur.readings += 1; }
  }
  if (cur) out.push(cur);
  return out;
}

export type Witness = {
  ticker: string; equityFeed: string; readings: number; equityReadings: number; firstAt: string | null; lastAt: string | null;
  entitled: boolean; lastEquityStatus: string | null;
  /** distinct equity publish times seen while the flag said closed, against readings in that state: 1 means frozen */
  closedReadings: number; closedDistinctPrints: number;
  /** mean confidence as a share of price, open vs closed hours */
  confOpenPct: number | null; confClosedPct: number | null;
  /** widest gap between our TSLAx line and Pyth's frozen equity print during a closed run */
  maxClosedDivergence: { at: string; jupiter: number; equity: number; pct: number } | null;
  /** first reading after Pyth's flag flips back to open, against the last print before it froze */
  reopen: { at: string; equityBefore: number; equityAfter: number; jupiterAfter: number; equityGapPct: number; jupiterVsEquityPct: number } | null;
  runs: ClosedRun[];
};

export function witness(rows: PythRow[], ticker: string): Witness {
  const tr = rows.filter((r) => r.ticker === ticker);
  const eq = tr.filter((r) => r.equity !== null);
  const runs = closedRuns(tr);
  const closed = eq.filter((r) => r.marketOpen === false), open = eq.filter((r) => r.marketOpen === true);
  const meanConf = (xs: PythRow[]) => xs.length ? xs.reduce((a, r) => a + (r.equityConf! / r.equity!) * 100, 0) / xs.length : null;
  let maxDiv: Witness["maxClosedDivergence"] = null;
  for (const r of closed) {
    if (r.jupiter === null) continue;
    const pct = ((r.jupiter - r.equity!) / r.equity!) * 100;
    if (!maxDiv || Math.abs(pct) > Math.abs(maxDiv.pct)) maxDiv = { at: r.t, jupiter: r.jupiter, equity: r.equity!, pct };
  }
  let reopen: Witness["reopen"] = null;
  for (const run of runs.filter((x) => x.full)) {
    const before = [...eq].reverse().find((r) => r.ms < run.startMs && r.marketOpen === true);
    const after = eq.find((r) => r.ms > run.endMs && r.marketOpen === true && r.jupiter !== null);
    if (before && after) reopen = { at: after.t, equityBefore: before.equity!, equityAfter: after.equity!, jupiterAfter: after.jupiter!,
      equityGapPct: ((after.equity! - before.equity!) / before.equity!) * 100, jupiterVsEquityPct: ((after.jupiter! - after.equity!) / after.equity!) * 100 };
  }
  const last = tr.at(-1) ?? null;
  return { ticker, equityFeed: tr.find((r) => r.equityFeed)?.equityFeed ?? "", readings: tr.length, equityReadings: eq.length,
    firstAt: tr[0]?.t ?? null, lastAt: last?.t ?? null, entitled: last ? last.equityStatus === "ok" : false, lastEquityStatus: last?.equityStatus ?? null,
    closedReadings: closed.length, closedDistinctPrints: new Set(closed.map((r) => r.equityPublishMs)).size,
    confOpenPct: meanConf(open), confClosedPct: meanConf(closed), maxClosedDivergence: maxDiv, reopen, runs };
}
