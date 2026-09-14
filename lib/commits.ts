// Find the commit that wrote a given reading, so a judge can check the timestamp is GitHub's.
// A reading's commit is immutable once it exists, so the lookup result is cached for the process.
const REPO = process.env.GITHUB_REPO ?? "Lideeyah/gapless";
export const HISTORY_URL = `https://github.com/${REPO}/commits/main/data/prices.csv`;
const cache = new Map<string, string | null>();

export async function commitForReading(isoTs: string): Promise<string | null> {
  if (cache.has(isoTs)) return cache.get(isoTs)!;
  const t = Date.parse(isoTs);
  const since = new Date(t - 60_000).toISOString(), until = new Date(t + 4 * 60_000).toISOString();
  const token = process.env.GITHUB_TOKEN;
  try {
    const res = await fetch(`https://api.github.com/repos/${REPO}/commits?path=data/prices.csv&since=${since}&until=${until}&per_page=10`,
      { headers: { Accept: "application/vnd.github+json", "User-Agent": "gapless-web", ...(token ? { Authorization: `Bearer ${token}` } : {}) }, cache: "no-store" });
    if (!res.ok) return null;
    const commits = (await res.json()) as { sha: string; commit: { message: string } }[];
    const hit = commits.find((c) => c.commit.message.startsWith("record ")) ?? commits[0];
    const url = hit ? `https://github.com/${REPO}/commit/${hit.sha}` : null;
    if (url) cache.set(isoTs, url);
    return url;
  } catch { return null; }
}
