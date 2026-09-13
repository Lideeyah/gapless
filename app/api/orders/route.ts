import { NextResponse } from "next/server";
import { readOrders, updateOrders, storeConfigured, type Order } from "@/lib/github";
import { verifyApprove, verifyOrderMemo, KEEPER_PUBKEY } from "@/lib/chain";

export const dynamic = "force-dynamic";

export async function GET(req: Request) {
  const owner = new URL(req.url).searchParams.get("owner");
  try {
    const { store } = await readOrders();
    const orders = owner ? store.orders.filter((o) => o.owner === owner) : [];
    return NextResponse.json({ orders, storeConfigured: storeConfigured(), keeper: KEEPER_PUBKEY || null }, { headers: { "Cache-Control": "no-store" } });
  } catch (e) {
    return NextResponse.json({ error: (e as Error).message }, { status: 502 });
  }
}

export async function POST(req: Request) {
  const b = (await req.json()) as Partial<Order>;
  const need = ["owner", "ticker", "mint", "tokenAccount", "decimals", "quantityRaw", "quantityUi", "floorUsd", "approveSig", "orderSig"] as const;
  for (const k of need) if (b[k] === undefined || b[k] === null || b[k] === "") return NextResponse.json({ error: `missing ${k}` }, { status: 400 });
  if (!KEEPER_PUBKEY) return NextResponse.json({ error: "keeper delegate is not configured on the server" }, { status: 503 });
  if (!(Number(b.floorUsd) > 0) || !/^\d+$/.test(String(b.quantityRaw)) || BigInt(String(b.quantityRaw)) <= 0n) return NextResponse.json({ error: "floor and quantity must be positive" }, { status: 400 });
  try {
    const bad = (await verifyApprove(b.approveSig!, b.owner!, b.tokenAccount!, String(b.quantityRaw)))
      ?? (await verifyOrderMemo(b.orderSig!, b.owner!, { gapless: 1, mint: b.mint, tokenAccount: b.tokenAccount, quantityRaw: b.quantityRaw, floorUsd: b.floorUsd, delegate: KEEPER_PUBKEY, approveSig: b.approveSig }));
    if (bad) return NextResponse.json({ error: bad }, { status: 422 });
    const order: Order = { id: b.orderSig!, owner: b.owner!, ticker: b.ticker!, mint: b.mint!, tokenAccount: b.tokenAccount!, decimals: Number(b.decimals),
      quantityRaw: String(b.quantityRaw), quantityUi: Number(b.quantityUi), remainingRaw: String(b.quantityRaw), floorUsd: Number(b.floorUsd), delegate: KEEPER_PUBKEY,
      status: "armed", createdAt: new Date().toISOString().replace(/\.\d+Z$/, "Z"), approveSig: b.approveSig!, orderSig: b.orderSig!, revokeSig: null, breachCount: 0, fills: [], lastCheck: null };
    await updateOrders((s) => {
      if (s.orders.some((o) => o.id === order.id)) return "order already recorded";
      for (const o of s.orders) if (o.status === "armed" && o.tokenAccount === order.tokenAccount) { o.status = "cancelled"; o.lastCheck = { at: order.createdAt, session: "-", price: null, decision: "superseded: a newer approve replaced this delegation" }; }
      s.orders.push(order);
      return `order ${order.ticker} floor ${order.floorUsd} ${order.createdAt}`;
    });
    return NextResponse.json({ order });
  } catch (e) {
    return NextResponse.json({ error: (e as Error).message }, { status: 502 });
  }
}
