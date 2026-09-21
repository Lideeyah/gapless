"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { fmtTs, fmtUsd } from "@/lib/format";

// Two lines over one window: Pyth's regular equity feed for the underlying (ink) and our own recorded
// xStock price from data/prices.csv (green). The shaded bands are not the recorder's labels: they are
// Pyth's keyless market_hours flag, so the third party also decides where "closed" is drawn.
export type WitnessRow = { t: string; jupiter: number | null; equity: number | null; marketOpen: boolean | null };
const CADENCE = 5 * 60 * 1000, GAP = 3 * CADENCE;

export default function PythChart({ rows, ticker, equityFeed, minReadings = 12 }: { rows: WitnessRow[]; ticker: string; equityFeed: string; minReadings?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(1120);
  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver((e) => setW(Math.max(320, e[0].contentRect.width)));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  const h = 256, padL = 8, padR = 88, top = 24, bottom = 56;
  const model = useMemo(() => {
    const t0 = rows[0] ? Date.parse(rows[0].t) : 0, t1 = rows.at(-1) ? Date.parse(rows.at(-1)!.t) : 1;
    const span = Math.max(1, t1 - t0);
    const x = (ms: number) => padL + ((ms - t0) / span) * (w - padL - padR);
    const priced = rows.flatMap((r) => [r.jupiter, r.equity]).filter((p): p is number => p !== null);
    let lo = Math.min(...priced), hi = Math.max(...priced);
    if (!Number.isFinite(lo) || !Number.isFinite(hi)) { lo = 0; hi = 1; }
    const pad = Math.max((hi - lo) * 0.15, hi * 0.002);
    lo = Math.max(0, lo - pad); hi += pad;
    const y = (p: number) => top + (1 - (p - lo) / (hi - lo)) * (h - top - bottom);
    const bands: { x0: number; x1: number; open: boolean; ms: number }[] = [];
    for (let i = 0; i < rows.length; i++) {
      const r = rows[i];
      if (r.marketOpen === null) continue;
      const ms = Date.parse(r.t), next = i + 1 < rows.length ? Date.parse(rows[i + 1].t) : ms + CADENCE;
      const x0 = x(ms), x1 = x(Math.min(next, t1 + CADENCE)), last = bands.at(-1);
      if (last && last.open === r.marketOpen && last.x1 >= x0 - 0.5) last.x1 = x1; else bands.push({ x0, x1, open: r.marketOpen, ms });
    }
    const marks: { x: number; label: string; anchorEnd: boolean }[] = [];
    const labelW = (t: string) => t.length * 7.3 + 16;
    for (const b of bands) {
      const label = `${b.open ? "NYSE open" : "NYSE closed"} · ${new Date(b.ms).toISOString().slice(5, 16).replace("T", " ")}`;
      const prev = marks.at(-1);
      if (prev && b.x0 - prev.x < labelW(prev.label)) continue;
      marks.push({ x: b.x0, label, anchorEnd: b.x0 + labelW(label) > w });
    }
    const path = (pick: (r: WitnessRow) => number | null) => {
      let d = "", prev: number | null = null;
      for (const r of rows) {
        const p = pick(r), ms = Date.parse(r.t);
        if (p === null) { prev = null; continue; }
        d += `${prev === null || ms - prev > GAP ? "M" : "L"}${x(ms).toFixed(1)} ${y(p).toFixed(1)} `;
        prev = ms;
      }
      return d;
    };
    const endOf = (pick: (r: WitnessRow) => number | null) => { const r = [...rows].reverse().find((z) => pick(z) !== null); return r ? { x: x(Date.parse(r.t)), y: y(pick(r)!), p: pick(r)! } : null; };
    return { x, y, lo, hi, bands, marks, dJ: path((r) => r.jupiter), dE: path((r) => r.equity), endJ: endOf((r) => r.jupiter), endE: endOf((r) => r.equity), priced: priced.length, count: rows.length };
  }, [rows, w]);

  const equityCount = rows.filter((r) => r.equity !== null).length;
  const tooShort = equityCount < minReadings || rows.filter((r) => r.jupiter !== null).length < minReadings;
  return (
    <div ref={ref} style={{ width: "100%" }}>
      <div className="mono secondary" style={{ display: "flex", justifyContent: "space-between", lineHeight: "24px" }}>
        <span>{rows[0] ? fmtTs(rows[0].t) : "no readings yet"}</span>
        <span>{ticker} · {equityFeed || "Pyth equity feed"} · {model.count} readings</span>
        <span>{rows.at(-1) ? fmtTs(rows.at(-1)!.t) : ""}</span>
      </div>
      {tooShort ? (
        <div style={{ height: h, display: "flex", alignItems: "center" }}>
          <p className="secondary" style={{ maxWidth: 560 }}>
            The witness window is too short to draw honestly. {equityCount} Pyth reading{equityCount === 1 ? "" : "s"} of {equityFeed || ticker} exist so far; the lines appear at {minReadings}. The recorder adds one every five minutes alongside the primary series.
          </p>
        </div>
      ) : (
        <svg width="100%" height={h} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" style={{ display: "block", overflow: "visible" }}>
          <defs><filter id="bleed2" x="-5%" y="-10%" width="110%" height="120%"><feGaussianBlur stdDeviation="5 0" /></filter></defs>
          <g filter="url(#bleed2)">
            {model.bands.map((b, i) => b.open ? null : <rect key={i} x={b.x0} y={top} width={Math.max(0.5, b.x1 - b.x0)} height={h - bottom - top} fill="#14161A" opacity={0.3} />)}
          </g>
          {model.marks.map((m, i) => (
            <g key={i}>
              <line x1={m.x} x2={m.x} y1={h - bottom + 8} y2={h - bottom + 16} stroke="#14161A" strokeOpacity={0.3} strokeWidth={1} />
              <text x={m.anchorEnd ? m.x - 4 : m.x + 4} y={h - bottom + 28} textAnchor={m.anchorEnd ? "end" : "start"} fill="#14161A" opacity={0.6} fontSize={12} fontFamily="var(--font-mono), monospace">{m.label}</text>
            </g>
          ))}
          <path d={model.dE} fill="none" stroke="#F4F1EA" strokeWidth={2.25} strokeOpacity={0.7} strokeLinejoin="round" strokeLinecap="round" />
          <path d={model.dE} fill="none" stroke="#14161A" strokeWidth={1.25} strokeLinejoin="round" strokeLinecap="round" />
          <path d={model.dJ} fill="none" stroke="#F4F1EA" strokeWidth={2.25} strokeOpacity={0.7} strokeLinejoin="round" strokeLinecap="round" />
          <path d={model.dJ} fill="none" stroke="#1F4D3D" strokeWidth={1.25} strokeLinejoin="round" strokeLinecap="round" />
          {model.endE && <g><circle cx={model.endE.x} cy={model.endE.y} r={2.5} fill="#14161A" stroke="#F4F1EA" strokeWidth={1.5} /><text x={model.endE.x + 10} y={model.endE.y + 4} fill="#14161A" fontSize={13} fontFamily="var(--font-mono), monospace" paintOrder="stroke" stroke="#F4F1EA" strokeWidth={3}>{fmtUsd(model.endE.p)} pyth</text></g>}
          {model.endJ && <g><circle cx={model.endJ.x} cy={model.endJ.y} r={2.5} fill="#1F4D3D" stroke="#F4F1EA" strokeWidth={1.5} /><text x={model.endJ.x + 10} y={model.endJ.y + 4} fill="#1F4D3D" fontSize={13} fontFamily="var(--font-mono), monospace" paintOrder="stroke" stroke="#F4F1EA" strokeWidth={3}>{fmtUsd(model.endJ.p)} {ticker}</text></g>}
          <text x={w - padR} y={top - 8} textAnchor="end" fill="#14161A" opacity={0.6} fontSize={12} fontFamily="var(--font-mono), monospace">high {fmtUsd(model.hi)} · low {fmtUsd(model.lo)}</text>
        </svg>
      )}
      <div className="mono faint" style={{ lineHeight: "24px" }}>green: {ticker}, our recorder, Jupiter · ink: {equityFeed || "Pyth equity feed"}, Pyth · shaded: NYSE closed by Pyth’s own market_hours flag · holes are missing readings</div>
    </div>
  );
}
