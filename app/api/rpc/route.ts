import { NextResponse } from "next/server";
import { RPC_URL } from "@/lib/chain";
export const dynamic = "force-dynamic";
// Same-origin JSON-RPC relay. The public Solana RPC refuses some calls made directly from a browser
// page (403), but accepts them from a server. No key, no state: the body is forwarded as-is.
export async function POST(req: Request) {
  const body = await req.text();
  const res = await fetch(RPC_URL, { method: "POST", headers: { "Content-Type": "application/json" }, body, cache: "no-store" });
  return new NextResponse(await res.text(), { status: res.status, headers: { "Content-Type": "application/json", "Cache-Control": "no-store" } });
}
