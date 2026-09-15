// Server-side verification against the chain. The app never trusts the browser's claim that a
// transaction happened; it reads the transaction back from an RPC and checks its contents.
export const RPC_URL = process.env.RPC_URL ?? process.env.NEXT_PUBLIC_RPC_URL ?? "https://api.mainnet-beta.solana.com";
export const MEMO_PROGRAM = "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr";
export const KEEPER_PUBKEY = process.env.NEXT_PUBLIC_KEEPER_PUBKEY ?? "";

export async function rpc<T = unknown>(method: string, params: unknown[]): Promise<T> {
  const res = await fetch(RPC_URL, { method: "POST", headers: { "Content-Type": "application/json" }, cache: "no-store",
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method, params }) });
  const json = await res.json();
  if (json.error) throw new Error(`rpc ${method}: ${JSON.stringify(json.error)}`);
  return json.result as T;
}

type ParsedIx = { program?: string; programId: string; parsed?: unknown };
type ParsedTx = { transaction: { message: { accountKeys: { pubkey: string; signer: boolean }[]; instructions: ParsedIx[] } }; meta: { err: unknown } } | null;

async function getTx(sig: string): Promise<ParsedTx> {
  for (let i = 0; i < 6; i++) {  // a just-confirmed transaction can take a moment to be servable
    const tx = await rpc<ParsedTx>("getTransaction", [sig, { encoding: "jsonParsed", maxSupportedTransactionVersion: 0, commitment: "confirmed" }]);
    if (tx) return tx;
    await new Promise((r) => setTimeout(r, 1500));
  }
  return null;
}

function signers(tx: NonNullable<ParsedTx>) { return tx.transaction.message.accountKeys.filter((k) => k.signer).map((k) => k.pubkey); }

/** The approve transaction must be signed by the owner and delegate `amount` of `tokenAccount` to the keeper. */
export async function verifyApprove(sig: string, owner: string, tokenAccount: string, amountRaw: string): Promise<string | null> {
  const tx = await getTx(sig);
  if (!tx) return "approve transaction not found on chain";
  if (tx.meta.err) return "approve transaction failed on chain";
  if (!signers(tx).includes(owner)) return "approve transaction was not signed by the connected wallet";
  const ok = tx.transaction.message.instructions.some((ix) => {
    const p = ix.parsed as { type?: string; info?: Record<string, unknown> } | undefined;
    if (!p || !["approve", "approveChecked"].includes(p.type ?? "")) return false;
    const info = p.info ?? {};
    const amt = (info.tokenAmount as { amount?: string } | undefined)?.amount ?? (info.amount as string | undefined);
    // The token account's owner must be the wallet that claims the order: proceeds are paid to owner_pubkey.
    return info.source === tokenAccount && info.delegate === KEEPER_PUBKEY && amt === amountRaw && info.owner === owner;
  });
  return ok ? null : "approve transaction does not delegate this amount of this account, owned by this wallet, to the keeper";
}

/** The order transaction must be signed by the owner and carry a memo whose JSON matches the order. */
export async function verifyOrderMemo(sig: string, owner: string, expected: Record<string, unknown>): Promise<string | null> {
  const tx = await getTx(sig);
  if (!tx) return "order transaction not found on chain";
  if (tx.meta.err) return "order transaction failed on chain";
  if (!signers(tx).includes(owner)) return "order transaction was not signed by the connected wallet";
  const memo = tx.transaction.message.instructions.find((ix) => ix.programId === MEMO_PROGRAM);
  if (!memo || typeof memo.parsed !== "string") return "order transaction carries no memo";
  let body: Record<string, unknown>;
  try { body = JSON.parse(memo.parsed); } catch { return "order memo is not JSON"; }
  for (const [k, v] of Object.entries(expected)) if (String(body[k]) !== String(v)) return `order memo field ${k} does not match`;
  return null;
}

/** The revoke transaction must be signed by the owner and revoke `tokenAccount`. */
export async function verifyRevoke(sig: string, owner: string, tokenAccount: string): Promise<string | null> {
  const tx = await getTx(sig);
  if (!tx) return "revoke transaction not found on chain";
  if (tx.meta.err) return "revoke transaction failed on chain";
  if (!signers(tx).includes(owner)) return "revoke transaction was not signed by the connected wallet";
  const ok = tx.transaction.message.instructions.some((ix) => {
    const p = ix.parsed as { type?: string; info?: Record<string, unknown> } | undefined;
    return p?.type === "revoke" && p.info?.source === tokenAccount;
  });
  return ok ? null : "revoke transaction does not revoke this token account";
}
