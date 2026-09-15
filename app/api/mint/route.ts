import { NextResponse } from "next/server";
import { readMintState } from "@/lib/mint";
export const dynamic = "force-dynamic";
export async function GET(req: Request) {
  const mint = new URL(req.url).searchParams.get("mint");
  if (!mint) return NextResponse.json({ error: "missing mint" }, { status: 400 });
  try { return NextResponse.json(await readMintState(mint), { headers: { "Cache-Control": "no-store" } }); }
  catch (e) { return NextResponse.json({ error: (e as Error).message }, { status: 502 }); }
}
