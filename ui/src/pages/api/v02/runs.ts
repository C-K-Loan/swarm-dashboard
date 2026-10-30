import type { APIRoute } from 'astro';
import { timelineRuns, headers } from '../../../lib/v02-store';
export const prerender = false;

// GET -> replayable runs with live coverage (completed/total from each status.json).
export const GET: APIRoute = async () => {
  try { return new Response(JSON.stringify({ runs: timelineRuns() }), { headers }); }
  catch { return new Response(JSON.stringify({ error: 'Could not list runs.' }), { status: 500, headers }); }
};
