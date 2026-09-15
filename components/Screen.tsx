"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useConnection, useWallet } from "@solana/wallet-adapter-react";
import { PublicKey, Transaction, TransactionInstruction } from "@solana/web3.js";
import { TOKEN_2022_PROGRAM_ID, createApproveCheckedInstruction, createRevokeInstruction } from "@solana/spl-token";
import { Buffer } from "buffer";
import Timeline, { type Row } from "@/components/Timeline";
import Counterfactual from "@/components/Counterfactual";
import type { Gap, Stats } from "@/lib/csv";
import type { Order } from "@/lib/github";
import { fmtPct, fmtQty, fmtTs, fmtUsd, short, solscanTx } from "@/lib/format";

const KEEPER = process.env.NEXT_PUBLIC_KEEPER_PUBKEY ?? "";
const LIVE = ["armed", "triggered", "executing", "failed"];
/** Decimal string -> (integer numerator, power-of-ten scale). No floats. */
function scaled(str: string): [bigint, bigint] {
  const [i, f = ""] = str.split(".");
  return [BigInt((i || "0") + f), 10n ** BigInt(f.length)];
}
/** Raw units for a typed UI quantity on a scaled-UI mint: ui * 10^dec / multiplier, all in integers. */
function rawFromUi(ui: string, decimals: number, multiplier: string): bigint {
  const [q, qs] = scaled(ui); const [m, ms] = scaled(multiplier);
  return (q * 10n ** BigInt(decimals) * ms) / (qs * m);
}
const MEMO_PROGRAM = new PublicKey("MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr");
const RECORDED: Record<string, string> = { NVDAx: "Xsc9qvGR1efVDFGLrVsmkzv3qi45LTBjeUKSPmx9qEh", TSLAx: "XsDoVfqeBukxuZHWhdvWHBhgEHjGNst4MLodqsJHzoB", SPYx: "XsoCS1TfEyfFhfvj8EtZ528L3CaKBDBRqRapnBbDF2W" };

type History = { rows: Row[]; stats: Stats; gap: Gap | null; regimes: Record<string, { hours: string; confirmations: number; slippageBps: number; splitOnImpact: boolean }> };
type Holding = { mint: string; ticker: string; name: string; tokenAccount: string; decimals: number; raw: string; ui: number; uiString: string; delegate: string | null; delegatedRaw: string; price: number | null; multiplier: string };
type Phase = { kind: "set" | "revoke"; state: "confirm" | "inflight" | "done" | "error"; step?: string; detail?: string; sigs?: string[] };

