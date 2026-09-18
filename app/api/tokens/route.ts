import { NextResponse } from "next/server";
export const dynamic = "force-dynamic";
// Identify which of a wallet's Token-2022 mints are xStocks or PreStocks, via Jupiter's token search.
export async function GET(req: Request) {
  const mints = (new URL(req.url).searchParams.get("mints") ?? "").split(",").filter(Boolean).slice(0, 100);
  if (mints.length === 0) return NextResponse.json([]);
  const out: { mint: string; symbol: string; name: string; decimals: number }[] = [];
  for (let i = 0; i < mints.length; i += 20) {
    const res = await fetch(`https://api.jup.ag/tokens/v2/search?query=${mints.slice(i, i + 20).join(",")}`, { cache: "no-store", headers: { "User-Agent": "gapless-web" } });
    if (!res.ok) return NextResponse.json({ error: `jupiter tokens HTTP ${res.status}` }, { status: 502 });
    for (const t of (await res.json()) as { id: string; symbol: string; name: string; decimals: number }[])
      if (/xstock|prestocks/i.test(t.name)) out.push({ mint: t.id, symbol: t.symbol, name: t.name, decimals: t.decimals });
  }
  return NextResponse.json(out, { headers: { "Cache-Control": "no-store" } });
}
