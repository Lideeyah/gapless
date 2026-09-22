import { NextResponse } from "next/server";
import { readOrders, updateOrders, storeConfigured, type Order } from "@/lib/github";
import { verifyApprove, verifyOrderMemo, KEEPER_PUBKEY } from "@/lib/chain";
import { readMintState } from "@/lib/mint";

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

type Body = { ceiling_price_usd?: string | null; id: string; owner_pubkey: string; ticker: string; mint: string; token_account: string; decimals: number; quantity_raw: string; floor_price_usd: string; delegation_sig: string; order_sig: string };

const MAX_BODY = 4096;          // an order is a few hundred bytes; anything bigger is not an order
const MAX_ORDERS_PER_OWNER = 50; // bounds the store's growth per wallet (the file lives in a public repo)
const TICKER = /^[A-Za-z0-9]{1,16}$/;

export async function POST(req: Request) {
  const text = await req.text();
  if (text.length > MAX_BODY) return NextResponse.json({ error: "body too large" }, { status: 413 });
  let b: Partial<Body>;
  try { b = JSON.parse(text) as Partial<Body>; } catch { return NextResponse.json({ error: "invalid JSON" }, { status: 400 }); }
  for (const k of ["id", "owner_pubkey", "ticker", "mint", "token_account", "decimals", "quantity_raw", "delegation_sig", "order_sig"] as const)
    if (b[k] === undefined || b[k] === null || b[k] === "") return NextResponse.json({ error: `missing ${k}` }, { status: 400 });
  // a floor, a ceiling, or both; never neither
  const floorSet = b.floor_price_usd !== undefined && b.floor_price_usd !== null && b.floor_price_usd !== "";
  const ceilingSet = b.ceiling_price_usd !== undefined && b.ceiling_price_usd !== null && b.ceiling_price_usd !== "";
  if (!floorSet && !ceilingSet) return NextResponse.json({ error: "set a floor, a ceiling, or both" }, { status: 400 });
  if (!UUID.test(b.id!)) return NextResponse.json({ error: "id must be a v4 uuid" }, { status: 400 });
  if (!/^\d+$/.test(b.quantity_raw!) || BigInt(b.quantity_raw!) <= 0n) return NextResponse.json({ error: "quantity_raw must be a positive integer string" }, { status: 400 });
  if (floorSet && (!DECIMAL.test(b.floor_price_usd!) || Number(b.floor_price_usd) <= 0 || b.floor_price_usd!.length > 24)) return NextResponse.json({ error: "floor_price_usd must be a positive decimal string" }, { status: 400 });
  if (ceilingSet && (!DECIMAL.test(b.ceiling_price_usd!) || Number(b.ceiling_price_usd) <= 0 || b.ceiling_price_usd!.length > 24)) return NextResponse.json({ error: "ceiling_price_usd must be a positive decimal string" }, { status: 400 });
  if (floorSet && ceilingSet && Number(b.ceiling_price_usd) <= Number(b.floor_price_usd)) return NextResponse.json({ error: "the ceiling must be above the floor" }, { status: 400 });
  if (!TICKER.test(String(b.ticker))) return NextResponse.json({ error: "ticker must be 1-16 alphanumeric characters" }, { status: 400 });
  if (!Number.isInteger(Number(b.decimals)) || Number(b.decimals) < 0 || Number(b.decimals) > 18) return NextResponse.json({ error: "decimals out of range" }, { status: 400 });
  if (!KEEPER_PUBKEY) return NextResponse.json({ error: "keeper delegate is not configured on the server" }, { status: 503 });
  try {
    const bad = (await verifyApprove(b.delegation_sig!, b.owner_pubkey!, b.token_account!, b.quantity_raw!))
      ?? (await verifyOrderMemo(b.order_sig!, b.owner_pubkey!, { gapless: 1, id: b.id, mint: b.mint, token_account: b.token_account, quantity_raw: b.quantity_raw, ...(floorSet ? { floor_price_usd: b.floor_price_usd } : {}), ...(ceilingSet ? { ceiling_price_usd: b.ceiling_price_usd } : {}), delegate: KEEPER_PUBKEY, delegation_sig: b.delegation_sig }));
    if (bad) return NextResponse.json({ error: bad }, { status: 422 });
    // The floor is meaningful only against the multiplier in force when it was chosen: read it from the mint now.
    const mintNow = await readMintState(b.mint!);
    if (mintNow.decimals !== Number(b.decimals)) return NextResponse.json({ error: `decimals ${b.decimals} do not match the mint (${mintNow.decimals})` }, { status: 422 });
    const now = new Date().toISOString().replace(/\.\d+Z$/, "Z");
    const order: Order = { id: b.id!, owner_pubkey: b.owner_pubkey!, mint: b.mint!, ticker: b.ticker!, token_account: b.token_account!, quantity_raw: b.quantity_raw!, decimals: Number(b.decimals),
      floor_price_usd: floorSet ? b.floor_price_usd! : null, ceiling_price_usd: ceilingSet ? b.ceiling_price_usd! : null, status: "armed", breach_count: 0, last_checked_at: null, delegation_sig: b.delegation_sig!, fill_sig: null, fill_price_usd: null, filled_at: null, failure_reason: null, created_at: now,
      order_sig: b.order_sig!, delegate: KEEPER_PUBKEY, remaining_raw: b.quantity_raw!, fills: [], last_decision: null, last_session: null, last_price_usd: null, pending_sig: null, pending_amount_raw: null, pending_since: null, revoke_sig: null,
      multiplier: mintNow.multiplier, rebases: [], blocked: null, multiplier_event: null };
    let reason: string | null = null;
    const written = await updateOrders((s) => {
      if (s.orders.some((o) => o.id === order.id || o.order_sig === order.order_sig || o.delegation_sig === order.delegation_sig)) { reason = "order already recorded"; return null; }
      if (s.orders.filter((o) => o.owner_pubkey === order.owner_pubkey).length >= MAX_ORDERS_PER_OWNER) { reason = "too many orders for this wallet"; return null; }
      // One delegation, one order: a newer approve on the same token account supersedes any open order on it.
      for (const o of s.orders) if (["armed", "triggered", "failed"].includes(o.status) && o.token_account === order.token_account) { o.status = "revoked"; o.last_decision = "superseded: a newer approve replaced this delegation"; }
      s.orders.push(order);
      return `order ${order.ticker}${order.floor_price_usd ? ` floor ${order.floor_price_usd}` : ""}${order.ceiling_price_usd ? ` ceiling ${order.ceiling_price_usd}` : ""} ${now}`;
    });
    if (!written) return NextResponse.json({ error: reason ?? "not written" }, { status: 409 });
    return NextResponse.json({ order });
  } catch (e) {
    return NextResponse.json({ error: (e as Error).message }, { status: 502 });
  }
}
