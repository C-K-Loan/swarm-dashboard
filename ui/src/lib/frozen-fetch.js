// Static demo build only (PUBLIC_FROZEN=1): answers the dashboard's /api/* calls from pre-built files.
// Runs inline in <head> before any page script, so every fetch on the page goes through it.
(function () {
  var F = window.__FROZEN__;
  if (!F || window.__frozenFetch) return;
  var orig = window.fetch.bind(window), base = F.base || "/";
  // Static snapshot: hide the live/loading status badges (nothing here is live).
  var st = document.createElement("style"); st.textContent = "#sum-status,#eval-status{display:none}"; document.head.appendChild(st);
  var files = new Map(), runs = new Map();
  var json = function (obj, status) { return new Response(JSON.stringify(obj), { status: status || 200, headers: { "Content-Type": "application/json" } }); };
  var missing = function () { return json({ error: "Not part of the static demo." }, 404); };
  var getJSON = function (u) {
    if (!files.has(u)) files.set(u, orig(base + u).then(function (r) { if (!r.ok) throw new Error(String(r.status)); return r.json(); }));
    return files.get(u);
  };
  var file = function (u) { return getJSON(u).then(function (d) { return json(d); }, missing); };
  // Inverse of scripts/freeze/freeze_data.py diff(): {v} replace, {a} append, {o,x} per-key patch.
  var apply = function (prev, p) {
    if (!p) return prev;
    if ("v" in p) return p.v;
    if ("a" in p) return (prev || []).concat(p.a);
    var out = Object.assign({}, prev || {});
    (p.x || []).forEach(function (k) { delete out[k]; });
    Object.keys(p.o || {}).forEach(function (k) { out[k] = apply(out[k], p.o[k]); });
    return out;
  };
  var run = function (name) {
    if (!runs.has(name)) runs.set(name, getJSON("frozen/v02/" + name + "/selection.json").then(function (d) {
      var sa = null, tn = null, byId = new Map();
      d.states.forEach(function (s) {
        sa = apply(sa, s._state); tn = apply(tn, s._names);
        var t = Object.assign({}, s); delete t._state; delete t._names;
        t.state_after = sa; t.task_names = tn; byId.set(s.id, t);
      });
      return { manifest: d.manifest, status: d.status, dataset: d.dataset, turns: d.turns, byId: byId };
    }));
    return runs.get(name);
  };
  // Same filter as src/pages/api/v02/selection.ts: turns ending inside a window, half-open.
  var selection = function (body) {
    var name = (body && body.dataset) || F.defaultRun;
    if (F.runs.indexOf(name) < 0) return Promise.resolve(json({ error: "This run is not part of the static demo." }, 503));
    var bySid = new Map();
    ((body && body.windows) || []).forEach(function (w) { if (w && typeof w.sid === "string" && isFinite(w.start) && isFinite(w.end)) bySid.set(w.sid, (bySid.get(w.sid) || []).concat([w])); });
    return run(name).then(function (d) {
      var turns = d.turns.filter(function (t) { return (bySid.get(t.session_id) || []).some(function (w) { return t.end_ts >= w.start && t.end_ts < w.end; }); })
        .map(function (t) { return (t.has_prediction && d.byId.get(t.id)) || t; });
      return json({ manifest: d.manifest, status: d.status, dataset: d.dataset, turns: turns });
    }, function () { return json({ error: "Dataset unavailable." }, 503); });
  };
  window.fetch = function (input, init) {
    var raw = typeof input === "string" ? input : input instanceof URL ? input.href : input && input.url;
    var u; try { u = new URL(raw, location.href); } catch (e) { return orig(input, init); }
    if (u.origin !== location.origin) return orig(input, init);
    var p = u.pathname.replace(/^\/+/, "");
    if (p.indexOf("data/") === 0) return orig(base + p, init);
    if (p.indexOf("api/") !== 0) return orig(input, init);
    if (p === "api/timeline") return file("data/timeline.json");
    if (p === "api/transcript") return file("data/transcript.json");
    if (p === "api/artifacts") return file("data/artifacts.json");
    if (p === "api/v02/runs") return orig(base + "api/v02/runs"); // prerendered by the static build
    if (p === "api/v02/eval-summary") return file("frozen/v02/eval-summary.json");
    if (p === "api/v01b/merge-tree") return file("frozen/v01b/" + (u.searchParams.get("name") || "gamejam-day1").replace(/[^a-z0-9_-]/gi, "") + ".json");
    if (p === "api/v02/selection") { var b = {}; try { b = JSON.parse((init && init.body) || "{}"); } catch (e) {} return selection(b); }
    return Promise.resolve(missing());
  };
  window.__frozenFetch = true;
})();
