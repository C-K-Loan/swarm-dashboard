#!/usr/bin/env python3
"""Build timeline data for the /timeline dashboard.

Reads classified JSONL + prose JSONL, groups turns by session, segments by
state change or time gap, emits timeline.json with per-agent segment series.

usage: make_timeline.py <classified_dir> <prose_dir> <out_json>
"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fleet_merge import canonicalize as canonicalize_fleet  # noqa: E402


GAP_SEC = 300  # new segment if gap > 5 min
MIN_SEG_TURNS = 1


def _nn(v):
    return None if v in (None, "", "null", "none", "None") else v


def load_prose(prose_dir, project_names):
    """Return (session_id, ts) → {text, meta, role, agent_title, model, host, account}.
    session_meta also carries `models` = Counter(model -> count) across all turns.
    """
    lookup = {}
    session_meta = {}
    for name in project_names:
        p = Path(prose_dir) / f"{name}.jsonl"
        if not p.exists():
            continue
        for line in open(p):
            try:
                o = json.loads(line)
            except Exception:
                continue
            key = (o.get("session_id"), round(float(o.get("ts", 0)), 2))
            lookup[key] = {
                "text": o.get("text") or "",
                "meta": o.get("meta") or {},
                "role": o.get("role"),
            }
            sid = o.get("session_id")
            if not sid:
                continue
            if sid not in session_meta:
                session_meta[sid] = {
                    "agent_title": o.get("agent_title"),
                    "project": o.get("project"),
                    "fleet": o.get("fleet"),
                    "model": o.get("model"),
                    "host": o.get("host"),
                    "account": o.get("account"),
                    "provider": o.get("provider"),
                    "models": Counter(),
                }
            m = o.get("model") or ""
            if m:
                session_meta[sid]["models"][m] += 1
    return lookup, session_meta


def derive_state(labels, text, prose_meta):
    """One-word state for the segment."""
    blocker = _nn(labels.get("blocker_type"))
    if blocker == "context-exhausted":
        return "context_exhausted"
    if blocker:
        urgency = _nn(labels.get("urgency")) or "low"
        if urgency in ("mid", "high"):
            return "blocked"
        return "waiting"
    intent = _nn(labels.get("agent_intent"))
    if intent in ("report-progress", "handoff", "summary", "celebration"):
        return "working"
    if intent == "ask-clarification":
        return "waiting"
    return "idle"


def segment_session(turns, text_lookup):
    """Walk sorted turns, start new segment on state change or time gap > 5 min."""
    turns = sorted(turns, key=lambda t: t.get("ts", 0))
    segs = []
    cur = None
    for t in turns:
        txt_info = text_lookup.get((t.get("src_id"), round(float(t.get("ts", 0)), 2)), {})
        state = derive_state(t.get("labels", {}), txt_info.get("text", ""), txt_info.get("meta", {}))
        ts = t.get("ts")
        if cur is None or state != cur["state"] or (ts - cur["last_ts"]) > GAP_SEC:
            if cur is not None:
                segs.append(cur)
            cur = {
                "start_ts": ts,
                "end_ts": ts,
                "last_ts": ts,
                "state": state,
                "blocker_type": _nn(t["labels"].get("blocker_type")),
                "turns": [],
                "cost_usd": 0.0,
                "turn_tokens_in": 0,
                "turn_tokens_out": 0,
                "roles": Counter(),
                "intents": Counter(),
                "user_intents": Counter(),
                "blockers": Counter(),
                "urgencies": Counter(),
                "sample_text": "",
                "latest_text": "",
                "file_edits_count": 0,
                "errors_count": 0,
            }
        cur["end_ts"] = ts
        cur["last_ts"] = ts
        cur["turns"].append(t.get("ts"))
        u = t.get("usage", {})
        cur["cost_usd"] += u.get("cost_usd", 0)
        cur["turn_tokens_in"] += u.get("in", 0)
        cur["turn_tokens_out"] += u.get("out", 0)
        lab = t.get("labels", {})
        for field, counter_key in [
            ("agent_role", "roles"), ("agent_intent", "intents"),
            ("user_intent", "user_intents"),
            ("blocker_type", "blockers"), ("urgency", "urgencies"),
        ]:
            v = _nn(lab.get(field))
            if v:
                cur[counter_key][v] += 1
        meta = txt_info.get("meta", {})
        if meta.get("has_file_edit"):
            cur["file_edits_count"] += 1
        if meta.get("has_error"):
            cur["errors_count"] += 1
        text = txt_info.get("text", "").strip()
        if text:
            if not cur["sample_text"] and len(text) > 40:
                cur["sample_text"] = text[:280]
            cur["latest_text"] = text[:280]
    if cur is not None:
        segs.append(cur)

    # Normalize counters to dicts for JSON
    out = []
    for s in segs:
        if len(s["turns"]) < MIN_SEG_TURNS:
            continue
        duration = max(1, s["end_ts"] - s["start_ts"])
        out.append({
            "start_ts": s["start_ts"],
            "end_ts": s["end_ts"],
            "duration_sec": duration,
            "state": s["state"],
            "blocker_type": s["blocker_type"],
            "turn_count": len(s["turns"]),
            "cost_usd": round(s["cost_usd"], 6),
            "tokens_in": s["turn_tokens_in"],
            "tokens_out": s["turn_tokens_out"],
            "roles": dict(s["roles"]),
            "intents": dict(s["intents"]),
            "user_intents": dict(s["user_intents"]),
            "blockers": dict(s["blockers"]),
            "urgencies": dict(s["urgencies"]),
            "sample_text": s["sample_text"],
            "latest_text": s["latest_text"],
            "file_edits_count": s["file_edits_count"],
            "errors_count": s["errors_count"],
        })
    return out


def main():
    if len(sys.argv) < 4:
        print("usage: make_timeline.py <classified_dir> <prose_dir> <out_json> [model_filter]", file=sys.stderr)
        sys.exit(1)
    classified_dir = Path(sys.argv[1])
    prose_dir = Path(sys.argv[2])
    out_path = Path(sys.argv[3])
    # Optional 4th arg: filter classified files to those whose name contains "__<model_filter>"
    model_filter = sys.argv[4] if len(sys.argv) > 4 else None
    def _match(p):
        return (model_filter is None) or (f"__{model_filter}" in p.name)

    class_files = [p for p in classified_dir.glob("*.jsonl") if _match(p)]
    project_names = sorted({p.stem.split("__", 1)[0] for p in class_files})
    # Also include every raw prose file so re-labeled codex projects (post-v11-N) land
    prose_names = sorted({p.stem for p in Path(prose_dir).glob("*.jsonl")})
    all_names = sorted(set(project_names) | set(prose_names))
    print(f"[info] model_filter={model_filter!r} classified files={len(class_files)}", file=sys.stderr)
    print(f"[info] projects (classified+prose): {all_names}", file=sys.stderr)

    text_lookup, session_meta = load_prose(prose_dir, all_names)
    print(f"[info] loaded prose: {len(text_lookup)} turns / {len(session_meta)} sessions", file=sys.stderr)

    # Group classified by session
    by_session = defaultdict(list)
    for p in sorted(class_files):
        for line in open(p):
            try:
                o = json.loads(line)
            except Exception:
                continue
            by_session[o.get("src_id")].append(o)
    print(f"[info] classified sessions: {len(by_session)}", file=sys.stderr)

    # Build per-agent segments
    agents = []
    min_ts = float("inf")
    max_ts = 0
    for sid, turns in by_session.items():
        meta = session_meta.get(sid, {})
        segs = segment_session(turns, text_lookup)
        if not segs:
            continue
        for s in segs:
            min_ts = min(min_ts, s["start_ts"])
            max_ts = max(max_ts, s["end_ts"])
        models_counter = meta.get("models") or Counter()
        models_list = [{"model": k, "count": v} for k, v in models_counter.most_common()]
        raw_fleet = meta.get("fleet") or ""
        canon_fleet = canonicalize_fleet(raw_fleet)
        agents.append({
            "id": sid,
            "title": meta.get("agent_title") or sid[:8],
            "project": meta.get("project") or "",
            "fleet": canon_fleet,
            "fleet_raw": raw_fleet,
            "model": meta.get("model") or "",
            "models": models_list,
            "host": meta.get("host") or "",
            "account": meta.get("account") or "",
            "provider": meta.get("provider") or "",
            "total_turns": sum(s["turn_count"] for s in segs),
            "total_cost_usd": round(sum(s["cost_usd"] for s in segs), 4),
            "segments": segs,
        })

    # Sort agents: by project, then by first segment ts
    def _sort_key(a):
        first = min(s["start_ts"] for s in a["segments"])
        return (a["project"], first)
    agents.sort(key=_sort_key)

    report = {
        "schema_v": "0.1",
        "generated_at": max_ts,
        "time_range": {"min_ts": min_ts, "max_ts": max_ts, "span_sec": max_ts - min_ts},
        "agents": agents,
        "aggregate": {
            "agent_count": len(agents),
            "segment_count": sum(len(a["segments"]) for a in agents),
            "total_cost_usd": round(sum(a["total_cost_usd"] for a in agents), 4),
            "total_turns": sum(a["total_turns"] for a in agents),
        },
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    json.dump(report, open(out_path, "w"), indent=2)
    print(f"[done] agents={len(agents)} segments={report['aggregate']['segment_count']} span={report['time_range']['span_sec']/3600:.1f}h → {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
