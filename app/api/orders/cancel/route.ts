import { NextResponse } from "next/server";
import { readOrders, updateOrders } from "@/lib/github";
import { verifyRevoke } from "@/lib/chain";

export const dynamic = "force-dynamic";

export async function POST(req: Request) {
  const text = await req.text();
  if (text.length > 2048) return NextResponse.json({ error: "body too large" }, { status: 413 });
  let body: { id?: string; owner?: string; revokeSig?: string };
  try { body = JSON.parse(text); } catch { return NextResponse.json({ error: "invalid JSON" }, { status: 400 }); }
  const { id, owner, revokeSig } = body;
  if (!id || !owner || !revokeSig || !/^[1-9A-HJ-NP-Za-km-z]{64,120}$/.test(revokeSig)) return NextResponse.json({ error: "missing or malformed id, owner or revokeSig" }, { status: 400 });
  try {
    // Verify on chain FIRST. Nothing is written for a revoke the chain does not show, signed by this owner, for this account.
    const { store } = await readOrders();
    const target = store.orders.find((x) => x.id === id && x.owner_pubkey === owner);
    if (!target) return NextResponse.json({ error: "order not found" }, { status: 404 });
    if (!["armed", "triggered", "failed"].includes(target.status)) return NextResponse.json({ error: `order is ${target.status}; only an open order can be revoked` }, { status: 422 });
    const bad = await verifyRevoke(revokeSig, owner, target.token_account);
    if (bad) return NextResponse.json({ error: bad }, { status: 422 });
    const written = await updateOrders((s) => {
      const o = s.orders.find((x) => x.id === id && x.owner_pubkey === owner);
      if (!o || !["armed", "triggered", "failed"].includes(o.status)) return null;
      o.status = "revoked"; o.revoke_sig = revokeSig;
      o.last_decision = "revoked by owner"; o.last_checked_at = new Date().toISOString().replace(/\.\d+Z$/, "Z");
      return `revoke ${o.ticker} ${revokeSig.slice(0, 8)}`;
    });
    if (!written) return NextResponse.json({ error: "order changed before the revoke could be recorded; reload" }, { status: 409 });
    return NextResponse.json({ ok: true, verified: true });
  } catch (e) {
    return NextResponse.json({ error: (e as Error).message }, { status: 502 });
  }
}
