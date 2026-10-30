import type { APIRoute } from 'astro';
import fs from 'node:fs';
import path from 'node:path';
import { ROOT } from '../../../lib/v02-store';
export const prerender = false;

// v0.1.B hierarchical bullet merge (scripts/v01b/hier_merge.py output), cached by mtime.
const cache = new Map<string, { mtimeMs: number; body: string }>();
export const GET: APIRoute = ({ url }) => {
  const name = (url.searchParams.get('name') || 'gamejam-day1').replace(/[^a-z0-9_-]/gi, '');
  const file = path.join(ROOT, 'data', 'v01b', name, 'merge_tree.json');
  const headers = { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' };
  if (!fs.existsSync(file)) return new Response(JSON.stringify({ error: 'Merged summaries are not built yet.' }), { status: 404, headers });
  const mtimeMs = fs.statSync(file).mtimeMs, hit = cache.get(file);
  if (hit && hit.mtimeMs === mtimeMs) return new Response(hit.body, { headers });
  const body = fs.readFileSync(file, 'utf8');
  cache.set(file, { mtimeMs, body });
  return new Response(body, { headers });
};
