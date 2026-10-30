// v15-K: per-fleet / per-org timeline shard endpoint.
//
// Loading the full timeline.json (~55MB) via the browser's main thread was
// freezing the tab for 5-10 seconds on first paint. Most views only need the
// agents currently in scope. This endpoint takes a fleet / org / range filter
// and returns a shrunk payload with just those agents.
//
// Query params (all optional):
//   fleet=<title>       -> only agents whose .fleet === title
//   org=<org-id>        -> only agents mapped to that org (via timeline_hierarchy)
//   range=<7d|24h|1h>   -> keep only segments inside the window (shrinks per-agent payload)
//                          "all" or missing = full history
//
// Falls back to the full file when no filter.

import type { APIRoute } from "astro";
import fs from "node:fs";
import path from "node:path";

// Query parameters require an on-demand (non-prerendered) route.
export const prerender = false;

const DATA_PATH      = path.resolve("../data/timeline.json");
const HIERARCHY_PATH = path.resolve("../data/timeline_hierarchy.json");

let _cache: { mtime: number; data: any; hierarchy: any } | null = null;

function loadFresh() {
  const dstat = fs.statSync(DATA_PATH);
  const hstat = fs.existsSync(HIERARCHY_PATH) ? fs.statSync(HIERARCHY_PATH) : null;
  const mtime = Math.max(dstat.mtimeMs, hstat ? hstat.mtimeMs : 0);
  if (_cache && _cache.mtime === mtime) return _cache;
  const data = JSON.parse(fs.readFileSync(DATA_PATH, "utf-8"));
  const hierarchy = hstat ? JSON.parse(fs.readFileSync(HIERARCHY_PATH, "utf-8")) : null;
  _cache = { mtime, data, hierarchy };
  return _cache;
}

function rangeCutoff(range: string | null, maxTs: number): number {
  if (!range || range === "all") return 0;
  if (range === "7d")  return maxTs - 7 * 86400;
  if (range === "24h") return maxTs - 86400;
  if (range === "1h")  return maxTs - 3600;
  return 0;
}

export const GET: APIRoute = ({ url, request }) => {
  const { data, hierarchy } = loadFresh();
  const u = new URL(request.url);
  const q = u.searchParams;
  const fleet = (q.get("fleet") || "").trim();
  const org   = (q.get("org") || "").trim();
  const range = (q.get("range") || "").trim();

  let agents: any[] = data.agents || [];

  // Accept BOTH fleet+org (intersection). Users land here when the URL carries
  // both selectors — treat them as AND, not either/or.
  if (fleet) {
    agents = agents.filter((a: any) => (a.fleet || "") === fleet || (a.fleet_raw || "") === fleet);
  }
  if (org && hierarchy) {
    const amap = hierarchy.agentToOrg || {};
    agents = agents.filter((a: any) => amap[a.id] === org);
  }

  const maxTs = data.time_range?.max_ts || 0;
  const cutoff = rangeCutoff(range, maxTs);
  if (cutoff > 0) {
    agents = agents.map((a: any) => {
      const segs = (a.segments || []).filter((s: any) => (s.end_ts || s.start_ts || 0) >= cutoff);
      if (segs.length === a.segments?.length) return a;
      return { ...a, segments: segs };
    });
  }

  const minTs = agents.reduce((m, a) => {
    for (const s of (a.segments || [])) if ((s.start_ts || 0) > 0 && (!m || s.start_ts < m)) m = s.start_ts;
    return m;
  }, 0);
  const maxTsOut = agents.reduce((m, a) => {
    for (const s of (a.segments || [])) if ((s.end_ts || 0) > m) m = s.end_ts;
    return m;
  }, 0);

  const slim = {
    schema_v: data.schema_v,
    generated_at: data.generated_at,
    time_range: { min_ts: minTs || data.time_range?.min_ts, max_ts: maxTsOut || data.time_range?.max_ts, span_sec: (maxTsOut || 0) - (minTs || 0) },
    agents,
    aggregate: data.aggregate,  // keep aggregate as-is; small
  };

  return new Response(JSON.stringify(slim), {
    status: 200,
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": "no-store",
    },
  });
};
