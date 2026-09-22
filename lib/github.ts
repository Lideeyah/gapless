// Order store: data/orders.json in this repository, read and written through the GitHub Contents
// API with optimistic concurrency (sha). The keeper writes the same file the same way.
// Field names follow the system architecture (§5). All token amounts are raw integer strings.
export type Fill = { at: string; signature: string; in_raw: string; out_usdc_raw: string; fill_price_usd: string; session: string; side?: "floor" | "ceiling"; kind?: "stop" | "take profit" };
export type Status = "armed" | "triggered" | "executing" | "filled" | "failed" | "revoked";
export type Order = {
  id: string; owner_pubkey: string; mint: string; ticker: string; token_account: string; quantity_raw: string; decimals: number;
  floor_price_usd: string | null; ceiling_price_usd?: string | null; status: Status; breach_count: number; last_checked_at: string | null; delegation_sig: string;
  fill_sig: string | null; fill_price_usd: string | null; filled_at: string | null; failure_reason: string | null; created_at: string;
  // additional, needed by this implementation
  order_sig: string; delegate: string; remaining_raw: string; fills: Fill[]; last_decision: string | null; last_session: string | null;
  last_price_usd: string | null; pending_sig: string | null; pending_amount_raw: string | null; pending_since: string | null; revoke_sig: string | null;
  // mint state guard
  multiplier: string | null; rebases: Rebase[]; blocked: "paused" | "transfer_hook" | "mint_unreadable" | "no_balance" | "pyth_unavailable" | "pyth_divergence" | null;
  multiplier_event: { detected_at: string; old_multiplier: string; new_multiplier: string; pre_price_usd: string | null } | null;
  // pyth (branch pyth): where the execution session came from, and what the second witness saw before the last execution attempt
  last_session_source?: "pyth" | "calendar" | "forced" | null;
  // the band: which side a breach sequence is counting on, and which side triggered the pending execution
  breach_side?: "floor" | "ceiling" | null; triggered_side?: "floor" | "ceiling" | null;
  pyth_check?: PythCheck | null;
};
/** Refusal-only witness record. `agree`/`market_closed`/`not_entitled`/`no_feed` let execution proceed; the rest stopped it. */
export type PythCheck = { checked_at: string; exec_price_usd: string; session: string; status: "agree" | "diverged" | "stale" | "unreadable" | "market_closed" | "not_entitled" | "no_feed";
  reference?: string; pyth_price_usd?: string; pyth_conf_usd?: string; pyth_publish_utc?: string; age_s?: number; divergence_bps?: number; feeds?: Record<string, string> };
/** A multiplier change, classified on the reading after it: a split moves the floor by the ratio, an accrual leaves it untouched. */
export type Rebase = { at: string; kind: "split" | "accrual"; old_floor: string | null; new_floor: string | null; old_ceiling?: string | null; new_ceiling?: string | null; ceiling_note?: string; old_multiplier: string; new_multiplier: string; pre_price_usd: string | null; post_price_usd: string };
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
  if (process.env.NODE_ENV !== "production" && process.env.ORDERS_LOCAL_FILE) { // local inspection only
    const { readFileSync } = await import("fs");
    return { store: JSON.parse(readFileSync(process.env.ORDERS_LOCAL_FILE, "utf8")) as Store, sha: null };
  }
  const res = await fetch(`${API}?ref=main`, { headers: headers(), cache: "no-store" });
  if (!res.ok) throw new Error(`orders.json read failed: HTTP ${res.status}`);
  const meta = await res.json();
  return { store: JSON.parse(Buffer.from(meta.content, "base64").toString("utf8")) as Store, sha: meta.sha };
}

/** Read-modify-write with one retry if the file moved under us. `mutate` returns a commit message, or null to write nothing. */
export async function updateOrders(mutate: (s: Store) => string | null): Promise<boolean> {
  if (!storeConfigured() && !(process.env.NODE_ENV !== "production" && process.env.ORDERS_LOCAL_FILE)) throw new Error("order store is not configured (GITHUB_TOKEN missing on the server)");
  for (let attempt = 0; attempt < 2; attempt++) {
    const { store, sha } = await readOrders();
    const message = mutate(store);
    if (message === null) return false;
    if (process.env.NODE_ENV !== "production" && process.env.ORDERS_LOCAL_FILE) { // local inspection only
      const { writeFileSync } = await import("fs"); writeFileSync(process.env.ORDERS_LOCAL_FILE, JSON.stringify(store, null, 2) + "\n"); return true;
    }
    const body = JSON.stringify({ message, sha, committer: AUTHOR, author: AUTHOR, content: Buffer.from(JSON.stringify(store, null, 2) + "\n").toString("base64") });
    const res = await fetch(API, { method: "PUT", headers: { ...headers(), "Content-Type": "application/json" }, body });
    if (res.ok) return true;
    if (res.status !== 409 || attempt === 1) throw new Error(`orders.json write failed: HTTP ${res.status} ${(await res.text()).slice(0, 200)}`);
  }
  return false;
}
