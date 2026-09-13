// Order store: data/orders.json in this repository, read and written through the GitHub Contents
// API with optimistic concurrency (sha). The keeper writes the same file the same way.
// Field names follow the system architecture (§5). All token amounts are raw integer strings.
export type Fill = { at: string; signature: string; in_raw: string; out_usdc_raw: string; fill_price_usd: string; session: string };
export type Status = "armed" | "triggered" | "executing" | "filled" | "failed" | "revoked";
export type Order = {
  id: string; owner_pubkey: string; mint: string; ticker: string; token_account: string; quantity_raw: string; decimals: number;
  floor_price_usd: string; status: Status; breach_count: number; last_checked_at: string | null; delegation_sig: string;
  fill_sig: string | null; fill_price_usd: string | null; filled_at: string | null; failure_reason: string | null; created_at: string;
  // additional, needed by this implementation
  order_sig: string; delegate: string; remaining_raw: string; fills: Fill[]; last_decision: string | null; last_session: string | null;
  last_price_usd: string | null; pending_sig: string | null; pending_amount_raw: string | null; pending_since: string | null; revoke_sig: string | null;
};
type Store = { orders: Order[] };

const REPO = process.env.GITHUB_REPO ?? "Lideeyah/gapless";
const API = `https://api.github.com/repos/${REPO}/contents/data/orders.json`;
const AUTHOR = { name: "Lydia Solomon", email: "lydiasolomon137@gmail.com" };

function headers() {
  const token = process.env.GITHUB_TOKEN;
  return { Accept: "application/vnd.github+json", "User-Agent": "gapless-web", ...(token ? { Authorization: `Bearer ${token}` } : {}) };
}

export function storeConfigured() { return Boolean(process.env.GITHUB_TOKEN); }

export async function readOrders(): Promise<{ store: Store; sha: string | null }> {
  const res = await fetch(`${API}?ref=main`, { headers: headers(), cache: "no-store" });
  if (!res.ok) throw new Error(`orders.json read failed: HTTP ${res.status}`);
  const meta = await res.json();
  return { store: JSON.parse(Buffer.from(meta.content, "base64").toString("utf8")) as Store, sha: meta.sha };
}

/** Read-modify-write with one retry if the file moved under us. */
export async function updateOrders(mutate: (s: Store) => string): Promise<void> {
  if (!storeConfigured()) throw new Error("order store is not configured (GITHUB_TOKEN missing on the server)");
  for (let attempt = 0; attempt < 2; attempt++) {
    const { store, sha } = await readOrders();
    const message = mutate(store);
    const body = JSON.stringify({ message, sha, committer: AUTHOR, author: AUTHOR, content: Buffer.from(JSON.stringify(store, null, 2) + "\n").toString("base64") });
    const res = await fetch(API, { method: "PUT", headers: { ...headers(), "Content-Type": "application/json" }, body });
    if (res.ok) return;
    if (res.status !== 409 || attempt === 1) throw new Error(`orders.json write failed: HTTP ${res.status} ${(await res.text()).slice(0, 200)}`);
  }
}
