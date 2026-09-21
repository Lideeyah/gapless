"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { fmtTs, fmtUsd } from "@/lib/format";

export type Row = { t: string; ticker: string; price: number | null; session: "open" | "overnight" | "weekend" | "closed" };
const CADENCE = 5 * 60 * 1000, GAP = 3 * CADENCE, MIN_READINGS = 20;

export default function Timeline({ rows, ticker, floor, firstAt, lastAt, minReadings = MIN_READINGS }: { rows: Row[]; ticker: string; floor: number | null; firstAt: string | null; lastAt: string | null; minReadings?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(1120);
  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver((e) => setW(Math.max(320, e[0].contentRect.width)));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  const h = 256, padL = 8, padR = 72, top = 24, bottom = 56;
  const series = useMemo(() => rows.filter((r) => r.ticker === ticker), [rows, ticker]);
  const model = useMemo(() => {
    const t0 = firstAt ? Date.parse(firstAt) : 0, t1 = lastAt ? Date.parse(lastAt) : 1;
    const span = Math.max(1, t1 - t0);
    const x = (ms: number) => padL + ((ms - t0) / span) * (w - padL - padR);
    const priced = series.filter((r) => r.price !== null).map((r) => r.price!);
    let lo = Math.min(...priced), hi = Math.max(...priced);
    if (floor !== null && Number.isFinite(floor)) { lo = Math.min(lo, floor); hi = Math.max(hi, floor); }
    if (!Number.isFinite(lo) || !Number.isFinite(hi)) { lo = 0; hi = 1; }
    const pad = Math.max((hi - lo) * 0.15, hi * 0.002);
    lo = Math.max(0, lo - pad); hi += pad; // a price axis never goes below zero
    const y = (p: number) => top + (1 - (p - lo) / (hi - lo)) * (h - top - bottom);
    // session bands from distinct timestamps, in order
    // bands come from the selected ticker's own labels when it has no market session; otherwise from the session-bearing rows
    const noMarket = series.length > 0 && series.every((r) => r.session === "closed");
    const bandRows = noMarket ? series : rows.filter((r) => r.session !== "closed");
    const stamps = [...new Map(bandRows.map((r) => [Date.parse(r.t), r.session])).entries()].sort((a, b) => a[0] - b[0]);
    const bands: { x0: number; x1: number; s: string; ms: number }[] = [];
    for (let i = 0; i < stamps.length; i++) {
      const [ms, s] = stamps[i];
      const next = i + 1 < stamps.length ? stamps[i + 1][0] : ms + CADENCE;
      const x0 = x(ms), x1 = x(Math.min(next, t1 + CADENCE));
      const last = bands.at(-1);
      if (last && last.s === s && last.x1 >= x0 - 0.5) last.x1 = x1; else bands.push({ x0, x1, s, ms });
    }
    // one label per session change, thinned so they never collide
    const marks: { x: number; label: string; anchorEnd: boolean }[] = [];
    const labelW = (t: string) => t.length * 7.3 + 16;
    for (const b of bands) {
      const d = new Date(b.ms);
      const label = `${b.s === "closed" ? "no market session" : b.s} · ${d.toISOString().slice(5, 16).replace("T", " ")}`;
      const prev = marks.at(-1);
      if (prev && b.x0 - prev.x < labelW(prev.label)) continue; // would collide with the previous label
      marks.push({ x: b.x0, label, anchorEnd: b.x0 + labelW(label) > w });
    }
    const lastPriced = [...series].reverse().find((r) => r.price !== null) ?? null;
    // price path, broken at holes (missing price or gap > 3 readings); each hole gets a label
    let d = ""; let prev: { ms: number } | null = null;
    const holes: { x0: number; x1: number; label: string }[] = [];
    const holeW = (t: string) => t.length * 7.3 + 24;
    for (const r of series) {
      const ms = Date.parse(r.t);
      if (r.price === null) { prev = null; continue; }
      const isHole = Boolean(prev && ms - prev.ms > GAP);
      if (isHole) {
        const mins = Math.round((ms - prev!.ms) / 60000);
        const label = `no readings · ${mins >= 60 ? `${Math.floor(mins / 60)}h ${mins % 60}m` : `${mins}m`}`;
        const x0 = x(prev!.ms), x1 = x(ms), last = holes.at(-1);
        // label only holes wide enough to hold their label, and never on top of the previous label
        if (x1 - x0 >= holeW(label) && (!last || (x0 + x1) / 2 - (last.x0 + last.x1) / 2 > holeW(last.label))) holes.push({ x0, x1, label });
      }
      d += `${isHole || !prev ? "M" : "L"}${x(ms).toFixed(1)} ${y(r.price).toFixed(1)} `;
      prev = { ms };
    }
    return { x, y, lo, hi, bands, marks, holes, d, readings: series.length, priced: priced.length, end: lastPriced ? { x: x(Date.parse(lastPriced.t)), y: y(lastPriced.price!), p: lastPriced.price! } : null };
  }, [rows, series, w, floor, firstAt, lastAt]);

  const tooShort = model.priced < minReadings;
  return (
    <div ref={ref} style={{ width: "100%" }}>
      <div className="mono secondary" style={{ display: "flex", justifyContent: "space-between", lineHeight: "24px" }}>
        <span>{firstAt ? fmtTs(firstAt) : "no readings yet"}</span>
        <span>{ticker} · {model.readings} readings</span>
        <span>{lastAt ? fmtTs(lastAt) : ""}</span>
      </div>
      {tooShort ? (
        <div style={{ height: h, display: "flex", alignItems: "center" }}>
          <p className="secondary" style={{ maxWidth: 560 }}>
            The window is too short to draw honestly. {model.priced} priced reading{model.priced === 1 ? "" : "s"} of {ticker} exist so far
            {firstAt ? ` since ${fmtTs(firstAt)}` : ""}; the line appears at {minReadings}. The recorder adds one every five minutes, best-effort.
          </p>
        </div>
      ) : (
        <svg width="100%" height={h} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" style={{ display: "block", overflow: "visible" }}>
          <defs>
            <filter id="bleed" x="-5%" y="-10%" width="110%" height="120%"><feGaussianBlur stdDeviation="5 0" /></filter>
          </defs>
          <g filter="url(#bleed)">
            {model.bands.map((b, i) => b.s === "open" ? null : (
              <rect key={i} x={b.x0} y={top} width={Math.max(0.5, b.x1 - b.x0)} height={h - bottom - top} fill="#14161A" opacity={b.s === "weekend" || b.s === "closed" ? 1 : 0.3} />
            ))}
          </g>
          {model.holes.map((g, i) => (
            <text key={`hole-${i}`} x={(g.x0 + g.x1) / 2} y={(top + h - bottom) / 2 + 4} textAnchor="middle" fill="#14161A" opacity={0.6} fontSize={12} fontFamily="var(--font-mono), monospace" paintOrder="stroke" stroke="#F4F1EA" strokeWidth={3}>{g.label}</text>
          ))}
          {model.marks.map((m, i) => (
            <g key={i}>
              <line x1={m.x} x2={m.x} y1={h - bottom + 8} y2={h - bottom + 16} stroke="#14161A" strokeOpacity={0.3} strokeWidth={1} />
              <text x={m.anchorEnd ? m.x - 4 : m.x + 4} y={h - bottom + 28} textAnchor={m.anchorEnd ? "end" : "start"} fill="#14161A" opacity={0.6} fontSize={12} fontFamily="var(--font-mono), monospace">{m.label}</text>
            </g>
          ))}
          {floor !== null && Number.isFinite(floor) && (
            <g>
              <line x1={padL} x2={w - padR} y1={model.y(floor)} y2={model.y(floor)} stroke="#F4F1EA" strokeWidth={3} opacity={0.9} />
              <line x1={padL} x2={w - padR} y1={model.y(floor)} y2={model.y(floor)} stroke="#1F4D3D" strokeWidth={1} opacity={0.3} />
            </g>
          )}
          <path d={model.d} fill="none" stroke="#F4F1EA" strokeWidth={2.25} strokeOpacity={0.7} strokeLinejoin="round" strokeLinecap="round" pathLength={1} className="draw" />
          <path d={model.d} fill="none" stroke="#1F4D3D" strokeWidth={1.25} strokeLinejoin="round" strokeLinecap="round" pathLength={1} className="draw" />
          {model.end && (<g><circle cx={model.end.x} cy={model.end.y} r={2.5} fill="#1F4D3D" stroke="#F4F1EA" strokeWidth={1.5} /><text x={model.end.x + 10} y={model.end.y + 4} textAnchor="start" fill="#1F4D3D" fontSize={13} fontFamily="var(--font-mono), monospace">{fmtUsd(model.end.p)}</text></g>)}
          
          <text x={w - padR} y={top - 8} textAnchor="end" fill="#14161A" opacity={0.6} fontSize={12} fontFamily="var(--font-mono), monospace">high {fmtUsd(model.hi)} · low {fmtUsd(model.lo)}</text>
          {floor !== null && Number.isFinite(floor) && (
            <text x={w - padR} y={model.y(floor) - 6} textAnchor="end" fill="#1F4D3D" fontSize={13} fontFamily="var(--font-mono), monospace" paintOrder="stroke" stroke="#F4F1EA" strokeWidth={3}>floor {fmtUsd(floor)}</text>
          )}
        </svg>
      )}
      <div className="mono faint" style={{ lineHeight: "24px" }}>paper: open · ink 30%: overnight · ink: weekend, or no market session at all · holes are missing readings · session labels are the recorder’s own</div>
    </div>
  );
}
