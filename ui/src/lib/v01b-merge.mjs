// v0.1.B: show hierarchically merged V0.1 bullets for a chart selection.
// Row nodes = one agent over time (1h -> 4h -> day); column nodes = all agents in one time bucket.
// A selection covering every agent active in its window uses column nodes; otherwise per-agent row nodes.

const FIELDS = [
  { k: 'started', title: 'started', cls: 'bs-started', icon: '⏵' },
  { k: 'finished', title: 'finished', cls: 'bs-finished', icon: '✓' },
  { k: 'blocked', title: 'blocked', cls: 'bs-blocked', icon: '⨯' },
  { k: 'key_decisions', title: 'key decisions', cls: 'bs-decisions', icon: '◉' },
];
const LEVELS = ['day', '4h', '1h'];
// Merge trees built by scripts/v01b/hier_merge.py; same leaves, different merge model.
// First entry is the default (CKL 2026-10-30: Qwen3-235B; 8B stays selectable for comparison).
const TREES = [{ name: 'gamejam-full-q235b', label: 'Qwen3-235B (whole gamejam)' }, { name: 'gamejam-day1', label: 'Qwen3-8B (Day1 only)' }];
const treePromises = new Map();
function chosenTree() { try { return localStorage.getItem('v01b-tree') || TREES[0].name; } catch { return TREES[0].name; } }

export function loadMergeTree(name = chosenTree()) {
  if (!treePromises.has(name)) treePromises.set(name, fetch('/api/v01b/merge-tree?name=' + encodeURIComponent(name), { cache: 'no-store' })
    .then(r => r.ok ? r.json() : null).catch(() => null)
    .then(tree => tree && indexTree(tree)));
  return treePromises.get(name);
}

function indexTree(tree) {
  const leafText = new Map();
  for (const n of Object.values(tree.nodes)) {
    if (n.level !== '1h' || n.kind !== 'row') continue;
    for (const f of FIELDS) for (const b of n.bullets[f.k]) leafText.set(b.id, { t: b.t, agent: n.agent, start: n.start });
  }
  return { ...tree, leafText };
}

// CKL: one summary per selection. Snap to the single smallest node covering [s, e); if none covers (the window
// crosses a day boundary), take the node overlapping it most, preferring the coarser level.
function snap(nodes, s, e) {
  const span = n => n.end - n.start, ov = n => Math.min(n.end, e) - Math.max(n.start, s);
  const covering = nodes.filter(n => n.start <= s && n.end >= e).sort((x, y) => span(x) - span(y));
  if (covering.length) return { node: covering[0], exact: true };
  const best = nodes.filter(n => ov(n) > 0).sort((x, y) => ov(y) - ov(x) || span(y) - span(x))[0];
  return best ? { node: best, exact: false } : null;
}

// Three supported shapes (CKL): one agent in one hour (cell), one agent over a longer span (row nodes),
// or a whole time column (all agents). A column click can miss an agent the chart hides, so >= COLUMN_SHARE
// of the active agents counts as a column and the note names who was not selected. Anything else: plain concat.
const COLUMN_SHARE = 0.85;
export function mergedView(tree, sel) {
  const all = Object.values(tree.nodes);
  const s = sel.startTs, e = sel.endTs, want = new Set(sel.sessionIds);
  const active = new Map(all.filter(n => n.kind === 'row' && n.level === '1h' && n.start < e && n.end > s).map(n => [n.sid, n.agent]));
  const chosen = [...active.keys()].filter(sid => want.has(sid));
  if (!chosen.length) return { groups: [], mode: 'empty' };
  if (chosen.length >= 2 && chosen.length >= COLUMN_SHARE * active.size) {
    const missing = [...active].filter(([sid]) => !want.has(sid)).map(([, a]) => a);
    const note = missing.length ? `Column node includes ${missing.length} active agent(s) outside the selection: ${missing.join(', ')}.` : '';
    const hit = snap(all.filter(n => n.kind === 'col'), s, e);
    return { mode: 'column', note, snapped: hit, groups: hit ? [{ label: `all ${active.size} agents`, nodes: [hit.node] }] : [] };
  }
  if (chosen.length === 1) {
    const hit = snap(all.filter(n => n.kind === 'row' && n.sid === chosen[0]), s, e);
    return { mode: hit?.node.level === '1h' ? 'cell' : 'row', snapped: hit, groups: hit ? [{ label: active.get(chosen[0]), nodes: [hit.node] }] : [] };
  }
  const groups = chosen.map(sid => ({ label: active.get(sid), nodes: all.filter(n => n.kind === 'row' && n.level === '1h' && n.sid === sid && n.start < e && n.end > s) }));
  return { mode: 'concat', note: `${chosen.length} of ${active.size} agents selected: not supported hierarchically (select one cell, one agent row, or a whole column). Showing the plain concatenation.`, groups };
}

