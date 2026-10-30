// Per-scope transcript shard. Same shape as /data/transcript.json but filtered
// to sessions whose owning agent is in the requested fleet/org. Shrinks 42MB
// → 1-2MB for gamejam, drops main-thread JSON.parse from ~3s to ~50ms.

import type { APIRoute } from "astro";
import fs from "node:fs";
import path from "node:path";

export const prerender = false;

// transcript.json lives in ui/public/data (not top-level /data/) in this repo.
const TRANSCRIPT_PATH = (() => {
  const p1 = path.resolve("../data/transcript.json");
  const p2 = path.resolve("public/data/transcript.json");
  return fs.existsSync(p1) ? p1 : p2;
})();
const TIMELINE_PATH   = path.resolve("../data/timeline.json");
const HIERARCHY_PATH  = path.resolve("../data/timeline_hierarchy.json");

let _cache: { mtime: number; transcript: any; timeline: any; hierarchy: any } | null = null;

function loadFresh() {
  const tx = fs.statSync(TRANSCRIPT_PATH).mtimeMs;
  const tl = fs.statSync(TIMELINE_PATH).mtimeMs;
  const hr = fs.existsSync(HIERARCHY_PATH) ? fs.statSync(HIERARCHY_PATH).mtimeMs : 0;
  const mtime = Math.max(tx, tl, hr);
  if (_cache && _cache.mtime === mtime) return _cache;
  const transcript = JSON.parse(fs.readFileSync(TRANSCRIPT_PATH, "utf-8"));
  const timeline   = JSON.parse(fs.readFileSync(TIMELINE_PATH, "utf-8"));
  const hierarchy  = hr ? JSON.parse(fs.readFileSync(HIERARCHY_PATH, "utf-8")) : null;
  _cache = { mtime, transcript, timeline, hierarchy };
  return _cache;
}

export const GET: APIRoute = ({ request }) => {
  const { transcript, timeline, hierarchy } = loadFresh();
  const q = new URL(request.url).searchParams;
  const fleet = (q.get("fleet") || "").trim();
  const org   = (q.get("org") || "").trim();

  if (!fleet && !org) {
    return new Response(JSON.stringify(transcript), {
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
    if (transcript[id]) slim[id] = transcript[id];
  }
  return new Response(JSON.stringify(slim), {
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  });
};
