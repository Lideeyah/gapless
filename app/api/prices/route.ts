import { NextResponse } from "next/server";
export const dynamic = "force-dynamic";
// Jupiter Price API v3, keyless. Same source the recorder and the keeper use.
export async function GET(req: Request) {
  const mints = new URL(req.url).searchParams.get("mints") ?? "";
  if (!mints) return NextResponse.json({});
  const res = await fetch(`https://api.jup.ag/price/v3?ids=${encodeURIComponent(mints)}`, { cache: "no-store", headers: { "User-Agent": "gapless-web" } });
  if (!res.ok) return NextResponse.json({ error: `jupiter price HTTP ${res.status}` }, { status: 502 });
  const data = (await res.json()) as Record<string, { usdPrice?: number; scaledUiConfig?: { multiplier?: number } }>;
  const out: Record<string, { price: number; multiplier: number }> = {};
  for (const [m, v] of Object.entries(data)) if (typeof v?.usdPrice === "number") out[m] = { price: v.usdPrice, multiplier: v.scaledUiConfig?.multiplier ?? 1 };
  return NextResponse.json(out, { headers: { "Cache-Control": "no-store" } });
}
