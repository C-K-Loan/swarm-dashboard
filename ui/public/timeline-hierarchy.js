/* Shared, dependency-free hierarchy logic. Groups never enter the selection bus. */
(function (root) {
  const members = row => row.members || [row];
  function buildRows(agents, level, config = {}) {
    if (level === "agent") return agents;
    const groups = new Map();
    const labels = new Map((config.orgs || []).map(org => [org.id, org.label || org.id]));
    for (const agent of agents) {
      // An explicit session assignment overrides its fleet; loose agents use this too.
      const org = config.agentToOrg?.[agent.id] || config.fleetToOrg?.[agent.fleet];
      const key = level === "fleet" ? (agent.fleet || "") : (org || "");
      if (!groups.has(key)) groups.set(key, new Map());
      groups.get(key).set(agent.id, agent);
    }
    return [...groups].map(([key, unique]) => {
      const agents = [...unique.values()];
      return {
        id: `group:${level}:${key}`, title: key ? (level === "org" ? labels.get(key) || key : key) : `Unassigned ${level}`,
        project: level === "fleet" ? key : "", fleet: level === "fleet" ? key : "",
        level, members: agents, segments: agents.flatMap(a => a.segments),
        total_turns: agents.reduce((n, a) => n + (a.total_turns || 0), 0),
        total_cost_usd: agents.reduce((n, a) => n + (a.total_cost_usd || 0), 0),
      };
    });
  }
  function scopeForRows(rows, timeRange = null) {
    const agents = rows.flatMap(members);
    return { agentIds: new Set(agents.map(a => a.id)), titles: new Set(agents.map(a => a.title).filter(Boolean)), timeRange };
  }
  function rawGroupCells(row, bucketize) {
    const starts = [...new Set(row.segments.map(s => s.start_ts))].sort((a, b) => a - b);
    const maxEnd = row.segments.reduce((n, s) => Math.max(n, s.end_ts || s.start_ts + 1), 0);
    return starts.map((start, i) => {
      const end = starts[i + 1] || Math.max(start + 1, maxEnd);
      const cell = bucketize(row, start, end, end - start)[0];
      return { ...cell, duration_sec: end - start };
    });
  }
  function matches(agent, scope) {
    if (scope?.agentIds) return scope.agentIds.has(agent.id);
    return !scope?.titles || scope.titles.has(agent.title);
  }
  function commsInWindow(transcript, agents, [lo, hi], scope = null) {
    const pairs = new Map(), volume = new Map();
    for (const agent of agents.filter(a => matches(a, scope))) for (const msg of transcript[agent.id] || []) {
      if (!msg.a2a || msg.t < lo || msg.t >= hi) continue;
      const text = msg.x || msg.m || "";
      const from = typeof msg.a2a === "string" ? msg.a2a.trim() : /^\s*\[from\s+([^\]]+)\]/i.exec(text)?.[1]?.trim();
      const to = agent.title || agent.id.slice(0, 8);
      if (!from || from === to) continue;
      const key = JSON.stringify([from, to]);
      const edge = pairs.get(key) || [from, to, 0, 0];
      edge[2]++; edge[3] += text.length;
      pairs.set(key, edge);
      for (const name of [from, to]) volume.set(name, (volume.get(name) || 0) + 1);
    }
    return { edges: [...pairs.values()], nodes: [...volume].map(([n, vol]) => ({ n, vol })) };
  }
  const summaryFields = ["started", "finished", "blocked", "key_decisions"];
  const normalizedBullet = text => String(text).normalize("NFKC").toLowerCase().replace(/\s+/g, " ").trim();
  function collectSummaries(index, sessionIds, startTs, endTs, grain = 3600) {
    if (!index) return [];
    const rows = [];
    for (const sid of new Set(sessionIds)) {
      for (let hb = Math.floor(startTs / grain) * grain; hb < endTs; hb += grain) {
        if (index[`${sid}|${hb}`]) rows.push({ ...index[`${sid}|${hb}`], _sid: sid, _hb: hb });
      }
    }
    return rows.sort((a, b) => a._hb - b._hb || String(a._sid).localeCompare(String(b._sid)));
  }
  function mergeBullets(rows, field) {
    const seen = new Map();
    for (const row of rows) for (const text of row.summary?.[field] || []) {
      const key = normalizedBullet(text);
      if (!key) continue;
      const bullet = seen.get(key) || { text, ts: row._hb, sources: [] };
      if (!bullet.sources.some(source => source.sid === row._sid && source.ts === row._hb)) {
        bullet.sources.push({ sid: row._sid, ts: row._hb });
      }
      seen.set(key, bullet);
    }
    return [...seen.values()];
  }
  function summaryRollup(index, sessionIds, startTs, endTs) {
    const rows = collectSummaries(index, sessionIds, startTs, endTs);
    const fields = Object.fromEntries(summaryFields.map(field => [field, mergeBullets(rows, field)]));
    const rawBullets = rows.reduce((n, row) => n + summaryFields.reduce((sum, field) => sum + (row.summary?.[field]?.length || 0), 0), 0);
    const uniqueBullets = summaryFields.reduce((n, field) => n + fields[field].length, 0);
    return { rows, fields, rawBullets, uniqueBullets, duplicates: rawBullets - uniqueBullets, coveredAgents: new Set(rows.map(row => row._sid)).size };
  }
  // Expand a group cell to real members, preserving even members with no classified
  // segment in the interval: transcript/artifact/summary indexes may still have evidence.
  function expandPicked(picked, bucketize, scope = null) {
    const out = new Map();
    for (const { a, cell } of picked) for (const agent of members(a)) {
      if (!matches(agent, scope)) continue;
      const end = cell.end_ts || cell.start_ts + 60;
      const key = `${agent.id}|${cell.start_ts}|${end}`;
      if (out.has(key)) continue;
      if (!a.members) { out.set(key, { a: agent, cell }); continue; }
      const actual = bucketize(agent, cell.start_ts, end, end - cell.start_ts)[0];
      out.set(key, { a: agent, cell: { ...actual, _bucket: cell._bucket } });
    }
    return [...out.values()];
  }
  root.TimelineHierarchy = { members, buildRows, scopeForRows, matches, expandPicked, rawGroupCells, commsInWindow, collectSummaries, mergeBullets, summaryRollup, normalizedBullet };
})(typeof window === "undefined" ? globalThis : window);
