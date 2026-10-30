#!/usr/bin/env python3
"""Prepare stratified prose bundles for teacher labeling (schema v0.2).

Reads data/prose/*.jsonl, stratifies across fleet × role × time, writes:
  data/eval/bundles/agent_NN.txt  (50 agent_prose msgs per bundle)
  data/eval/bundles/user_NN.txt   (50 user_prose msgs per bundle)
  data/eval/bundles/_index.json   (manifest with per-bundle metadata)

Each bundle is a self-contained prompt: schema prelude + 50 MSG blocks.
Teacher agent replies with 50 JSONL rows, one per MSG, in order.

Target output: 1000 gold msgs ~= 4 user bundles (200) + 16 agent bundles (800).
Train output: 3000 msgs ~= 12 user + 48 agent (same stratification seed+offset).
"""
import json, re, hashlib, random
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone

ROOT = Path(".")
PROSE = ROOT / "data/prose"
OUT = ROOT / "data/eval/bundles"
OUT.mkdir(parents=True, exist_ok=True)

BUNDLE_SIZE = 50
MAX_TEXT_CHARS = 2000   # truncate per-msg text
MIN_TEXT_CHARS = 30     # drop stub msgs

# ─── Privacy blocklist (defense in depth; prose extractor already filters these) ───
BLOCKLIST_PROJECTS = {
    "[REDACTED]", "[REDACTED]", "[REDACTED]", "[REDACTED]", "[REDACTED]", "[REDACTED]",
    "[REDACTED]", "[REDACTED]", "[REDACTED]", "[REDACTED]",
}
BLOCKLIST_PATH_HINTS = ["/[REDACTED]", "/[REDACTED]/", "/[REDACTED]/", "/[REDACTED]/"]

def is_blocked(row: dict) -> bool:
    proj = (row.get("project") or "").lower()
    fleet = (row.get("fleet") or "").lower()
    if proj in BLOCKLIST_PROJECTS or fleet in BLOCKLIST_PROJECTS:
        return True
    text = row.get("text", "") or ""
    if any(hint in text for hint in BLOCKLIST_PATH_HINTS):
        return True
    return False

def clean_text(t: str) -> str:
    """Normalize whitespace, truncate, strip control chars."""
    if not t:
        return ""
    t = re.sub(r"\x00|\x1b\[[0-9;]*m", "", t)   # ANSI escapes
    t = re.sub(r"\s+\n", "\n", t)                # trailing ws
    t = t.strip()
    if len(t) > MAX_TEXT_CHARS:
        t = t[:MAX_TEXT_CHARS] + "…[truncated]"
    return t

def ts_month(ts) -> str:
    if not ts: return "unknown"
    try:
        if isinstance(ts, (int, float)):
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        else:
            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return dt.strftime("%Y-%m")
    except Exception:
        return "unknown"

def load_rows():
    rows = []
    for f in sorted(PROSE.glob("*.jsonl")):
        for line in f.open():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if is_blocked(r):
                continue
            t = clean_text(r.get("text", ""))
            if len(t) < MIN_TEXT_CHARS:
                continue
            role = r.get("role", "")
            if role not in ("user_prose", "assistant_prose"):
                continue
            rows.append({
                "sid": r.get("session_id"),
                "fleet": r.get("fleet") or "unknown",
                "project": r.get("project") or "unknown",
                "agent": r.get("agent_title") or "unknown",
                "role": "user" if role == "user_prose" else "agent",
                "text": t,
                "ts": r.get("ts"),
                "month": ts_month(r.get("ts")),
                "provider": r.get("provider") or "unknown",
                "model": r.get("model") or "unknown",
            })
    return rows

