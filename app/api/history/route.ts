import { NextResponse } from "next/server";
import { fetchCsv, parseCsv, largestGap, stats, closures } from "@/lib/csv";
import regimes from "@/keeper/regimes.json";

export const dynamic = "force-dynamic"; // every request re-reads data/prices.csv from GitHub

export async function GET() {
  try {
    const rows = parseCsv(await fetchCsv());
    const { _comment, ...rules } = regimes as Record<string, unknown>;
    void _comment;
    return NextResponse.json({ rows: rows.map((r) => ({ t: r.t, ticker: r.ticker, price: r.price, session: r.session })),
      stats: stats(rows), gap: largestGap(rows), closures: closures(rows), regimes: rules }, { headers: { "Cache-Control": "no-store" } });
  } catch (e) {
    return NextResponse.json({ error: (e as Error).message }, { status: 502 });
  }
}
