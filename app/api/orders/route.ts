import { NextResponse } from "next/server";
import { readOrders, updateOrders, storeConfigured, type Order } from "@/lib/github";
import { verifyApprove, verifyOrderMemo, KEEPER_PUBKEY } from "@/lib/chain";

export const dynamic = "force-dynamic";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const DECIMAL = /^\d+(\.\d+)?$/;

export async function GET(req: Request) {
  const owner = new URL(req.url).searchParams.get("owner");
  try {
    const { store } = await readOrders();
    return NextResponse.json({ orders: owner ? store.orders.filter((o) => o.owner_pubkey === owner) : [], storeConfigured: storeConfigured(), keeper: KEEPER_PUBKEY || null },
      { headers: { "Cache-Control": "no-store" } });
  } catch (e) {
    return NextResponse.json({ error: (e as Error).message }, { status: 502 });
  }
}

type Body = { id: string; owner_pubkey: string; ticker: string; mint: string; token_account: string; decimals: number; quantity_raw: string; floor_price_usd: string; delegation_sig: string; order_sig: string };

export async function POST(req: Request) {
  const b = (await req.json()) as Partial<Body>;
  for (const k of ["id", "owner_pubkey", "ticker", "mint", "token_account", "decimals", "quantity_raw", "floor_price_usd", "delegation_sig", "order_sig"] as const)
    if (b[k] === undefined || b[k] === null || b[k] === "") return NextResponse.json({ error: `missing ${k}` }, { status: 400 });
  if (!KEEPER_PUBKEY) return NextResponse.json({ error: "keeper delegate is not configured on the server" }, { status: 503 });
  if (!UUID.test(b.id!)) return NextResponse.json({ error: "id must be a v4 uuid" }, { status: 400 });
  if (!/^\d+$/.test(b.quantity_raw!) || BigInt(b.quantity_raw!) <= 0n) return NextResponse.json({ error: "quantity_raw must be a positive integer string" }, { status: 400 });
  if (!DECIMAL.test(b.floor_price_usd!) || Number(b.floor_price_usd) <= 0) return NextResponse.json({ error: "floor_price_usd must be a positive decimal string" }, { status: 400 });
  try {
    const bad = (await verifyApprove(b.delegation_sig!, b.owner_pubkey!, b.token_account!, b.quantity_raw!))
      ?? (await verifyOrderMemo(b.order_sig!, b.owner_pubkey!, { gapless: 1, id: b.id, mint: b.mint, token_account: b.token_account, quantity_raw: b.quantity_raw, floor_price_usd: b.floor_price_usd, delegate: KEEPER_PUBKEY, delegation_sig: b.delegation_sig }));
    if (bad) return NextResponse.json({ error: bad }, { status: 422 });
    const now = new Date().toISOString().replace(/\.\d+Z$/, "Z");
    const order: Order = { id: b.id!, owner_pubkey: b.owner_pubkey!, mint: b.mint!, ticker: b.ticker!, token_account: b.token_account!, quantity_raw: b.quantity_raw!, decimals: Number(b.decimals),
      floor_price_usd: b.floor_price_usd!, status: "armed", breach_count: 0, last_checked_at: null, delegation_sig: b.delegation_sig!, fill_sig: null, fill_price_usd: null, filled_at: null, failure_reason: null, created_at: now,
      order_sig: b.order_sig!, delegate: KEEPER_PUBKEY, remaining_raw: b.quantity_raw!, fills: [], last_decision: null, last_session: null, last_price_usd: null, pending_sig: null, pending_amount_raw: null, pending_since: null, revoke_sig: null };
    await updateOrders((s) => {
      if (s.orders.some((o) => o.id === order.id || o.order_sig === order.order_sig)) return "order already recorded";
      // One delegation, one order: a newer approve on the same token account supersedes any open order on it.
      for (const o of s.orders) if (["armed", "triggered", "failed"].includes(o.status) && o.token_account === order.token_account) { o.status = "revoked"; o.last_decision = "superseded: a newer approve replaced this delegation"; }
      s.orders.push(order);
      return `order ${order.ticker} floor ${order.floor_price_usd} ${now}`;
    });
    return NextResponse.json({ order });
  } catch (e) {
    return NextResponse.json({ error: (e as Error).message }, { status: 502 });
  }
}
