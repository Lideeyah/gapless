import { NextResponse } from "next/server";
import { updateOrders } from "@/lib/github";
import { verifyRevoke } from "@/lib/chain";

export const dynamic = "force-dynamic";

export async function POST(req: Request) {
  const { id, owner, revokeSig } = (await req.json()) as { id?: string; owner?: string; revokeSig?: string };
  if (!id || !owner || !revokeSig) return NextResponse.json({ error: "missing id, owner or revokeSig" }, { status: 400 });
  try {
    let tokenAccount = "";
    let err: string | null = null;
    await updateOrders((s) => {
      const o = s.orders.find((x) => x.id === id && x.owner_pubkey === owner);
      if (!o) { err = "order not found"; return "noop"; }
      if (o.status === "executing") { err = "order is executing; wait for the keeper to settle it"; return "noop"; }
      tokenAccount = o.token_account;
      o.status = "revoked"; o.revoke_sig = revokeSig;
      o.last_decision = "revoked by owner"; o.last_checked_at = new Date().toISOString().replace(/\.\d+Z$/, "Z");
      return `revoke ${o.ticker} ${revokeSig.slice(0, 8)}`;
    }).catch((e) => { err = (e as Error).message; });
    if (err) return NextResponse.json({ error: err }, { status: 422 });
    const bad = await verifyRevoke(revokeSig, owner, tokenAccount);
    // Recorded even if the RPC lags; the keeper re-reads delegation from the chain before ever executing.
    return NextResponse.json({ ok: true, verified: bad === null, note: bad });
  } catch (e) {
    return NextResponse.json({ error: (e as Error).message }, { status: 502 });
  }
}