def stratified_sample(rows, n_target, seed=42):
    """Round-robin across (fleet, month) buckets so sample reflects spread, not volume."""
    random.seed(seed)
    buckets = defaultdict(list)
    for r in rows:
        buckets[(r["fleet"], r["month"])].append(r)
    for k in buckets:
        random.shuffle(buckets[k])
    bucket_keys = sorted(buckets.keys())
    out = []
    bi = 0
    exhausted = set()
    while len(out) < n_target and len(exhausted) < len(bucket_keys):
        k = bucket_keys[bi % len(bucket_keys)]
        if k not in exhausted:
            if buckets[k]:
                out.append(buckets[k].pop())
            else:
                exhausted.add(k)
        bi += 1
    return out

# ─── Rubric preludes per role ───

AGENT_PRELUDE = """\
You are labeling 50 agent messages against SCHEMA v0.2.

OUTPUT FORMAT: respond with EXACTLY 50 lines of JSONL, one JSON object per line, in message order (MSG 1 → MSG 50). No commentary, no code fences, no markdown. Pure JSONL.

SCHEMA (agent-turn fields):
  user_intent    ONE OF  feature-def | feature-update | unblock-context | unblock-infra | status-check | praise | frustration | null
                 (what the HUMAN wanted — infer from the agent's reply; null if agent is self-driven)
  agent_intent   ONE OF  report-progress | report-blocked | ask-clarification | handoff | celebration | summary | null
  agent_role     LIST subset of  [dev, tester, debugger, reviewer, planner, coordinator, manager, documenter, ops, analyst, researcher]
                 (multi-label — pick ALL that apply this message; empty list [] if unclear)
  blocker_type   ONE OF  waiting-human | rate-limit | tool-failure | retry-loop | context-exhausted | scope-ambiguous | null
  urgency        ONE OF  low | mid | high | null
  task_phase     ONE OF  planning | exploring | implementing | testing | debugging | reviewing | documenting | idle | null

ROLE DEFINITIONS (for agent_role multi-label):
  dev          — writing production code (Edit/Write/MultiEdit dominant)
  tester       — writing/running tests, interpreting failures
  debugger     — chasing a specific failure, no new feature intent
  reviewer     — reviewing another agent's work, posting findings
  planner      — decomposing problems, writing briefs, no immediate code
  coordinator  — routing work between agents, status aggregation
  manager      — setting direction, approving/rejecting, authority role
  documenter   — writing docs/READMEs/briefs
  ops          — deploys, migrations, infra, CI/CD, incident triage
  analyst      — measuring own-system data (metrics, evals, logs)
  researcher   — investigating external info (docs, repos, web, prior art)

EXAMPLE output row:
{"user_intent":"unblock-infra","agent_intent":"report-progress","agent_role":["dev","debugger"],"blocker_type":null,"urgency":"mid","task_phase":"debugging"}

Now the 50 messages:

"""

USER_PRELUDE = """\
You are labeling 50 HUMAN (user) messages against SCHEMA v0.2 (user-turn subset).

OUTPUT FORMAT: respond with EXACTLY 50 lines of JSONL, one JSON object per line, in message order (MSG 1 → MSG 50). No commentary, no code fences. Pure JSONL.

SCHEMA (user-turn fields):
  user_intent  ONE OF  feature-def | feature-update | unblock-context | unblock-infra | status-check | praise | frustration | null
  urgency      ONE OF  low | mid | high | null
  addresses    ONE OF  human | peer-agent | subagent | self | null
               (for a human message, almost always 'peer-agent' or 'subagent' — the human is directing an agent)

EXAMPLE output row:
{"user_intent":"feature-def","urgency":"mid","addresses":"peer-agent"}

Now the 50 messages:

"""

def pack_bundle(msgs, prelude):
    parts = [prelude]
    for i, m in enumerate(msgs, 1):
        head = f"MSG {i}\nSessionID: {m['sid']} · Fleet: {m['fleet']} · Project: {m['project']} · Agent: {m['agent']} · Role: {m['role']}\nTimestamp: {m['ts']}\n---\n"
        parts.append(head)
        parts.append(m['text'])
        parts.append("\n---\n")
    return "".join(parts)

