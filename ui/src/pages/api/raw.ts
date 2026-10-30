import type { APIRoute } from 'astro';
import { readFile, realpath, stat } from 'node:fs/promises';
import { isAbsolute, resolve, sep } from 'node:path';
import mime from 'mime';

// Query parameters require an on-demand route; production always returns 404.
export const prerender = false;
const roots = [
  '[REDACTED_PATH]',
  '[REDACTED_PATH]',
  '.',
];
const allowed = (path: string) => roots.some(root => path.startsWith(root + sep))
  && !path.split(sep).some(part => /^(?:[REDACTED]|[REDACTED]|[REDACTED]|[REDACTED]|[REDACTED]|[REDACTED])$/i.test(part));
const fail = (status: number) => new Response(null, { status });

export const GET: APIRoute = async ({ request }) => {
  if (!import.meta.env.DEV) return fail(404);
  const path = new URL(request.url).searchParams.get('path');
  if (!path || !isAbsolute(path) || path.includes('\0')) return fail(400);
  // Check both the lexical path and its symlink target before reading any bytes.
  if (!allowed(resolve(path))) return fail(403);
  try {
    const canonical = await realpath(path);
    if (!allowed(canonical)) return fail(403);
    const info = await stat(canonical);
    if (!info.isFile()) return fail(400);
    const body = await readFile(canonical);
    const type = mime.getType(canonical) || 'application/octet-stream';
    return new Response(new Uint8Array(body), {
      headers: {
        'Content-Type': type + (/^text\//.test(type) ? '; charset=utf-8' : ''),
        'Content-Length': String(body.byteLength),
        'Cache-Control': 'no-store',
        'X-Content-Type-Options': 'nosniff',
        'Content-Security-Policy': 'sandbox allow-scripts',
      },
    });
  } catch (error) {
    return fail((error as NodeJS.ErrnoException).code === 'EACCES' ? 403 : 404);
  }
};