const SHAPE = { cell: 'cell · 1h leaf', row: 'row', column: 'column · all agents' };
const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const hhmm = t => new Date(t * 1000).toISOString().slice(11, 16);
// Node window label; day-long nodes need dates or "14:00–14:00" is ambiguous.
const span = (a, b) => b - a >= 86400 ? `${new Date(a * 1000).toISOString().slice(5, 16).replace('T', ' ')} – ${new Date(b * 1000).toISOString().slice(5, 16).replace('T', ' ')}` : `${hhmm(a)}–${hhmm(b)}`;

// opts.head (optional element): the merge-model picker and a one-line info (full stats in its tooltip) go there
// instead of the body, for a compact widget header. Without it the output is unchanged.
export async function renderV01BSummary(body, sub, sel, opts = {}) {
  const head = opts.head || null;
  const pickerHtml = () => { const cur = chosenTree(); return `<select class="v01b-tree" aria-label="Merge model">${TREES.map(t => `<option value="${t.name}"${t.name === cur ? ' selected' : ''} title="${t.label}">${head ? t.label.split(' (')[0] : t.label}</option>`).join('')}</select>`; }; // header: short model name
  const setHead = (info = '', tip = '') => {
    if (!head) return;
    head.innerHTML = `${pickerHtml()}<span class="v01b-info"${tip ? ` title="${esc(tip)}"` : ''}>${esc(info)}</span>`;
    head.querySelector('.v01b-tree').addEventListener('change', ev => {
      try { localStorage.setItem('v01b-tree', ev.target.value); } catch {}
      renderV01BSummary(body, sub, sel, opts);
    });
  };
  setHead('loading…');
  body.innerHTML = '<p class="hint">loading merged summaries…</p>';
  const tree = await loadMergeTree();
  if (!tree) { setHead(); body.innerHTML = '<p class="hint">Merged (v0.1.B) summaries are not built yet.</p>'; return; }
  const view = mergedView(tree, sel);
  if (!view.groups.length) {
    setHead();
    const day = (t) => new Date(t * 1000).toISOString().slice(5, 16).replace('T', ' ');
    body.innerHTML = `<p class="hint">No merged summaries for this selection (${day(sel.startTs)}–${hhmm(sel.endTs)} UTC). The merged set covers ${day(tree.meta.start)} to ${day(tree.meta.end)} UTC (gamejam Day1).</p>`;
    if (sub) sub.textContent = ''; return;
  }
  let shown = 0, concat = 0; const sources = new Set();
  const bullet = b => {
    shown++; b.leaf_src.forEach(x => sources.add(x));
    const src = b.leaf_src.map(id => tree.leafText.get(id)).filter(Boolean)
      .map(l => `<li>${esc(l.agent)} · ${hhmm(l.start)}: ${esc(l.t)}</li>`).join('');
    return `<li>${esc(b.t)} <details class="bs-src"><summary>${b.leaf_src.length} src</summary><ul>${src}</ul></details></li>`;
  };
  const grid = byField => {
    const cols = FIELDS.filter(f => byField[f.k].length).map(f =>
      `<div class="bs-group ${f.cls}"><div class="bs-group-title">${f.icon} ${f.title} <span class="bs-group-n">${byField[f.k].length}</span></div><ul class="bs-list">${byField[f.k].map(bullet).join('')}</ul></div>`).join('');
    return cols ? `<div class="bs-grid">${cols}</div>` : '<p class="hint">quiet</p>';
  };
  const parts = [];
  let snapLabel = '', snapNote = '';
  if (view.mode === 'concat') {
    const merged = Object.fromEntries(FIELDS.map(f => [f.k, []])), seen = new Set();
    for (const g of view.groups) for (const n of g.nodes) for (const f of FIELDS) for (const b of n.bullets[f.k]) {
      concat++; const k = b.t.toLowerCase().replace(/\W+/g, ' ').trim();
      if (!seen.has(k)) { seen.add(k); merged[f.k].push(b); }
    }
    parts.push(`<p class="hint">${esc(view.note)}</p>`, grid(merged));
  } else {
    if (view.note) parts.push(`<p class="hint">${esc(view.note)}</p>`);
    const sn = view.snapped?.node;
    if (sn) {
      const same = sn.start === sel.startTs && sn.end === sel.endTs;
      snapLabel = `${sn.level} node ${span(sn.start, sn.end)} UTC`;
      snapNote = same ? '' : `snapped from your selection ${hhmm(sel.startTs)}–${hhmm(sel.endTs)} UTC${view.snapped.exact ? '' : '; it crosses a day boundary, so this is the day node it overlaps most'}`;
      if (!head) parts.push(`<p class="hint v01b-snap">showing: <b>${sn.level} node ${span(sn.start, sn.end)} UTC</b>${same ? '' : ` (${snapNote})`}</p>`);
      // Lets the chart highlight the window this summary actually covers.
      window.dispatchEvent(new CustomEvent('v01b-snap', { detail: { start: sn.start, end: sn.end, level: sn.level, sessionIds: sel.sessionIds, mode: view.mode } }));
    }
    for (const g of view.groups) for (const n of g.nodes) {
      concat += n.concat_bullets ?? FIELDS.reduce((a, f) => a + n.bullets[f.k].length, 0);
      const shape = view.mode === 'row' ? `row · ${n.level} node` : SHAPE[view.mode].replace('all agents', `all agents · ${n.level} node`);
      parts.push(`<div class="bs-node"><div class="bs-node-head">${esc(g.label)} · ${span(n.start, n.end)} UTC · <b>${esc(shape)}</b></div>${grid(Object.fromEntries(FIELDS.map(f => [f.k, n.bullets[f.k]])))}</div>`);
    }
  }
  const statsText = `${shown} ${view.mode === 'concat' ? 'unique' : 'merged'} bullets (concatenated: ${concat}) · ${sources.size} source bullets · v0.1.B ${tree.meta.model}`;
  const stats = esc(statsText);
  if (head) {
    const info = `${snapLabel || `${view.groups.length} agents concatenated`} · ${shown} bullets`;
    setHead(info, [snapLabel && `showing: ${snapLabel}`, snapNote, statsText].filter(Boolean).join('\n'));
    body.innerHTML = parts.join('');
    return;
  }
  if (sub) sub.textContent = ' · ' + stats;
  else parts.unshift(`<p class="hint">${stats}</p>`);  // the widget header no longer has a #bs-sub line
  parts.unshift(`<label class="hint">merge model ${pickerHtml()}</label>`);
  body.innerHTML = parts.join('');
  body.querySelector('.v01b-tree')?.addEventListener('change', ev => {
    try { localStorage.setItem('v01b-tree', ev.target.value); } catch {}
    renderV01BSummary(body, sub, sel);
  });
}
