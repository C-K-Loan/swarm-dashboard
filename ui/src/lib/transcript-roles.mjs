// Reclassify transcript rows the indexer marked "u" (human) but that agents or the harness wrote.
// Index roles: u human · x a2a (a2a = sender) · sys harness · spawn brief (a2a = spawner) · a/s agent.
// Single source for "who wrote this user turn": transcript, A2A graph, nav counts and the v0.2 export all use it.

// CKL only types into these sessions; any other unmarked user turn in that fleet was delivered by an agent.
// codex-gamejam-49eed4dc is gamejam-master's Codex continuation on data-host (CKL types there; gamejam-codex-* report to it).
export const HUMAN_FACING = { gamejam: ["gamejam-master", "codex-gamejam-49eed4dc"] };
export const UNKNOWN_SENDER = "unknown sender";

const PASTE = /^\s*(?:<pasted_content[^>]*>\s*)?/;
const PASTE_OPEN = /^\s*<pasted_content[^>]*>/;
const HARNESS_TAG = /^<(?:environment_context|system-reminder|task-notification|local-command-caveat|local-command-stdout|turn_aborted|recommended_plugins|fork-boilerplate|codex_internal_context|bash-stdout|user-prompt-submit-hook)\b/;
const HARNESS_TEXT = /^(?:\[(?:SYSTEM NOTIFICATION|heartbeat|Cross-session delivery notice)\b|\[Image: original \d+x\d+|\[from cct-harness-system\]|Background command "[^"]*" completed \(exit code|(?:[\w-]+\s+)?toolu_\w+\s+\S+\s+completed|This session is being continued from a previous conversation|Caveat: The messages below were generated)/;
// Typed by CKL even though they look machine-made.
const HUMAN_ACTION = /^(?:\[Request interrupted by user|<command-name>|<bash-input>)/;
// A short typed or harness stub can precede the cct prefix: "continue[from X]", "/model gpt-6-luna[from X]".
const FROM = /^(?:\[Image #\d+\]\s*)*(?:[^\n\[]{0,24})\[from ([^\]\s]+)[^\]]*\]/;
// cct model toggles typed into an agent pane.
const MODEL_SWITCH = /^\/model\s+\S+\s*$/;
// "[tester] ...", "[harness-dev -> cct-manager] ...", "[cct-manager BRIEF] ...", "[master, no reply needed] ..."
const BRACKET_SENDER = /^\[([A-Za-z][\w.@]*(?:[-/][\w.@]+)*)(?=[\]\s,:—-]|\s*->|\s*→)[^\]\n]{0,120}\]/;
const NOT_A_SENDER = new Set(["Image", "Request", "SYSTEM", "heartbeat", "Cross-session", "Pasted", "TODO", "WIP", "NOTE", "x", "X"]);
const SHORT_AGENT_NAMES = new Set(["master", "tester", "dev", "reviewer", "architect", "manager", "orchestrator", "trainer", "worker"]);
const BRIEF = /^(?:You are\s|You report to\s)/;
// Briefs CKL never types himself: "You are a research agent", "You are gamejam-dev, ...".
const STRONG_BRIEF = /^You are (?:a|an|the) [^.\n]{0,60}\b(?:agent|lane|dev|worker|fork|engineer|reviewer|researcher)\b|^You are [a-z][\w]*-[\w-]+\b/;
const BRIEF_TAG = /\b(?:BRIEF|kickoff|KICKOFF)\b/;

// true / false for fleets with a human-facing list; null (legacy: unmarked = human) for fleets without one.
export function isHumanFacing(title) {
  const t = String(title || "");
  for (const [fleet, names] of Object.entries(HUMAN_FACING)) if (t.includes(fleet)) return names.includes(t);
  return null;
}

// Bus row (orchestrator-messages via bus_index.json) that delivered this text to receiver within +/- windowSec.
export function busSenderFor(busRows, ts, text, windowSec = 120) {
  if (!Array.isArray(busRows) || !busRows.length) return null;
  const key = String(text || "").replace(PASTE, "").replace(/^\s*\[from\s+[^\]]+\]\s*/i, "").slice(0, 120).trim().slice(0, 60);
  if (!key) return null;
  let lo = 0, hi = busRows.length;
  while (lo < hi) { const mid = (lo + hi) >>> 1; if (busRows[mid].ts < ts - windowSec) lo = mid + 1; else hi = mid; }
  for (let i = lo; i < busRows.length && busRows[i].ts <= ts + windowSec; i++) {
    const b = busRows[i];
    if (b.prefix && b.prefix.startsWith(key) && b.from && !/^(?:human|ckl)$/i.test(b.from)) return b.from;
  }
  return null;
}

function looksLikeAgent(name) {
  if (NOT_A_SENDER.has(name)) return false;
  return /[-@]/.test(name) || SHORT_AGENT_NAMES.has(name.split("/")[0]);
}

// Returns {r, a2a, why} for one "u" row; first = first "u" row of its session.
// humanFacing: false = CKL never types here (unmarked rows get a sender, never "u"); null = legacy.
export function classifyUserRow(text, { first = false, spawnParent = null, humanFacing = null, busSender = null } = {}) {
  const raw = String(text || "");
  const body = raw.replace(PASTE, "");
  if (HUMAN_ACTION.test(body)) return humanFacing === false
    ? { r: "sys", a2a: null, why: "harness action in agent pane" }
    : { r: "u", a2a: null, why: "human action" };
  if (HARNESS_TAG.test(body) || HARNESS_TEXT.test(body)) return { r: "sys", a2a: null, why: "harness" };
  if (humanFacing === false && MODEL_SWITCH.test(body)) return { r: "sys", a2a: null, why: "harness action in agent pane" };
  let m = body.match(FROM);
  if (m) return BRIEF.test(body.slice(m[0].length).trimStart())
    ? { r: "spawn", a2a: m[1], why: "[from X] brief" }
    : { r: "x", a2a: m[1], why: "[from X]" };
  m = body.match(BRACKET_SENDER);
  if (m && looksLikeAgent(m[1])) {
    const sender = m[1].split("/")[0];
    const rest = body.slice(m[0].length).trimStart();
    if (BRIEF_TAG.test(m[0]) || BRIEF.test(rest)) return { r: "spawn", a2a: sender, why: "bracket brief" };
    return { r: "x", a2a: sender, why: "[sender] prefix" };
  }
  // cct spawns deliver the brief by paste: a pasted first message in a session with a known spawner.
  if (first && spawnParent && PASTE_OPEN.test(raw)) return { r: "spawn", a2a: spawnParent, why: "pasted first brief" };
  if (STRONG_BRIEF.test(body) || (BRIEF.test(body) && (first || spawnParent))) return { r: "spawn", a2a: spawnParent || null, why: "spawn brief" };
  if (busSender) return { r: "x", a2a: busSender, why: "bus delivery" };
  if (humanFacing === false) {
    if (first && spawnParent) return { r: "spawn", a2a: spawnParent, why: "first input from spawner" };
    return { r: "x", a2a: UNKNOWN_SENDER, why: "not a human-facing session" };
  }
  return { r: "u", a2a: null, why: "human" };
}

// Mutates rows in place; sessions = {sid: rows[]}, parentOf(sid) -> spawner title or null.
// Recomputed from r0 on every call, so spawn parents or the bus index arriving later upgrade earlier guesses.
// opts.humanFacing(sid) -> true/false/null; opts.busSender(sid, row) -> sender title or null.
export function reclassifyTranscript(sessions, parentOf = () => null, { humanFacing = () => null, busSender = () => null } = {}) {
  let changed = 0;
  for (const [sid, rows] of Object.entries(sessions || {})) {
    if (!Array.isArray(rows)) continue;
    let seenUser = false;
    for (const row of rows) {
      if (!row) continue;
      const original = row.r0 ?? row.r;
      if (original !== "u") continue;
      const first = !seenUser;
      seenUser = true;
      if (row.a2a0 === undefined) row.a2a0 = row.a2a ?? null;
      const out = classifyUserRow(row.x, { first, spawnParent: parentOf(sid), humanFacing: humanFacing(sid), busSender: busSender(sid, row) });
      const a2a = out.r === "u" ? row.a2a0 : (out.a2a || row.a2a0 || null);
      if (row.r !== out.r || (row.a2a ?? null) !== a2a) { row.r0 = "u"; row.r = out.r; row.a2a = a2a; changed++; }
      row.why = out.why;
    }
  }
  return changed;
}
