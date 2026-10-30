import type { APIRoute } from 'astro';
import fs from 'node:fs';
import path from 'node:path';
import { Worker } from 'node:worker_threads';
import { dataDir, headers } from '../../../lib/v02-store';
export const prerender = false;

// Layout v2: small turn-index.json + immutable turn-states/<id>.json (~1.6 MB each, almost all state_after).
// Selection returns light rows only; the cursor turn's state_after/llm_input/messages come from GET /api/v02/turn.
const lightCache = new Map<string, any>(); // turn-states never change once written
const MAX_LIGHT = 200; // newest predicted turns per request that get light fields; older ones stay index rows
const HEAVY = ['messages', 'task_names', 'state_after', 'llm_input'];
// Files are indent=2 JSON with task_names, state_after, llm_input, _publication last; only the head before task_names is parsed.
const CUT = Buffer.from('\n  "task_names": ');

// turn-index.json is up to ~12 MB and live runs rewrite it constantly; parsing it on the main thread stalled every
// other request. A worker owns the parsed index (reused for 15 s across rewrites) and returns only the picked rows.
const WORKER = `
const { parentPort } = require('node:worker_threads');
const fs = require('node:fs');
const cache = new Map();
parentPort.on('message', ({ id, file, windows, limit, offset }) => {
  try {
    let hit = cache.get(file);
    const stat = fs.statSync(file, { throwIfNoEntry: false });
    if (!stat) { parentPort.postMessage({ id, missing: true }); return; }
    if (!hit || (hit.mtimeMs !== stat.mtimeMs && Date.now() - hit.at > 15000)) {
      const data = JSON.parse(fs.readFileSync(file, 'utf8'));
      for (const t of data.turns) delete t.search_text;
      hit = { mtimeMs: stat.mtimeMs, at: Date.now(), manifest: data.manifest, status: data.status, turns: data.turns };
      cache.set(file, hit);
    }
    const bySid = new Map();
    for (const w of windows) bySid.set(w.sid, [...(bySid.get(w.sid) || []), w]);
    const picked = hit.turns.filter(t => bySid.get(t.session_id)?.some(w => t.end_ts >= w.start && t.end_ts < w.end));
    const predicted = picked.filter(t => t.has_prediction).sort((a, b) => b.end_ts - a.end_ts || b.ordinal - a.ordinal);
    parentPort.postMessage({ id, manifest: hit.manifest, status: hit.status, top: predicted.slice(offset, offset + limit), pickedCount: picked.length, predictedCount: predicted.length });
  } catch (e) { parentPort.postMessage({ id, error: String(e) }); }
});`;
let worker: Worker | null = null, seq = 0;
const waiting = new Map<number, (v: any) => void>();
function pick(file: string, windows: any[], offset = 0): Promise<any> {
  if (!worker) {
    worker = new Worker(WORKER, { eval: true });
    worker.on('message', (m: any) => { waiting.get(m.id)?.(m); waiting.delete(m.id); });
    worker.on('error', () => { for (const r of waiting.values()) r({ error: 'worker' }); waiting.clear(); worker = null; });
    worker.unref();
  }
  const id = ++seq;
  return new Promise(resolve => { waiting.set(id, resolve); worker!.postMessage({ id, file, windows, limit: MAX_LIGHT, offset }); });
}

async function lightTurn(dir: string, id: string) {
  const file = path.join(dir, 'turn-states', id + '.json');
  if (lightCache.has(file)) return lightCache.get(file);
  const fh = await fs.promises.open(file, 'r').catch(() => null);
  if (!fh) return null;
  let value: any = null;
  try {
    const chunks: Buffer[] = [];let pos = 0;
    for (;;) {
      const buf = Buffer.alloc(32768);const { bytesRead } = await fh.read(buf, 0, buf.length, pos);
      if (!bytesRead) break;
      chunks.push(buf.subarray(0, bytesRead));pos += bytesRead;
      const head = Buffer.concat(chunks), cut = head.indexOf(CUT);
      if (cut >= 0) {
        const text = head.subarray(0, cut).toString('utf8').replace(/,\s*$/, '') + '\n}';
        try { value = JSON.parse(text); } catch { value = null; }
        break;
      }
      if (pos > 4 << 20) break; // marker missing: fall through to a full parse below
    }
    if (!value) value = JSON.parse(await fs.promises.readFile(file, 'utf8'));
  } finally { await fh.close(); }
  for (const k of HEAVY) delete value[k];
  // Rows only need what changesAtCursor reads; the cursor turn's full prediction comes from /api/v02/turn.
  if (value.prediction) value.prediction = true;
  const rp = value.resolved_prediction;
  if (rp) value.resolved_prediction = { decisions: rp.decisions || [], relationships: rp.relationships || [], tasks: (rp.tasks || []).map((t: any) => ({ ref: t.ref })) };
  value.lazy = true;
  if (lightCache.size > 20000) lightCache.clear();
  lightCache.set(file, value);
  return value;
}

async function mapLimit<T, R>(items: T[], limit: number, fn: (t: T) => Promise<R>) {
  const out: R[] = new Array(items.length);let next = 0;
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, async () => { while (next < items.length) { const i = next++;out[i] = await fn(items[i]); } }));
  return out;
}

const offsetOf = (body: any) => Math.max(0, Math.floor(Number(body?.offset) || 0));
// offset pages further back through the labeled turns (newest first) for replay beyond MAX_LIGHT.
// POST {dataset, windows:[{sid,start,end}], offset?} -> only turns ending inside a window (half-open, same as selectedTurns).
export const POST: APIRoute = async ({ request }) => {
  try {
    const body = await request.json();
    const dir = dataDir(body?.dataset || null);
    const windows = Array.isArray(body?.windows) ? body.windows.filter((w: any) => w && typeof w.sid === 'string' && Number.isFinite(w.start) && Number.isFinite(w.end)) : [];
    const data = await pick(path.join(dir, 'turn-index.json'), windows.map((w: any) => ({ sid: w.sid, start: w.start, end: w.end })), Math.max(0, Math.floor(Number(body?.offset) || 0)));
    if (data.missing) return new Response(JSON.stringify({ error: 'Dataset is being prepared.' }), { status: 503, headers });
    if (data.error) throw new Error(data.error);
    // Only replayable rows travel; unlabeled and capped turns are just counted (wide gamejam picks are ~11k rows).
    const turns = (await mapLimit(data.top, 16, (t: any) => lightTurn(dir, t.id))).filter(Boolean);
    const statusFile = path.join(dir, 'status.json');
    const status = await fs.promises.readFile(statusFile, 'utf8').then(JSON.parse).catch(() => data.status);
    return new Response(JSON.stringify({ manifest: data.manifest, status, dataset: path.basename(dir), turns, omitted: data.pickedCount - turns.length, predicted: data.predictedCount, capped: Math.max(0, data.predictedCount - offsetOf(body) - MAX_LIGHT) }), { headers });
  } catch {
    return new Response(JSON.stringify({ error: 'Could not load the selected turns. Retry shortly.' }), { status: 500, headers });
  }
};
