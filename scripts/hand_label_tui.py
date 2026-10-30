#!/usr/bin/env python3
"""Hand-label TUI for building ground-truth annotations.

Prompts the operator for the 5 schema v0.1 fields on each sampled message,
writes a JSONL truth file. Resumes from last position if truth file exists.

usage: hand_label_tui.py <sample_jsonl> <truth_jsonl> [--n 50] [--project <name>]
"""
import argparse
import json
import os
import sys
import termios
import tty
from pathlib import Path

CHOICES = {
    "user_intent": [
        ("", "null (skip)"),
        ("f", "feature-def"),
        ("u", "feature-update"),
        ("c", "unblock-context"),
        ("i", "unblock-infra"),
        ("s", "status-check"),
        ("p", "praise"),
        ("x", "frustration"),
    ],
    "agent_intent": [
        ("", "null"),
        ("p", "report-progress"),
        ("b", "report-blocked"),
        ("a", "ask-clarification"),
        ("h", "handoff"),
        ("c", "celebration"),
        ("s", "summary"),
    ],
    "agent_role": [
        ("", "null"),
        ("w", "worker"),
        ("d", "dev"),
        ("m", "manager"),
        ("r", "reviewer"),
        ("p", "planner"),
    ],
    "blocker_type": [
        ("", "null (no blocker)"),
        ("w", "waiting-human"),
        ("r", "rate-limit"),
        ("t", "tool-failure"),
        ("l", "retry-loop"),
        ("c", "context-exhausted"),
        ("s", "scope-ambiguous"),
    ],
    "urgency": [
        ("", "null"),
        ("l", "low"),
        ("m", "mid"),
        ("h", "high"),
    ],
}


def getch():
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    if ch == "\x03":  # Ctrl-C
        raise KeyboardInterrupt
    return ch


def prompt_field(name, choices):
    print(f"\n  \033[1m{name}:\033[0m")
    key_to_val = {k: v for k, v in choices}
    for k, v in choices:
        label = k or "<enter>"
        print(f"    [{label}] {v}")
    while True:
        print(f"    > ", end="", flush=True)
        ch = getch()
        if ch == "\r":
            ch = ""
        print(ch or "<enter>")
        if ch in key_to_val:
            return key_to_val[ch] if key_to_val[ch] not in ("null", "null (skip)", "null (no blocker)") else None


def load_done(truth_path):
    done = set()
    if truth_path.exists():
        for line in truth_path.open():
            try:
                o = json.loads(line)
                k = (o.get("src_id"), round(float(o["ts"]), 2))
                done.add(k)
            except Exception:
                continue
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sample")
    ap.add_argument("truth")
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--project", default=None, help="only label msgs from this project")
    args = ap.parse_args()

    sample_path = Path(args.sample)
    truth_path = Path(args.truth)
    truth_path.parent.mkdir(parents=True, exist_ok=True)

    rows = [json.loads(l) for l in sample_path.open()]
    if args.project:
        rows = [r for r in rows if r.get("project") == args.project]
    done = load_done(truth_path)
    todo = [r for r in rows if (r.get("session_id") or r.get("src_id"), round(float(r.get("ts", 0)), 2)) not in done]

    print(f"\n\033[1mHand-label TUI\033[0m")
    print(f"  sample: {sample_path}  ({len(rows)} rows, {len(done)} already labeled)")
    print(f"  todo:   {len(todo)}  — labelling up to {args.n}")
    print(f"  truth:  {truth_path}  (append mode)")
    print(f"  tip: ctrl-c to pause, resume safe\n")

    fh = open(truth_path, "a")
    count = 0
    for row in todo[: args.n]:
        count += 1
        print(f"\n\n===== {count}/{min(args.n, len(todo))}  session={row.get('agent_title')!r}  project={row.get('project')} =====")
        print(f"[role={row.get('role')}]  [tool_calls={row.get('meta', {}).get('tool_call_count')}]  [has_error={row.get('meta', {}).get('has_error')}]")
        print()
        text = (row.get("text") or "").strip()
        if len(text) > 1200:
            text = text[:1200] + "\n...[truncated]"
        print(text)
        print()

        truth = {}
        try:
            for field, choices in CHOICES.items():
                truth[field] = prompt_field(field, choices)
        except KeyboardInterrupt:
            print("\n\npaused. resume by rerunning the same command.")
            break

        rec = {
            "src_id": row.get("session_id") or row.get("src_id"),
            "ts": row.get("ts"),
            "project": row.get("project"),
            "truth": truth,
        }
        fh.write(json.dumps(rec) + "\n")
        fh.flush()
    fh.close()
    print(f"\nlabeled {count} total. truth file: {truth_path}")


if __name__ == "__main__":
    main()
