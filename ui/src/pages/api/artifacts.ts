// Per-scope artifacts shard. Shrinks the 11MB artifacts.json to just the
// agents in the requested fleet/org. Combined with /api/timeline + /api/transcript
// shards this drops gamejam from ~108MB initial payload to ~15MB.

import type { APIRoute } from "astro";
import fs from "node:fs";
import path from "node:path";

export const prerender = false;

const ARTIFACTS_PATH = (() => {
  const p1 = path.resolve("../data/artifacts.json");
  const p2 = path.resolve("public/data/artifacts.json");
  return fs.existsSync(p1) ? p1 : p2;
})();
const TIMELINE_PATH  = path.resolve("../data/timeline.json");
const HIERARCHY_PATH = path.resolve("../data/timeline_hierarchy.json");

let _cache: { mtime: number; artifacts: any; timeline: any; hierarchy: any } | null = null;

function loadFresh() {
  const a = fs.statSync(ARTIFACTS_PATH).mtimeMs;
  const t = fs.statSync(TIMELINE_PATH).mtimeMs;
  const h = fs.existsSync(HIERARCHY_PATH) ? fs.statSync(HIERARCHY_PATH).mtimeMs : 0;
  const mtime = Math.max(a, t, h);
  if (_cache && _cache.mtime === mtime) return _cache;
  const artifacts = JSON.parse(fs.readFileSync(ARTIFACTS_PATH, "utf-8"));
  const timeline  = JSON.parse(fs.readFileSync(TIMELINE_PATH, "utf-8"));
  const hierarchy = h ? JSON.parse(fs.readFileSync(HIERARCHY_PATH, "utf-8")) : null;
  _cache = { mtime, artifacts, timeline, hierarchy };
  return _cache;
}

export const GET: APIRoute = ({ request }) => {
  const { artifacts, timeline, hierarchy } = loadFresh();
  const q = new URL(request.url).searchParams;
  const fleet = (q.get("fleet") || "").trim();
  const org   = (q.get("org") || "").trim();

  if (!fleet && !org) {
    return new Response(JSON.stringify(artifacts), {
      headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
    });
  }

  // Accept BOTH fleet+org as AND (intersection).
  let scopedIds: Set<string>;
  if (fleet && org && hierarchy) {
    const amap = hierarchy.agentToOrg || {};
    scopedIds = new Set(
      (timeline.agents || [])
        .filter((a: any) => (a.fleet || a.fleet_raw || "") === fleet && amap[a.id] === org)
        .map((a: any) => a.id)
    );
  } else if (fleet) {
    scopedIds = new Set(
      (timeline.agents || [])
        .filter((a: any) => (a.fleet || a.fleet_raw || "") === fleet)
        .map((a: any) => a.id)
    );
  } else {
    const amap = hierarchy?.agentToOrg || {};
    scopedIds = new Set(Object.keys(amap).filter(id => amap[id] === org));
  }

  const slim: Record<string, any> = {};
  for (const id of scopedIds) {
    if (artifacts[id]) slim[id] = artifacts[id];
  }
  return new Response(JSON.stringify(slim), {
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  });
};
