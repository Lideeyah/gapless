"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { fmtTs, fmtUsd } from "@/lib/format";

export type Row = { t: string; ticker: string; price: number | null; session: "open" | "overnight" | "weekend" };
const CADENCE = 5 * 60 * 1000, GAP = 3 * CADENCE, MIN_READINGS = 20;

export default function Timeline({ rows, ticker, floor, firstAt, lastAt }: { rows: Row[]; ticker: string; floor: number | null; firstAt: string | null; lastAt: string | null }) {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(1120);
  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver((e) => setW(Math.max(320, e[0].contentRect.width)));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  const h = 264, padL = 8, padR = 8, top = 24, bottom = 48;
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
    lo -= pad; hi += pad;
    const y = (p: number) => top + (1 - (p - lo) / (hi - lo)) * (h - top - bottom);
    // session bands from distinct timestamps, in order
    const stamps = [...new Map(rows.map((r) => [Date.parse(r.t), r.session])).entries()].sort((a, b) => a[0] - b[0]);
    const bands: { x0: number; x1: number; s: string }[] = [];
    for (let i = 0; i < stamps.length; i++) {
      const [ms, s] = stamps[i];
      const next = i + 1 < stamps.length ? stamps[i + 1][0] : ms + CADENCE;
      const x0 = x(ms), x1 = x(Math.min(next, t1 + CADENCE));
      const last = bands.at(-1);
      if (last && last.s === s && last.x1 >= x0 - 0.5) last.x1 = x1; else bands.push({ x0, x1, s });
    }
    // price path, broken at holes (missing price or gap > 3 readings)
    let d = ""; let prev: { ms: number } | null = null;
    for (const r of series) {
      const ms = Date.parse(r.t);
      if (r.price === null) { prev = null; continue; }
      const cmd = prev && ms - prev.ms <= GAP ? "L" : "M";
      d += `${cmd}${x(ms).toFixed(1)} ${y(r.price).toFixed(1)} `;
      prev = { ms };
    }
    return { x, y, lo, hi, bands, d, readings: series.length, priced: priced.length };
  }, [rows, series, w, floor, firstAt, lastAt]);

  const tooShort = model.priced < MIN_READINGS;
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
            {firstAt ? ` since ${fmtTs(firstAt)}` : ""}; the line appears at {MIN_READINGS}. The recorder adds one every five minutes, best-effort.
          </p>
        </div>
      ) : (
        <svg width="100%" height={h} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" style={{ display: "block", overflow: "visible" }}>
          <defs>
            <filter id="bleed" x="-2%" y="-10%" width="104%" height="120%"><feGaussianBlur stdDeviation="4 0" /></filter>
          </defs>
          <g filter="url(#bleed)">
            {model.bands.map((b, i) => b.s === "open" ? null : (
              <rect key={i} x={b.x0} y={0} width={Math.max(0.5, b.x1 - b.x0)} height={h - bottom + 12} fill="#14161A" opacity={b.s === "weekend" ? 1 : 0.3} />
            ))}
          </g>
          {floor !== null && Number.isFinite(floor) && (
            <g>
              <line x1={padL} x2={w - padR} y1={model.y(floor)} y2={model.y(floor)} stroke="#F4F1EA" strokeWidth={3} opacity={0.9} />
              <line x1={padL} x2={w - padR} y1={model.y(floor)} y2={model.y(floor)} stroke="#1F4D3D" strokeWidth={1} opacity={0.3} />
            </g>
          )}
          <path d={model.d} fill="none" stroke="#F4F1EA" strokeWidth={3.5} strokeLinejoin="round" strokeLinecap="round" pathLength={1} className="draw" />
          <path d={model.d} fill="none" stroke="#1F4D3D" strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" pathLength={1} className="draw" />
          <text x={padL} y={h - 8} fill="#14161A" opacity={0.6} fontSize={13} fontFamily="var(--font-mono), monospace">{fmtUsd(model.lo)}</text>
          <text x={padL} y={top - 8} fill="#14161A" opacity={0.6} fontSize={13} fontFamily="var(--font-mono), monospace">{fmtUsd(model.hi)}</text>
          {floor !== null && Number.isFinite(floor) && (
            <text x={w - padR} y={model.y(floor) - 6} textAnchor="end" fill="#1F4D3D" fontSize={13} fontFamily="var(--font-mono), monospace">floor {fmtUsd(floor)}</text>
          )}
        </svg>
      )}
      <div className="mono faint" style={{ lineHeight: "24px" }}>bare paper: open · ink 30%: overnight · ink: weekend · session labels are the recorder’s own</div>
    </div>
  );
}
