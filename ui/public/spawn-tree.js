/* Spawn forest layout lifted from mock-hierarchy.astro. Keep the prototype palette,
 * hyphen wrapping, measured columns and cubic parent→child edges identical. */
(function (root) {
  function wrapName(name, max = 22) {
    if (name.length <= max) return [name];
    const target = Math.floor(name.length / 2), idxs = [];
    for (let i = 0; i < name.length; i++) if (name[i] === '-') idxs.push(i);
    if (!idxs.length) return [name.slice(0, max), name.slice(max)];
    idxs.sort((a, b) => Math.abs(a - target) - Math.abs(b - target));
    return [name.slice(0, idxs[0]), name.slice(idxs[0] + 1)];
  }
  function layoutTree(forest, rowH = 36, startY = 20) {
    const positions = [], edges = [], widthByDepth = {}, xByDepth = {};
    function measure(node, depth) {
      widthByDepth[depth] = Math.max(widthByDepth[depth] || 0, ...wrapName(node.name).map(l => l.length * 6));
      node.kids.forEach(k => measure(k, depth + 1));
    }
    forest.forEach(r => measure(r, 0));
    let x = 40, y = startY;
    Object.keys(widthByDepth).map(Number).sort((a, b) => a - b).forEach(d => {
      xByDepth[d] = x; x += widthByDepth[d] + 24 + 20;
    });
    function walk(node, depth) {
      const kidYs = node.kids.map(k => walk(k, depth + 1));
      const ny = kidYs.length ? (kidYs[0] + kidYs[kidYs.length - 1]) / 2 : y;
      if (!kidYs.length) y += rowH;
      positions.push({ x: xByDepth[depth], y: ny, name: node.name, depth, lines: wrapName(node.name) });
      node.kids.forEach(k => edges.push([node.name, k.name]));
      return ny;
    }
    forest.forEach(r => walk(r, 0));
    return { positions, edges, totalY: y, widthByDepth, xByDepth, totalX: x };
  }
  function childrenOf(parents) {
    const children = new Map();
    for (const [child, parent] of Object.entries(parents)) {
      if (typeof parent !== 'string' || !parent || child === parent) continue;
      if (!children.has(parent)) children.set(parent, []);
      children.get(parent).push(child);
    }
    for (const kids of children.values()) kids.sort();
    return children;
  }
  function selectSpawnTree(agents, filtered, sourceParents, scope, grouped = false) {
    const parents = Object.assign(Object.create(null), sourceParents || {});
    for (const a of agents) {
      if (a.source === 'cct-live' && a.title && a.fleet && a.fleet !== a.title && !Object.hasOwn(parents, a.title)) {
        parents[a.title] = a.fleet;
      }
    }
    const selected = filtered.filter(a => !scope || (scope.agentIds
      ? scope.agentIds.has(a.id) : !scope.titles || scope.titles.has(a.title)));
    const names = new Set(selected.map(a => a.title || a.id));
    const children = childrenOf(parents), keep = new Set(names);
    // A single explicit agent selection includes its whole subtree and ancestor chain.
    const single = !grouped && names.size === 1 && (scope?.agentIds || scope?.titles);
    if (single) {
      const pending = [...names];
      while (pending.length) for (const child of children.get(pending.pop()) || []) {
        if (!keep.has(child)) { keep.add(child); pending.push(child); }
      }
    }
    // Fleet/org forests retain direct parents as context, without importing siblings.
    for (const name of names) {
      let node = name;
      const seen = new Set([node]);
      while (parents[node] && !seen.has(parents[node])) {
        node = parents[node]; seen.add(node); keep.add(node);
        if (!single) break;
      }
    }
    const scopedParents = Object.create(null);
    for (const name of keep) scopedParents[name] = keep.has(parents[name]) ? parents[name] : null;
    const roots = [...keep].filter(n => !scopedParents[n]);
    // Malformed cyclic components still render once, with a deterministic root.
    const covered = new Set();
    function cover(start) {
      const todo = [start]; let count = 0;
      while (todo.length) {
        const n = todo.pop();
        if (covered.has(n) || !keep.has(n)) continue;
        covered.add(n); count++; todo.push(...(children.get(n) || []));
      }
      return count;
    }
    const sizes = new Map(roots.map(n => [n, cover(n)]));
    for (const n of keep) if (!covered.has(n)) { roots.push(n); sizes.set(n, cover(n)); scopedParents[n] = null; }
    roots.sort((a, b) => sizes.get(b) - sizes.get(a) || a.localeCompare(b));
    return { roots, parents: scopedParents };
  }
  function renderSpawnTree(svgContainer, roots, parents) {
    const children = childrenOf(parents), seen = new Set(), ordered = [];
    const stack = [...roots].reverse().map(name => ({ name, parent: null }));
    while (stack.length) {
      const entry = stack.pop();
      if (seen.has(entry.name)) continue;
      seen.add(entry.name); ordered.push(entry);
      const kids = children.get(entry.name) || [];
      for (let i = kids.length - 1; i >= 0; i--) stack.push({ name: kids[i], parent: entry.name });
    }
    const forest = [], nodes = new Map();
    for (const entry of ordered.slice(0, 40)) {
      const node = { name: entry.name, kids: [] };
      nodes.set(entry.name, node);
      if (nodes.has(entry.parent)) nodes.get(entry.parent).kids.push(node); else forest.push(node);
    }
    const omitted = Math.max(0, ordered.length - nodes.size);
    svgContainer.replaceChildren();
    if (!forest.length) {
      const empty = document.createElement('p'); empty.className = 'hint';
      empty.textContent = 'No agents in this selection. Broaden the filters to see spawn relationships.';
      svgContainer.append(empty);
      return { count: 0, omitted: 0 };
    }
    const layout = layoutTree(forest), ns = 'http://www.w3.org/2000/svg';
    function el(tag, attrs, text) {
      const node = document.createElementNS(ns, tag);
      for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
      if (text !== undefined) node.textContent = text;
      return node;
    }
    const svg = el('svg', { viewBox: `0 0 ${Math.max(160, layout.totalX)} ${layout.totalY + 20 + (omitted ? 24 : 0)}`, preserveAspectRatio: 'xMinYMin meet', role: 'img', 'aria-label': `Spawn tree: ${nodes.size} nodes${omitted ? `, ${omitted} more omitted` : ''}` });
    svg.style.cssText = `width:100%;min-width:${layout.totalX}px;background:#0d0d14;border-radius:4px;display:block`;
    const positions = new Map(layout.positions.map(p => [p.name, p]));
    for (const [parent, child] of layout.edges) {
      const a = positions.get(parent), b = positions.get(child);
      const aEnd = a.x + layout.widthByDepth[a.depth] + 10, mx = aEnd + (b.x - aEnd) * .5;
      svg.append(el('path', { d: `M${aEnd},${a.y} C${mx},${a.y} ${mx},${b.y} ${b.x - 8},${b.y}`, fill: 'none', stroke: '#4a4a5e', 'stroke-width': 1.2 }));
    }
    for (const p of layout.positions) {
      const g = el('g', { 'data-spawn-node': p.name });
      g.append(el('title', {}, p.name));
      g.append(el('circle', { cx: p.x - 10, cy: p.y, r: 4, fill: p.depth === 0 ? '#a855f7' : p.depth === 1 ? '#5bb6ff' : '#ec4899' }));
      p.lines.forEach((line, i) => g.append(el('text', { x: p.x, y: p.y + 3 + (i - (p.lines.length - 1) / 2) * 12, 'font-family': 'SF Mono,Menlo,monospace', 'font-size': 10, fill: '#c0c0d0' }, line)));
      svg.append(g);
    }
    if (omitted) svg.append(el('text', { x: 40, y: layout.totalY + 12, 'font-family': 'SF Mono,Menlo,monospace', 'font-size': 10, fill: '#c0c0d0' }, `+${omitted} more · narrow the selection`));
    svgContainer.append(svg);
    return { count: nodes.size, omitted };
  }
  root.SpawnTree = { wrapName, layoutTree, selectSpawnTree, renderSpawnTree };
})(globalThis);
