#!/usr/bin/env python3
"""Pair v0.2 training labels with their bundle messages into SFT training rows.

Input:
  data/sft/train_v2.jsonl            — 3000 label rows (6-field agent OR 3-field user),
                                       ordered by bundle (50 rows per bundle, 60 bundles).
  data/eval/bundles/train_agent_NN.txt
  data/eval/bundles/train_user_NN.txt — 50 MSG blocks each, same order as labels.

Output:
  data/sft/train_v2_sft.jsonl       — 3000 rows in OpenAI messages format.

Each row:
  {"messages":[{"role":"user","content":"<v0.2 prompt + MSG>"},
               {"role":"assistant","content":"<compact label JSON>"}]}

Agent rows use the 6-field prompt. User rows use the 3-field prompt.
Prompts are a single-message adaptation of the AGENT_PRELUDE / USER_PRELUDE from
scripts/prepare_label_bundles.py (same schema, same role defs, same enums).
"""
import json
import re
import sys
from pathlib import Path
from collections import defaultdict

ROOT = Path(".")
LABELS = ROOT / "data/sft/train_v2.jsonl"
BUNDLES = ROOT / "data/eval/bundles"
OUT = ROOT / "data/sft/train_v2_sft.jsonl"

AGENT_PROMPT = """You classify ONE AI-agent prose message against SCHEMA v0.2.

OUTPUT FORMAT: respond with EXACTLY one JSON object on one line. No commentary, no code fences.

SCHEMA (agent-turn fields):
  user_intent    ONE OF  feature-def | feature-update | unblock-context | unblock-infra | status-check | praise | frustration | null
                 (what the HUMAN wanted; infer from the agent's reply; null if agent is self-driven)
  agent_intent   ONE OF  report-progress | report-blocked | ask-clarification | handoff | celebration | summary | null
  agent_role     LIST subset of  [dev, tester, debugger, reviewer, planner, coordinator, manager, documenter, ops, analyst, researcher]
                 (multi-label; pick ALL that apply this message; empty list [] if unclear)
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

EXAMPLE output:
{"user_intent":"unblock-infra","agent_intent":"report-progress","agent_role":["dev","debugger"],"blocker_type":null,"urgency":"mid","task_phase":"debugging"}

Message to classify:
SessionID: @@SID@@ · Fleet: @@FLEET@@ · Project: @@PROJECT@@ · Agent: @@AGENT@@ · Role: @@ROLE@@
Timestamp: @@TS@@
---
@@TEXT@@
---"""

USER_PROMPT = """You classify ONE HUMAN (user) prose message against SCHEMA v0.2 (user-turn subset).

OUTPUT FORMAT: respond with EXACTLY one JSON object on one line. No commentary, no code fences.

SCHEMA (user-turn fields):
  user_intent  ONE OF  feature-def | feature-update | unblock-context | unblock-infra | status-check | praise | frustration | null
  urgency      ONE OF  low | mid | high | null
  addresses    ONE OF  human | peer-agent | subagent | self | null
               (for a human message, almost always 'peer-agent' or 'subagent' — the human is directing an agent)

EXAMPLE output:
{"user_intent":"feature-def","urgency":"mid","addresses":"peer-agent"}

Message to classify:
SessionID: @@SID@@ · Fleet: @@FLEET@@ · Project: @@PROJECT@@ · Agent: @@AGENT@@ · Role: @@ROLE@@
Timestamp: @@TS@@
---
@@TEXT@@
---"""

AGENT_FIELDS = ("user_intent", "agent_intent", "agent_role", "blocker_type", "urgency", "task_phase")
USER_FIELDS = ("user_intent", "urgency", "addresses")

# MSG header: "MSG N\nSessionID: <sid> · Fleet: <f> · Project: <p> · Agent: <a> · Role: <r>\nTimestamp: <ts>\n---\n<text>\n---\n"
MSG_RE = re.compile(
    r"MSG\s+(\d+)\s*\n"
    r"SessionID:\s*(?P<sid>\S+)\s*·\s*Fleet:\s*(?P<fleet>.+?)\s*·\s*Project:\s*(?P<project>.+?)\s*·\s*Agent:\s*(?P<agent>.+?)\s*·\s*Role:\s*(?P<role>\S+)\s*\n"
    r"Timestamp:\s*(?P<ts>.+?)\s*\n"
    r"---\s*\n"
    r"(?P<text>.*?)\n---\s*\n",
    re.DOTALL,
)


def parse_bundle(path: Path) -> list[dict]:
    """Return list of 50 message dicts from a bundle txt file."""
    text = path.read_text()
    # Drop prelude: everything before the first "MSG 1"
    i = text.find("MSG 1\n")
    if i < 0:
        raise ValueError(f"no 'MSG 1' in {path}")
    body = text[i:]
    msgs = []
    for m in MSG_RE.finditer(body):
        msgs.append({
            "n": int(m.group(1)),
            "sid": m.group("sid").strip(),
            "fleet": m.group("fleet").strip(),
            "project": m.group("project").strip(),
            "agent": m.group("agent").strip(),
            "role": m.group("role").strip(),
            "ts": m.group("ts").strip(),
            "text": m.group("text").strip(),
        })
    return msgs


def main():
    labels = [json.loads(l) for l in LABELS.open()]
    print(f"loaded {len(labels)} label rows", file=sys.stderr)

    # Group by bundle, preserve order within each bundle
    by_bundle = defaultdict(list)
    bundle_order = []
    for i, row in enumerate(labels):
        b = row.get("_bundle")
        if not b:
            raise ValueError(f"row {i} missing _bundle")
        if b not in by_bundle:
            bundle_order.append(b)
        by_bundle[b].append(row)

    n_written = 0
    skipped = 0
    with OUT.open("w") as fout:
        for bname in bundle_order:
            rows = by_bundle[bname]
            path = BUNDLES / f"{bname}.txt"
            if not path.exists():
                print(f"[SKIP] no bundle file {path}", file=sys.stderr)
                skipped += len(rows)
                continue
            msgs = parse_bundle(path)
            if len(msgs) != len(rows):
                print(f"[WARN] {bname}: {len(msgs)} msgs vs {len(rows)} labels; truncating to min",
                      file=sys.stderr)
            is_user = bname.startswith("train_user") or bname.startswith("user_")
            prompt_tpl = USER_PROMPT if is_user else AGENT_PROMPT
            fields = USER_FIELDS if is_user else AGENT_FIELDS
            for i, (row, msg) in enumerate(zip(rows, msgs), 1):
                user_content = (prompt_tpl
                    .replace("@@SID@@", msg["sid"])
                    .replace("@@FLEET@@", msg["fleet"])
                    .replace("@@PROJECT@@", msg["project"])
                    .replace("@@AGENT@@", msg["agent"])
                    .replace("@@ROLE@@", msg["role"])
                    .replace("@@TS@@", msg["ts"])
                    .replace("@@TEXT@@", msg["text"]))
                label_only = {k: row.get(k) for k in fields}
                assistant_content = json.dumps(label_only, separators=(",", ":"))
                sft_row = {
                    "messages": [
                        {"role": "user", "content": user_content},
                        {"role": "assistant", "content": assistant_content},
                    ],
                }
                fout.write(json.dumps(sft_row) + "\n")
                n_written += 1

    print(f"wrote {n_written} SFT rows → {OUT}", file=sys.stderr)
    print(f"skipped: {skipped}", file=sys.stderr)
    assert n_written == 3000, f"expected 3000, got {n_written}"
    print("OK: 3000 rows", file=sys.stderr)


if __name__ == "__main__":
    main()
