import { NextResponse } from "next/server";
import { fetchPythCsv, parsePythCsv, witness } from "@/lib/pyth";
import cfg from "@/keeper/pyth.json";

export const dynamic = "force-dynamic"; // every request re-reads data/pyth.csv from GitHub

export async function GET(req: Request) {
  const ticker = new URL(req.url).searchParams.get("ticker") ?? "TSLAx";
  try {
    const rows = parsePythCsv(await fetchPythCsv());
    const { _comment, ...config } = cfg as Record<string, unknown>;
    void _comment;
    return NextResponse.json({
      rows: rows.filter((r) => r.ticker === ticker).map((r) => ({ t: r.t, jupiter: r.jupiter, equity: r.equity, equityConf: r.equityConf, marketOpen: r.marketOpen, equityStatus: r.equityStatus })),
      witness: witness(rows, ticker), config,
    }, { headers: { "Cache-Control": "no-store" } });
  } catch (e) {
    return NextResponse.json({ error: (e as Error).message }, { status: 502 });
  }
}
