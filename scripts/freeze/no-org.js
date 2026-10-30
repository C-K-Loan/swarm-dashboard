// Static demo only (appended to the frozen fetch shim by build.sh): no org concept, one fleet (gamejam).
(function () {
  if (!window.__FROZEN__ || window.__frozenNoOrg) return;
  window.__frozenNoOrg = true;
  var strip = function (url) {
    try { var u = new URL(url, location.href); if (!u.searchParams.has("org")) return url; u.searchParams.delete("org"); return u.pathname + u.search + u.hash; } catch (e) { return url; }
  };
  // ?org= never reaches page scripts, and later URL updates cannot add it back.
  if (new URLSearchParams(location.search).has("org")) history.replaceState(history.state, "", strip(location.href));
  ["pushState", "replaceState"].forEach(function (k) {
    var f = history[k].bind(history);
    history[k] = function (s, t, url) { return f(s, t, url == null ? url : strip(String(url))); };
  });
  // Links: drop org before the base-path click handler reads the href.
  document.addEventListener("click", function (e) {
    var a = e.target && e.target.closest && e.target.closest("a[href]");
    if (a && /[?&]org=/.test(a.getAttribute("href"))) a.setAttribute("href", strip(a.getAttribute("href")));
  }, true);
  var st = document.createElement("style");
  st.textContent = "#org-combo{display:none!important}";
  document.head.appendChild(st);
  // Timeline rows: agent / fleet only.
  document.addEventListener("DOMContentLoaded", function () {
    var sel = document.getElementById("row-level");
    var o = sel && sel.querySelector('option[value="org"]');
    if (!o) return;
    if (sel.value === "org") { sel.value = "fleet"; sel.dispatchEvent(new Event("change", { bubbles: true })); }
    o.remove();
  });
})();
