// Reads the recorder's data/prices.csv from GitHub at request time and derives every figure the
// interface shows. Nothing here is cached beyond the fetch itself; nothing is interpolated.

export type Reading = { t: string; ms: number; ticker: string; mint: string; price: number | null; session: Session; source: string };
export type Session = "open" | "overnight" | "weekend";

const RAW_URL = process.env.PRICES_CSV_URL ?? "https://raw.githubusercontent.com/Lideeyah/gapless/main/data/prices.csv";
export const CADENCE_MS = 5 * 60 * 1000;
export const GAP_MS = 3 * CADENCE_MS; // a hole longer than this is drawn as a hole, never bridged

export async function fetchCsv(): Promise<string> {
  const res = await fetch(`${RAW_URL}?t=${Date.now()}`, { cache: "no-store", headers: { "User-Agent": "gapless-web" } });
  if (!res.ok) throw new Error(`prices.csv fetch failed: HTTP ${res.status}`);
  return res.text();
}

export function parseCsv(text: string): Reading[] {
  const lines = text.trim().split("\n");
  const out: Reading[] = [];
  for (const line of lines.slice(1)) {
    const [t, ticker, mint, price, session, source] = line.split(",");
    if (!t || !ticker) continue;
    const ms = Date.parse(t);
    if (!Number.isFinite(ms)) continue;
    const p = price === "" ? null : Number(price);
    out.push({ t, ms, ticker, mint, price: Number.isFinite(p as number) ? p : null, session: session as Session, source });
  }
  return out.sort((a, b) => a.ms - b.ms);
}

export type Closure = { session: Session; startMs: number; endMs: number; readings: number; full: boolean };

/** Contiguous runs of non-open readings, by distinct timestamp. `full` means open readings exist on both sides. */
export function closures(rows: Reading[]): Closure[] {
  const byTs = new Map<number, Session>();
  for (const r of rows) byTs.set(r.ms, r.session);
  const ts = [...byTs.keys()].sort((a, b) => a - b);
  const out: Closure[] = [];
  let cur: Closure | null = null;
  let seenOpen = false;
  for (const ms of ts) {
    const s = byTs.get(ms)!;
    if (s === "open") {
      if (cur) { cur.full = seenOpen; out.push(cur); cur = null; }
      seenOpen = true;
    } else if (!cur) cur = { session: s, startMs: ms, endMs: ms, readings: 1, full: false };
    else { cur.endMs = ms; cur.readings += 1; if (s === "weekend") cur.session = "weekend"; }
  }
  if (cur) out.push(cur); // still inside a closure: not full
  return out;
}

export type Gap = { ticker: string; closeAt: string; closePrice: number; extremeAt: string; extremePrice: number; reopenAt: string; reopenPrice: number; movePct: number; extremePct: number; session: Session };

/** Largest gap across full closures: last open reading before, extreme during, first open reading after. */
export function largestGap(rows: Reading[]): Gap | null {
  let best: Gap | null = null;
  for (const c of closures(rows).filter((c) => c.full)) {
    for (const ticker of new Set(rows.map((r) => r.ticker))) {
      const tr = rows.filter((r) => r.ticker === ticker && r.price !== null);
      const before = [...tr].reverse().find((r) => r.session === "open" && r.ms < c.startMs);
      const after = tr.find((r) => r.session === "open" && r.ms > c.endMs);
      const during = tr.filter((r) => r.ms >= c.startMs && r.ms <= c.endMs);
      if (!before || !after || during.length === 0) continue;
      const extreme = during.reduce((m, r) => (Math.abs(r.price! - before.price!) > Math.abs(m.price! - before.price!) ? r : m), during[0]);
      const g: Gap = { ticker, closeAt: before.t, closePrice: before.price!, extremeAt: extreme.t, extremePrice: extreme.price!, reopenAt: after.t, reopenPrice: after.price!,
        movePct: ((after.price! - before.price!) / before.price!) * 100, extremePct: ((extreme.price! - before.price!) / before.price!) * 100, session: c.session };
      if (!best || Math.abs(g.movePct) > Math.abs(best.movePct)) best = g;
    }
  }
  return best;
}

export type Stats = {
  computedAt: string; firstAt: string | null; lastAt: string | null; rows: number; readings: number; errorRows: number;
  tickers: string[]; closedShareOfReadings: number; closedShareOfMovement: number | null; closureCount: number; fullClosureCount: number;
  currentSession: Session | null; inClosureSince: string | null; closureReadings: number;
  perTicker: Record<string, { readings: number; last: number | null; lastAt: string | null; minClosed: number | null; maxClosed: number | null; closedMove: number; totalMove: number }>;
};

export function stats(rows: Reading[]): Stats {
  const tickers = [...new Set(rows.map((r) => r.ticker))].sort();
  const readings = new Set(rows.map((r) => r.ms)).size;
  const closed = rows.filter((r) => r.session !== "open");
  const cls = closures(rows);
  const last = rows.at(-1) ?? null;
  const open = cls.at(-1) && !cls.at(-1)!.full && cls.at(-1)!.endMs === last?.ms ? cls.at(-1)! : null;
  const perTicker: Stats["perTicker"] = {};
  let closedMove = 0, totalMove = 0;
  for (const tk of tickers) {
    const tr = rows.filter((r) => r.ticker === tk);
    let cm = 0, tm = 0;
    for (let i = 1; i < tr.length; i++) {
      const a = tr[i - 1], b = tr[i];
      if (a.price === null || b.price === null || b.ms - a.ms > GAP_MS) continue; // holes contribute nothing
      const d = Math.abs(b.price - a.price);
      tm += d; if (a.session !== "open") cm += d;
    }
    closedMove += cm; totalMove += tm;
    const priced = tr.filter((r) => r.price !== null);
    const closedPriced = priced.filter((r) => r.session !== "open");
    perTicker[tk] = { readings: tr.length, last: priced.at(-1)?.price ?? null, lastAt: priced.at(-1)?.t ?? null,
      minClosed: closedPriced.length ? Math.min(...closedPriced.map((r) => r.price!)) : null,
      maxClosed: closedPriced.length ? Math.max(...closedPriced.map((r) => r.price!)) : null, closedMove: cm, totalMove: tm };
  }
  return { computedAt: new Date().toISOString().replace(/\.\d+Z$/, "Z"), firstAt: rows[0]?.t ?? null, lastAt: last?.t ?? null, rows: rows.length, readings,
    errorRows: rows.filter((r) => r.source === "error").length, tickers,
    closedShareOfReadings: rows.length ? closed.length / rows.length : 0, closedShareOfMovement: totalMove > 0 && rows.some((r) => r.session === "open") ? closedMove / totalMove : null, // meaningless until both regimes exist in the window
    closureCount: cls.length, fullClosureCount: cls.filter((c) => c.full).length, currentSession: last?.session ?? null,
    inClosureSince: open ? new Date(open.startMs).toISOString().replace(/\.\d+Z$/, "Z") : null, closureReadings: open?.readings ?? 0, perTicker };
}
