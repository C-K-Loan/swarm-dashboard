#!/usr/bin/env python3
"""Build local org membership using the CCT dashboard's own resolver, without importing its server.

Reads the current roster via GET, orchestrator SQLite in read-only mode, and timeline
metadata for historical sessions. No names are used to join session identities.
Outputs only org membership/provenance, never roster paths or original details.
Run again after CCT assignments change; output stays ignored local runtime data.
"""
import argparse
import ast
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def load_resolver(app_path, db_path, seed_path):
    source = Path(app_path).read_text()
    tree = ast.parse(source)
    constants = {"ORGS", "FLEET_ORG", "FLEET_ORG_PREFIX", "DEFAULT_ORG"}
    namespace = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in constants for t in node.targets):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in constants:
                    namespace[target.id] = ast.literal_eval(node.value)
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1) as connection:
            rows = [dict(zip(("name", "path", "org", "fleet"), row)) for row in connection.execute(
                "select name,path,org,fleet from projects where org is not null and org != ''")]
        origin = "orchestrator DB (live)"
    except sqlite3.Error:
        try:
            rows = json.loads(Path(seed_path).read_text())["projects"]
            origin = "CCT org seed fallback"
        except (OSError, ValueError, KeyError):
            rows, origin = [], "No project source; CCT fleet/default rules only"
    namespace["_ORG_LOWER"] = {org.lower(): org for org in namespace["ORGS"]}
    namespace["AGENT_ROOT"] = str(Path("..").resolve())
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in ("canon_org", "resolve_org")]
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(app_path), "exec"), namespace)
    canon = namespace["canon_org"]
    projects = [{**row, "org": canon(row["org"])} for row in rows if canon(row["org"])]
    dbfleet = {row["fleet"]: row["org"] for row in projects if row.get("fleet")}
    namespace["org_tables"] = lambda: (projects, dbfleet, origin)
    return namespace["resolve_org"], namespace["ORGS"], origin, hashlib.sha256(source.encode()).hexdigest()[:12]


def build_membership(agents, roster, resolver):
    by_uuid = defaultdict(list)
    for row in roster:
        for identity in {row.get(key) for key in ("mapping_uuid", "v2_uuid", "cct_uuid") if row.get(key)}:
            by_uuid[identity].append(row)
    membership, sources = {}, {}
    for agent in agents:
        matches = by_uuid.get(agent["id"], [])
        # Conflicting UUID records cannot silently choose a current assignment.
        resolved = {resolver(row)[:2] for row in matches}
        if len(resolved) == 1:
            row, match = matches[0], "canonical UUID"
        else:
            row = {key: agent[key] for key in ("project", "fleet", "cwd", "tags") if key in agent}
            match = "historical metadata" if not matches else "historical metadata (conflicting roster UUID)"
        org, source, _detail = resolver(row)
        membership[agent["id"]] = org
        sources[agent["id"]] = {"source": source, "match": match}
    return membership, sources


def disk_roster(directory):
    rows = []
    for path in Path(directory).glob("*.json"):
        try:
            row = json.loads(path.read_text())
            if isinstance(row, dict):
                rows.append({**row, "mapping_uuid": row.get("uuid"), "name": row.get("tmux_name")})
        except (OSError, ValueError):
            continue
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cct-app", type=Path, default=Path("../other-project/dash/app.py"))
    parser.add_argument("--db", type=Path, default=Path("../orchestrator/data/orchestrator.sqlite"))
    parser.add_argument("--seed", type=Path, default=Path("../other-project/dash/org_seed.json"))
    parser.add_argument("--sessions-url", default="http://127.0.0.1:8765/api/sessions")
    parser.add_argument("--sessions-json", type=Path)
    parser.add_argument("--mappings", type=Path, default=Path.home() / ".claude/tmux-sessions")
    parser.add_argument("--timeline", type=Path, action="append")
    parser.add_argument("--output", type=Path, default=ROOT / "data/timeline_hierarchy.json")
    parser.add_argument("--public-output", type=Path, default=ROOT / "ui/public/data/timeline_hierarchy.json")
    args = parser.parse_args()
    resolver, orgs, origin, revision = load_resolver(args.cct_app, args.db, args.seed)
    try:
        if args.sessions_json:
            roster = json.loads(args.sessions_json.read_text())["rows"]
        else:
            with urlopen(args.sessions_url, timeout=5) as response:
                roster = json.load(response)["rows"]
        roster_source = "CCT roster"
    except (OSError, ValueError, KeyError):
        roster = disk_roster(args.mappings)
        roster_source = "CCT disk mappings (service unavailable)" if roster else "CCT unavailable; historical metadata fallback"
    timelines = args.timeline or [ROOT / "data/timeline.json", *sorted((ROOT / "data").glob("timeline.*.json"))]
    agents = {}
    for timeline in timelines:
        for agent in json.loads(timeline.read_text())["agents"]:
            agents.setdefault(agent["id"], agent)
    membership, sources = build_membership(agents.values(), roster, resolver)
    out = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "source": f"{roster_source} + {origin}", "resolverRevision": revision,
        "orgs": [{"id": org, "label": org} for org in orgs],
        "agentToOrg": membership, "agentSources": sources,
        "sourceCounts": dict(Counter(item["source"] for item in sources.values())),
    }
    for output in (args.output, args.public_output):
        # Refuse accidental writes through a shared runtime-data symlink.
        if output.is_symlink() or output.parent.is_symlink():
            raise SystemExit(f"Refusing shared symlink output: {output}")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps({"agents": len(membership), "orgs": dict(Counter(membership.values())), "sources": out["sourceCounts"], "source": out["source"]}))


if __name__ == "__main__":
    main()
