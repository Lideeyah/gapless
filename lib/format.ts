// Every number on screen passes through here so nothing can render undefined, NaN or null.
const ok = (n: unknown): n is number => typeof n === "number" && Number.isFinite(n);
export const fmtUsd = (n: unknown, d = 2) => (ok(n) ? n.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d }) : "0." + "0".repeat(d));
export const fmtQty = (n: unknown) => (ok(n) ? n.toLocaleString("en-US", { maximumFractionDigits: 6 }) : "0");
export const fmtPct = (n: unknown, d = 2) => (ok(n) ? `${n > 0 ? "+" : ""}${n.toFixed(d)}%` : "0.00%");
export const fmtShare = (n: unknown) => (ok(n) ? `${(n * 100).toFixed(0)}%` : "0%");
export const fmtTs = (iso: unknown) => (typeof iso === "string" && iso ? iso.replace("T", " ").replace("Z", " UTC") : "—");
export const short = (s: unknown) => (typeof s === "string" && s.length > 12 ? `${s.slice(0, 4)}…${s.slice(-4)}` : typeof s === "string" && s ? s : "—");
export const solscanTx = (sig: string) => `https://solscan.io/tx/${sig}`;