export default function Screen() {
  const { connection } = useConnection();
  const { publicKey, connected, connecting, wallets, wallet, select, connect, disconnect, sendTransaction } = useWallet();
  // Local inspection only: ?as=<pubkey> shows that owner's rows read-only, without a wallet. Never active in production builds.
  const [devAs, setDevAs] = useState<string | null>(null);
  useEffect(() => { if (process.env.NODE_ENV !== "production") setDevAs(new URLSearchParams(window.location.search).get("as")); }, []);
  const viewKey = useMemo(() => { try { return devAs ? new PublicKey(devAs) : publicKey; } catch { return publicKey; } }, [devAs, publicKey]);
  const owner = viewKey?.toBase58() ?? null;
  const isConnected = connected || Boolean(devAs);

  const [history, setHistory] = useState<History | null>(null);
  const [histErr, setHistErr] = useState<string | null>(null);
  const [orders, setOrders] = useState<Order[]>([]);
  const [storeOk, setStoreOk] = useState<boolean | null>(null);
  const [holdings, setHoldings] = useState<Holding[] | null>(null);
  const [holdErr, setHoldErr] = useState<string | null>(null);
  const [sel, setSel] = useState<string>("NVDAx");
  const [floorText, setFloorText] = useState("");
  const [qtyText, setQtyText] = useState("");
  const [phase, setPhase] = useState<Phase | null>(null);
  const [wantConnect, setWantConnect] = useState(false);
  const [mounted, setMounted] = useState(false); // wallet detection only exists in the browser; keep SSR and first paint identical
  useEffect(() => { setMounted(true); }, []);

  const loadHistory = useCallback(async () => {
    try { const r = await fetch("/api/history", { cache: "no-store" }); const j = await r.json(); if (j.error) throw new Error(j.error); setHistory(j); setHistErr(null); }
    catch (e) { setHistErr((e as Error).message); }
  }, []);
  const loadOrders = useCallback(async () => {
    if (!owner) { setOrders([]); return; }
    try { const r = await fetch(`/api/orders?owner=${owner}`, { cache: "no-store" }); const j = await r.json(); if (j.error) throw new Error(j.error); setOrders(j.orders ?? []); setStoreOk(Boolean(j.storeConfigured)); }
    catch { /* keep last known */ }
  }, [owner]);
  const loadHoldings = useCallback(async () => {
    if (!viewKey) { setHoldings(null); return; }
    try {
      const accts = await connection.getParsedTokenAccountsByOwner(viewKey, { programId: TOKEN_2022_PROGRAM_ID });
      const owned = accts.value.map((a) => ({ acct: a.pubkey.toBase58(), info: a.account.data.parsed.info })).filter((a) => BigInt(a.info.tokenAmount.amount) > 0n);
      const mints = owned.map((a) => a.info.mint as string);
      if (mints.length === 0) { setHoldings([]); setHoldErr(null); return; }
      const [tokens, prices] = await Promise.all([
        fetch(`/api/tokens?mints=${mints.join(",")}`, { cache: "no-store" }).then((r) => r.json()),
        fetch(`/api/prices?mints=${mints.join(",")}`, { cache: "no-store" }).then((r) => r.json()),
      ]);
      const mintStates: Record<string, { multiplier: string }> = {};
      await Promise.all((tokens as { mint: string }[]).map?.((t) => fetch(`/api/mint?mint=${t.mint}`, { cache: "no-store" }).then((r) => r.json()).then((m) => { if (m.multiplier) mintStates[t.mint] = m; })) ?? []);
      if (tokens.error) throw new Error(tokens.error);
      const list: Holding[] = [];
      for (const t of tokens as { mint: string; symbol: string; name: string; decimals: number }[]) {
        const a = owned.find((o) => o.info.mint === t.mint)!;
        list.push({ mint: t.mint, ticker: t.symbol, name: t.name, tokenAccount: a.acct, decimals: t.decimals, raw: a.info.tokenAmount.amount, ui: Number(a.info.tokenAmount.uiAmountString ?? 0),
          uiString: String(a.info.tokenAmount.uiAmountString ?? "0"), delegate: a.info.delegate ?? null, delegatedRaw: a.info.delegatedAmount?.amount ?? "0", price: prices?.[t.mint]?.price ?? null, multiplier: mintStates[t.mint]?.multiplier ?? String(prices?.[t.mint]?.multiplier ?? 1) });
      }
      setHoldings(list); setHoldErr(null);
      if (list.length && !list.some((h) => h.ticker === sel)) setSel(list[0].ticker);
    } catch (e) { setHoldErr((e as Error).message); }
  }, [connection, viewKey, sel]);

  useEffect(() => { loadHistory(); const id = setInterval(loadHistory, 120_000); return () => clearInterval(id); }, [loadHistory]);
  useEffect(() => { loadOrders(); loadHoldings(); const id = setInterval(() => { loadOrders(); loadHoldings(); }, 60_000); return () => clearInterval(id); }, [loadOrders, loadHoldings]);
  useEffect(() => { if (wantConnect && wallet && !connected && !connecting) { connect().catch(() => {}).finally(() => setWantConnect(false)); } }, [wantConnect, wallet, connected, connecting, connect]);

  const phantom = wallets.find((w) => w.adapter.name === "Phantom");
  const onConnect = () => {
    const found = phantom ?? wallets.find((w) => w.adapter.name === "Phantom");
    if (!found) { window.open("https://phantom.app", "_blank"); return; }
    select(found.adapter.name); setWantConnect(true);
  };
  const connectLabel = !mounted ? "connect wallet" : connecting ? "connecting" : phantom ? "connect Phantom" : "install Phantom";

  const holding = holdings?.find((h) => h.ticker === sel) ?? null;
  const live = orders.filter((o) => LIVE.includes(o.status));
  const fired = orders.filter((o) => o.status === "filled");
  const activeForSel = live.find((o) => o.ticker === sel) ?? null;
  const floor = Number(floorText);
  const floorForChart = activeForSel ? Number(activeForSel.floor_price_usd) : Number.isFinite(floor) && floor > 0 ? floor : null;
  const selMint = holding?.mint ?? RECORDED[sel] ?? null;
  const price = holding?.price ?? (selMint && history ? history.stats.perTicker[sel]?.last ?? null : null);
  const rows = history?.rows ?? [];
  const lastSession = history?.stats.currentSession ?? null;
  const regime = lastSession && history ? history.regimes[lastSession] : null;

  useEffect(() => { if (holding && !qtyText) setQtyText(holding.uiString); }, [holding, qtyText]);

  // ------------------------------------------------------------------ actions
  const setLabel = !connected ? "connect wallet to set a floor" : !storeOk ? "order store is not configured on the server" : !KEEPER ? "keeper delegate is not configured on the server"
    : holdings === null ? "reading holdings" : !holding ? "hold an xStock to set a floor" : activeForSel ? `revoke the ${sel} floor before setting a new one` : !(floor > 0) ? "enter a floor to arm" : price !== null && floor >= price ? "floor is at or above the current price; it would fire on the next run" : null;
  const canSet = setLabel === null || (setLabel?.startsWith("floor is at or above") ?? false);
  const floorStr = floorText.replace(/\.$/, "");

  async function confirmWith(tx: Transaction, step: string) {
    setPhase((p) => ({ ...(p as Phase), state: "inflight", step }));
    const { blockhash, lastValidBlockHeight } = await connection.getLatestBlockhash("confirmed");
    tx.recentBlockhash = blockhash; tx.feePayer = publicKey!;
    const sig = await sendTransaction(tx, connection);
    await connection.confirmTransaction({ signature: sig, blockhash, lastValidBlockHeight }, "confirmed");
    return sig;
  }

  async function doSet() {
    if (!holding || !publicKey) return;
    try {
      if (!/^\d+(\.\d+)?$/.test(qtyText) || !/^\d+(\.\d+)?$/.test(floorStr)) throw new Error("quantity and floor must be plain decimals");
      let raw = rawFromUi(qtyText, holding.decimals, holding.multiplier);
      if (qtyText === holding.uiString || raw > BigInt(holding.raw)) raw = BigInt(holding.raw); // full balance: use the exact on-chain amount
      if (raw <= 0n) throw new Error("quantity must be positive");
      const id = crypto.randomUUID();
      const delegate = new PublicKey(KEEPER);
      const approveSig = await confirmWith(new Transaction().add(createApproveCheckedInstruction(new PublicKey(holding.tokenAccount), new PublicKey(holding.mint), delegate, publicKey, raw, holding.decimals, [], TOKEN_2022_PROGRAM_ID)), "1 of 2 · approve delegation in Phantom");
      const memo = JSON.stringify({ gapless: 1, id, mint: holding.mint, token_account: holding.tokenAccount, quantity_raw: raw.toString(), floor_price_usd: floorStr, delegate: KEEPER, delegation_sig: approveSig });
      const orderSig = await confirmWith(new Transaction().add(new TransactionInstruction({ keys: [{ pubkey: publicKey, isSigner: true, isWritable: false }], programId: MEMO_PROGRAM, data: Buffer.from(memo, "utf8") })), "2 of 2 · sign the order record in Phantom");
      setPhase({ kind: "set", state: "inflight", step: "recording the order" });
      const r = await fetch("/api/orders", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id, owner_pubkey: owner, ticker: holding.ticker, mint: holding.mint, token_account: holding.tokenAccount, decimals: holding.decimals, quantity_raw: raw.toString(), floor_price_usd: floorStr, delegation_sig: approveSig, order_sig: orderSig }) });
      const j = await r.json();
      if (!r.ok) throw new Error(j.error ?? `HTTP ${r.status}`);
      setPhase({ kind: "set", state: "done", detail: `armed at ${fmtUsd(Number(floorStr))}`, sigs: [approveSig, orderSig] });
      setFloorText(""); await Promise.all([loadOrders(), loadHoldings()]);
    } catch (e) { setPhase({ kind: "set", state: "error", detail: (e as Error).message }); }
  }

  async function doRevoke(o: Order) {
    if (!publicKey) return;
    try {
      const sig = await confirmWith(new Transaction().add(createRevokeInstruction(new PublicKey(o.token_account), publicKey, [], TOKEN_2022_PROGRAM_ID)), "revoke delegation in Phantom");
      setPhase({ kind: "revoke", state: "inflight", step: "recording the cancellation" });
      const r = await fetch("/api/orders/cancel", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: o.id, owner, revokeSig: sig }) });
      const j = await r.json();
      if (!r.ok) throw new Error(j.error ?? `HTTP ${r.status}`);
      setPhase({ kind: "revoke", state: "done", detail: `${o.ticker} floor removed`, sigs: [sig] });
      await Promise.all([loadOrders(), loadHoldings()]);
    } catch (e) { setPhase({ kind: "revoke", state: "error", detail: (e as Error).message }); }
  }

  const confirmingRevoke = phase?.kind === "revoke" && phase.state === "confirm";
  const closedLow = history?.stats.perTicker[sel]?.minClosed ?? null;

  return (
    <main className="page">
      <header style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", gap: 24, paddingBottom: 48 }}>
        <div>
          <h1>Gapless</h1>
          <h2>A stop loss that works when the stock market is closed.</h2>
        </div>
        <div style={{ textAlign: "right" }}>
          {isConnected && owner ? (
            <div><span className="mono">{short(owner)}</span><span className="faint"> · </span><button className="btn btn-secondary" onClick={() => disconnect()}>disconnect</button></div>
          ) : (
            <button className="btn btn-primary" onClick={onConnect} disabled={!mounted || connecting}>{connectLabel}</button>
          )}
        </div>
      </header>

      <section>
        <div className="tickers mono" style={{ lineHeight: "24px", paddingBottom: 8 }}>
          {[...new Set([...Object.keys(RECORDED), ...(holdings ?? []).map((h) => h.ticker)])].map((tk) => (
            <button key={tk} className={tk === sel ? "on" : ""} onClick={() => { setSel(tk); setQtyText(""); }}>{tk}</button>
          ))}
        </div>
        {histErr ? <p className="secondary">Could not read data/prices.csv: {histErr}</p>
          : <Timeline rows={rows} ticker={sel} floor={floorForChart} firstAt={history?.stats.firstAt ?? null} lastAt={history?.stats.lastAt ?? null} />}
      </section>

      <div style={{ height: 48 }} />

      <section>
        <div className="row head mono secondary"><span>ticker</span><span>quantity</span><span>current price</span><span>floor</span><span>status</span></div>
        {!isConnected && <div className="row"><span className="secondary" style={{ gridColumn: "1 / -1" }}>Connect a wallet to see holdings. Balances are read from the chain; nothing is deposited.</span></div>}
        {isConnected && holdErr && <div className="row"><span className="secondary" style={{ gridColumn: "1 / -1" }}>Could not read token accounts from the RPC: {holdErr}</span></div>}
        {isConnected && holdings && holdings.length === 0 && live.length === 0 && fired.length === 0 && (
          <div className="row"><span className="secondary" style={{ gridColumn: "1 / -1" }}>This wallet holds no xStocks. Gapless works on Token-2022 xStock balances (NVDAx, TSLAx, SPYx and the rest of the xStocks list). Buy one on any Solana DEX and it will appear here.</span></div>
        )}
        {holdings?.map((h) => {
          const o = live.find((x) => x.token_account === h.tokenAccount) ?? null;
          const dist = o && h.price ? ((h.price - Number(o.floor_price_usd)) / h.price) * 100 : null;
          const onChainArmed = Boolean(h.delegate && KEEPER && h.delegate === KEEPER && BigInt(h.delegatedRaw) > 0n);
          return (
            <div className="row fade" key={h.tokenAccount} style={{ opacity: 1 }}>
              <span><span className="num" style={{ fontSize: 28 }}>{h.ticker}</span><br /><span className="mono secondary">{short(h.mint)}</span></span>
              <span className="num" style={{ fontSize: 28 }}>{fmtQty(h.ui)}</span>
              <span className="num" style={{ fontSize: 28 }}>{h.price === null ? "—" : fmtUsd(h.price)}</span>
              <span className="num" style={{ fontSize: 28, color: o ? "#1F4D3D" : undefined }}>{o ? fmtUsd(Number(o.floor_price_usd)) : "—"}</span>
              <span>
                {o ? (
                  <>
                    <span style={{ color: "rgba(31,77,61,.4)" }}>{o.status === "armed" && o.fills.length ? "armed · partially filled" : o.status}</span>{o.status === "failed" ? <span className="secondary"> · retrying next run</span> : null}
                    {o.blocked === "paused" && <><br /><span>mint paused by the issuer</span><span className="secondary"> · the position cannot be sold while the pause lasts; the keeper checks again every cycle</span></>}
                    {o.blocked === "transfer_hook" && <><br /><span>transfer hook enabled on the mint</span><span className="secondary"> · Gapless does not route swaps through hooks; the position will not be sold until this is reviewed</span></>}
                    {o.blocked === "mint_unreadable" && <><br /><span>mint state unreadable</span><span className="secondary"> · the keeper will not sell until it can confirm the mint is unpaused and unsplit</span></>}
                    {o.multiplier_event && <><br /><span className="mono secondary">issuer multiplier changed {o.multiplier_event.old_multiplier} → {o.multiplier_event.new_multiplier} at {fmtTs(o.multiplier_event.detected_at)} · deciding split or dividend on the next reading; nothing evaluated until then</span></>}
                    {(o.rebases ?? []).map((r) => <span key={r.at}><br /><span className="mono secondary">{r.kind === "split"
                      ? `split: floor rebased ${fmtUsd(Number(r.old_floor))} → ${fmtUsd(Number(r.new_floor))} on ${fmtTs(r.at)} · issuer multiplier ${r.old_multiplier} → ${r.new_multiplier} · price moved ${r.pre_price_usd ? fmtUsd(Number(r.pre_price_usd)) : "—"} → ${fmtUsd(Number(r.post_price_usd))}`
                      : `dividend accrual on ${fmtTs(r.at)}: issuer multiplier ${r.old_multiplier} → ${r.new_multiplier}, price ${r.pre_price_usd ? fmtUsd(Number(r.pre_price_usd)) : "—"} → ${fmtUsd(Number(r.post_price_usd))}, floor left at ${fmtUsd(Number(r.new_floor))}`}</span></span>)}
                    <span className="secondary">{dist === null ? "" : ` · ${fmtPct(dist)} above floor`}</span>
                    <br /><span className="mono secondary">{onChainArmed ? `delegated ${h.delegatedRaw} raw on chain · ${o.breach_count} consecutive breach${o.breach_count === 1 ? "" : "es"}` : "delegation not visible on chain yet"}</span>
                    {o.last_decision ? <><br /><span className="mono secondary">{fmtTs(o.last_checked_at)} · {o.last_decision}</span></> : <><br /><span className="mono secondary">not yet checked by the keeper</span></>}
                    {closedLow !== null && h.ticker === sel ? <><br /><span className="mono secondary">closed-hours low in window {fmtUsd(closedLow)}{closedLow <= Number(o.floor_price_usd) ? " · reached this floor" : " · stayed above this floor"}</span></> : null}
                  </>
                ) : <span className="secondary">no floor set</span>}
              </span>
            </div>
          );
        })}
        {fired.map((o) => {
          const f = o.fills?.at(-1);
          return (
            <div className="row" key={o.id}>
              <span><span className="num" style={{ fontSize: 28 }}>{o.ticker}</span><br /><span className="mono secondary">{short(o.mint)}</span></span>
              <span className="num" style={{ fontSize: 28 }}>{o.quantity_raw}<br /><span className="mono secondary">raw units</span></span>
              <span className="num" style={{ fontSize: 28 }} >{o.fill_price_usd ? fmtUsd(Number(o.fill_price_usd)) : "—"}<br /><span className="mono secondary">fill{o.fills.length > 1 ? `, ${o.fills.length} parts` : ""}</span></span>
              <span className="num" style={{ fontSize: 28 }}>{fmtUsd(Number(o.floor_price_usd))}</span>
              <span>
                <span className={confirmingRevoke ? "" : "ox"}>filled</span><span className="secondary"> · {f ? `${fmtTs(f.at)} · ${f.session}` : ""}</span>
                <br />{o.fill_sig ? <a className="mono" href={solscanTx(o.fill_sig)} target="_blank" rel="noreferrer">{short(o.fill_sig)}</a> : <span className="mono secondary">no fill recorded</span>}
                <span className="mono secondary"> · USDC sent to {short(o.owner_pubkey)}</span>
              </span>
            </div>
          );
        })}
        {isConnected && live.filter((o) => !holdings?.some((h) => h.tokenAccount === o.token_account)).map((o) => (
          <div className="row" key={o.id}><span className="secondary" style={{ gridColumn: "1 / -1" }}>{o.ticker} order {short(o.id)} is armed but its token account no longer shows a balance in this wallet.</span></div>
        ))}
        <div className="rule" />
      </section>

      <div style={{ height: 48 }} />

      <section>
        <h2>Actions</h2>
        <div style={{ height: 24 }} />
        {phase && phase.state !== "confirm" ? (
          <div className="fade">
            {phase.state === "inflight" && <p><span className="green">in flight</span><span className="secondary"> · {phase.step}</span></p>}
            {phase.state === "done" && <p><span className="green">done</span><span className="secondary"> · {phase.detail}</span>{phase.sigs?.map((s) => <span key={s}> <a className="mono" href={solscanTx(s)} target="_blank" rel="noreferrer">{short(s)}</a></span>)}</p>}
            {phase.state === "error" && <p><span className="ox">failed</span><span className="secondary"> · {phase.detail}</span></p>}
            {phase.state !== "inflight" && <p style={{ paddingTop: 24 }}><button className="btn btn-secondary" onClick={() => setPhase(null)}>continue</button></p>}
          </div>
        ) : confirmingRevoke && activeForSel ? (
          <div className="fade">
            <p>Remove the {sel} floor at {fmtUsd(Number(activeForSel.floor_price_usd))}? This signs an SPL revoke; the keeper can no longer move these tokens.</p>
            <p style={{ paddingTop: 24 }}><button className="btn btn-destructive" onClick={() => doRevoke(activeForSel)}>revoke delegation</button> <span className="faint"> · </span> <button className="btn btn-secondary" onClick={() => setPhase(null)}>keep it</button></p>
          </div>
        ) : phase?.kind === "set" && phase.state === "confirm" && holding ? (
          <div className="fade">
            <p>Arm a floor at <span className="num" style={{ fontSize: 22 }}>{fmtUsd(floor)}</span> on {qtyText} {sel}. Two signatures: an SPL approve delegating up to that quantity to the keeper, and a memo recording the order. Tokens stay in your wallet. Gapless holds a capped delegation you can revoke at any time. The issuer separately holds an uncapped permanent delegation over every account of this token, which Gapless neither controls nor can remove.</p>
            {regime && <p className="secondary" style={{ paddingTop: 24 }}>Right now the recorder’s last reading is in the {lastSession} regime: {regime.confirmations} consecutive reading{regime.confirmations === 1 ? "" : "s"} at or below the floor and {regime.slippageBps} bps slippage tolerance{regime.splitOnImpact ? ", split across runs if price impact exceeds it" : ""}.</p>}
            <p style={{ paddingTop: 24 }}><button className="btn btn-primary" onClick={doSet}>sign and arm</button> <span className="faint"> · </span> <button className="btn btn-secondary" onClick={() => setPhase(null)}>back</button></p>
          </div>
        ) : (
          <div className="fade" style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1.6fr", gap: 24, alignItems: "end" }}>
            <label><span className="mono secondary">floor, USD</span><input inputMode="decimal" placeholder={price !== null ? `below ${fmtUsd(price)}` : "0.00"} value={floorText} onChange={(e) => setFloorText(e.target.value.replace(/[^0-9.]/g, ""))} disabled={!holding} /></label>
            <label><span className="mono secondary">quantity, {sel}</span><input inputMode="decimal" placeholder={holding ? fmtQty(holding.ui) : "0"} value={qtyText} onChange={(e) => setQtyText(e.target.value.replace(/[^0-9.]/g, ""))} disabled={!holding} /></label>
            <div>
              {activeForSel
                ? <button className="btn btn-secondary" onClick={() => setPhase({ kind: "revoke", state: "confirm" })}>revoke the {sel} floor</button>
                : canSet ? <button className="btn btn-primary" onClick={() => setPhase({ kind: "set", state: "confirm" })}>set floor</button>
                : <button className="btn btn-disabled" disabled>{setLabel}</button>}
              {canSet && setLabel && <p className="mono secondary" style={{ paddingTop: 8 }}>{setLabel}</p>}
            </div>
          </div>
        )}
        <p className="mono faint" style={{ paddingTop: 24 }}>
          {regime && lastSession ? `regime now: ${lastSession} (${regime.hours}) as of the recorder’s reading at ${fmtTs(history?.stats.lastAt)} · ${regime.confirmations} confirmation${regime.confirmations === 1 ? "" : "s"} · ${regime.slippageBps} bps` : "regime: waiting for the recorder’s first reading"}
          {KEEPER ? ` · keeper delegate ${short(KEEPER)}` : " · keeper delegate not configured"}
        </p>
      </section>

      <div style={{ height: 72 }} />
      <div className="rule" />
      <div style={{ height: 48 }} />

      <section><Counterfactual stats={history?.stats ?? null} gap={history?.gap ?? null} /></section>
    </main>
  );
}
