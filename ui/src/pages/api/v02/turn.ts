import type { APIRoute } from 'astro';
import fs from 'node:fs';
import path from 'node:path';
import { dataDir, headers } from '../../../lib/v02-store';
export const prerender = false;

// GET ?dataset=&id= -> one full turn-state file (state_after, llm_input, messages), streamed as-is without parsing.
export const GET: APIRoute = async ({ url }) => {
  const id = url.searchParams.get('id') || '';
  if (!/^[\w.-]+$/.test(id)) return new Response(JSON.stringify({ error: 'Bad turn id.' }), { status: 400, headers });
  const file = path.join(dataDir(url.searchParams.get('dataset') || null), 'turn-states', id + '.json');
  const text = await fs.promises.readFile(file).catch(() => null);
  if (!text) return new Response(JSON.stringify({ error: 'Turn state not found.' }), { status: 404, headers });
  return new Response(text, { headers: { ...headers, 'Cache-Control': 'private, max-age=3600' } });
};