def write_bundles(sample, prelude, prefix, start_idx=1):
    manifest = []
    i = start_idx
    for chunk_start in range(0, len(sample), BUNDLE_SIZE):
        chunk = sample[chunk_start:chunk_start + BUNDLE_SIZE]
        if len(chunk) < BUNDLE_SIZE:
            break  # drop incomplete final bundle
        txt = pack_bundle(chunk, prelude)
        path = OUT / f"{prefix}_{i:02d}.txt"
        path.write_text(txt)
        manifest.append({
            "bundle_id": f"{prefix}_{i:02d}",
            "path": str(path),
            "n_msgs": len(chunk),
            "role": chunk[0]['role'],
            "fleets": sorted(set(m['fleet'] for m in chunk)),
            "months": sorted(set(m['month'] for m in chunk)),
            "total_chars": sum(len(m['text']) for m in chunk),
            "msg_sids": [m['sid'] for m in chunk],
        })
        i += 1
    return manifest

def main():
    print("loading prose rows…")
    all_rows = load_rows()
    user_rows = [r for r in all_rows if r['role'] == 'user']
    agent_rows = [r for r in all_rows if r['role'] == 'agent']
    print(f"  loaded {len(all_rows):,} rows total")
    print(f"    user : {len(user_rows):,}")
    print(f"    agent: {len(agent_rows):,}")

    # ─── GOLD: 1000 msgs = 200 user + 800 agent (4 + 16 bundles) ───
    gold_user = stratified_sample(user_rows, 200, seed=42)
    gold_agent = stratified_sample(agent_rows, 800, seed=42)
    print(f"\ngold: {len(gold_user)} user + {len(gold_agent)} agent")

    gu_manifest = write_bundles(gold_user, USER_PRELUDE, "user", 1)
    ga_manifest = write_bundles(gold_agent, AGENT_PRELUDE, "agent", 1)
    print(f"gold bundles written: {len(gu_manifest)} user + {len(ga_manifest)} agent")

    # ─── TRAIN: 3000 msgs = 600 user + 2400 agent (12 + 48 bundles) ───
    # Different seed so train and gold don't overlap by chance
    train_user_pool = [r for r in user_rows if r not in gold_user]
    train_agent_pool = [r for r in agent_rows if r not in gold_agent]
    train_user = stratified_sample(train_user_pool, 600, seed=43)
    train_agent = stratified_sample(train_agent_pool, 2400, seed=43)
    print(f"\ntrain: {len(train_user)} user + {len(train_agent)} agent")

    tu_manifest = write_bundles(train_user, USER_PRELUDE, "train_user", 1)
    ta_manifest = write_bundles(train_agent, AGENT_PRELUDE, "train_agent", 1)
    print(f"train bundles written: {len(tu_manifest)} user + {len(ta_manifest)} agent")

    manifest = {
        "schema_version": "v0.2",
        "generated_ts": datetime.now(timezone.utc).isoformat(),
        "bundle_size": BUNDLE_SIZE,
        "gold": {"user": gu_manifest, "agent": ga_manifest},
        "train": {"user": tu_manifest, "agent": ta_manifest},
    }
    (OUT / "_index.json").write_text(json.dumps(manifest, indent=2))
    print(f"\nmanifest: {OUT / '_index.json'}")

    total_bundles = len(gu_manifest) + len(ga_manifest) + len(tu_manifest) + len(ta_manifest)
    total_msgs = sum(b['n_msgs'] for group in manifest.values() if isinstance(group, dict) for sub in group.values() if isinstance(sub, list) for b in sub)
    total_chars = sum(b['total_chars'] for group in manifest.values() if isinstance(group, dict) for sub in group.values() if isinstance(sub, list) for b in sub)
    est_input_tok = total_chars // 4
    print(f"\nTOTAL: {total_bundles} bundles · {total_msgs:,} msgs · ~{est_input_tok:,} input tokens (~{est_input_tok//15:,}k per bundle avg)")

if __name__ == "__main__":
    main()
