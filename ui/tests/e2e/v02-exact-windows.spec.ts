import { test, expect } from "@playwright/test";
import type { Page } from "@playwright/test";
import { goTimeline } from "./_helpers";

// Exact per-session selection windows (docs/v02/selection-contract.md).
// Read-only: only clicks/keys, never writes data or reviews.

const URL = "/timeline?fleet=gamejam&range=all&bucket=86400";

type Win = { sid: string; start: number; end: number };

async function ready(page: Page) {
  await goTimeline(page, URL);
  await expect
    .poll(() => page.evaluate(() => document.querySelectorAll("#tl-chart rect.col-header-hit").length), { timeout: 60_000 })
    .toBeGreaterThan(3);
  await expect
    .poll(() => page.evaluate(() => document.querySelectorAll("#comms-body svg g[data-node]").length), { timeout: 60_000 })
    .toBeGreaterThan(2);
  await expect
    .poll(() => page.evaluate(() => !!(window as any)._transcript), { timeout: 60_000 })
    .toBe(true);
}

// Pick three adjacent columns A,B,C where B is the busiest; returns their [start,end).
async function busyTriple(page: Page) {
  return page.evaluate(() => {
    const heads = Array.from(document.querySelectorAll<SVGRectElement>("#tl-chart rect.col-header-hit"))
      .map(h => ({ ts: +h.getAttribute("data-col-ts")!, end: +h.getAttribute("data-col-end")!, x: +h.getAttribute("x")!, w: +h.getAttribute("width")! }))
      .sort((a, b) => a.ts - b.ts);
    const cells = Array.from(document.querySelectorAll<SVGRectElement>("#tl-chart rect.seg[data-cell-i]:not(.state-idle)"))
      .map(r => +r.getAttribute("x")!);
    const load = heads.map(h => cells.filter(x => x >= h.x - 0.5 && x < h.x + h.w - 0.5).length);
    let best = 1;
    for (let i = 1; i < heads.length - 1; i++) if (load[i] > load[best]) best = i;
    return { a: heads[best - 1], b: heads[best], c: heads[best + 1] };
  });
}

async function clickCol(page: Page, ts: number, shift = false) {
  await page.evaluate(({ ts, shift }) => {
    const h = document.querySelector(`#tl-chart rect.col-header-hit[data-col-ts="${ts}"]`)!;
    h.dispatchEvent(new MouseEvent("click", { bubbles: true, shiftKey: shift }));
  }, { ts, shift });
  await page.waitForTimeout(1500);
}

const lastScope = (page: Page) => page.evaluate(() => {
  const s = (window as any)._selectionBus?.lastScope;
  return s ? { windows: s.windows as Win[] | null, agentIds: s.agentIds ? [...s.agentIds] : null, sticky: s.stickyElementKey ?? null, source: s.source } : null;
});

const summaryTs = (page: Page) => page.evaluate(() =>
  Array.from(document.querySelectorAll("#transcript-body [data-ts]")).map(e => +e.getAttribute("data-ts")!));

test("disjoint column selection excludes the gap in the chat transcript", async ({ page }) => {
  await ready(page);
  const { a, b, c } = await busyTriple(page);
  expect(a && b && c, "need three adjacent day columns").toBeTruthy();

  // Prove the gap column has traffic, otherwise the exclusion check is vacuous.
  await clickCol(page, b.ts);
  expect((await summaryTs(page)).length, "gap column must have transcript rows").toBeGreaterThan(0);

  await clickCol(page, a.ts);
  await clickCol(page, c.ts, true);
  const scope = await lastScope(page);
  expect(scope?.windows?.length, "disjoint selection must publish exact windows").toBeGreaterThan(0);
  for (const w of scope!.windows!) {
    expect(w.end <= b.ts || w.start >= b.end, `window ${JSON.stringify(w)} overlaps gap`).toBe(true);
  }

  const inGap = (t: number) => t >= b.ts && t < b.end;
  const sum = await summaryTs(page);
  expect(sum.length, "summary transcript should show A∪C rows").toBeGreaterThan(0);
  expect(sum.filter(inGap), "summary transcript leaked gap rows").toEqual([]);

  // Human pin keeps legacy sender semantics and does not publish a new scope.
  const pinned = await page.evaluate(() => {
    const n = document.querySelector('#comms-body svg g[data-node="human"]');
    n?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    return !!n;
  });
  test.skip(!pinned, "no human node in this selection");
  await page.waitForTimeout(1800);
  expect((await lastScope(page))?.sticky).not.toBe("human");
  // The single chat transcript (A2A no longer has its own pane) still excludes the gap.
  const after = await summaryTs(page);
  expect(after.filter(inGap), "transcript leaked gap rows after the A2A click").toEqual([]);
  expect(after.every(t => (t >= a.ts && t < a.end) || (t >= c.ts && t < c.end)), "transcript rows outside A∪C").toBe(true);
});

test("session pin intersects windows; unpin restores exact pre-pin windows; no-pin empty click is a no-op", async ({ page }) => {
  await ready(page);
  const { b } = await busyTriple(page);
  await clickCol(page, b.ts);
  const before = await lastScope(page);
  expect(before?.windows?.length).toBeGreaterThan(1);
  const norm = (ws: Win[] | null) => JSON.stringify((ws || []).map(w => [w.sid, w.start, w.end]).sort());

  // Ceiling: empty click with no pin must not change the bus scope.
  const clickEmpty = () => page.evaluate(() => {
    const svg = document.querySelector("#comms-body svg") as SVGSVGElement;
    const r = svg.getBoundingClientRect();
    svg.dispatchEvent(new MouseEvent("click", { bubbles: true, clientX: r.left + 3, clientY: r.top + 3 }));
  });
  await clickEmpty();
  await page.waitForTimeout(800);
  expect(norm((await lastScope(page))!.windows)).toBe(norm(before!.windows));

  // Pin a real-agent node whose session is inside the selection.
  const selectedSids = before!.windows!.map(w => w.sid);
  const pin = await page.evaluate((sids: string[]) => {
    const agents = (window as any).timelinePageData?.data?.agents || [];
    const titleBySid = new Map(agents.map((a: any) => [a.id, a.title]));
    const titles = new Set(sids.map(s => titleBySid.get(s)).filter(Boolean));
    const node = Array.from(document.querySelectorAll("#comms-body svg g[data-node]"))
      .find(g => titles.has(g.getAttribute("data-node")!));
    if (!node) return null;
    node.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    return node.getAttribute("data-node");
  }, selectedSids);
  test.skip(!pin, "no selected agent appears as an A2A node");
  await page.waitForTimeout(1500);
  const pinned = await lastScope(page);
  expect(pinned?.sticky).toBe(pin);
  expect(pinned?.windows?.length, "pin must keep exact windows").toBeGreaterThan(0);
  for (const w of pinned!.windows!) expect(pinned!.agentIds).toContain(w.sid);
  for (const w of pinned!.windows!) expect(norm(before!.windows)).toContain(JSON.stringify([w.sid, w.start, w.end]));

  // Unpin via empty click → exact pre-pin windows come back.
  await clickEmpty();
  await page.waitForTimeout(1500);
  const restored = await lastScope(page);
  expect(restored?.sticky ?? null).not.toBe(pin);
  expect(norm(restored!.windows)).toBe(norm(before!.windows));
});

test("pinning a session outside the selection never widens: the selection scope is unchanged", async ({ page }) => {
  await ready(page);
  // A2A only draws in-scope nodes plus peers that messaged them in-window.
  // Search single cells (one session each) until a drawn node is a fleet agent
  // whose sessions are all outside the selection, then pin it.
  const pin = await page.evaluate(async () => {
    const agents = (window as any).timelinePageData?.data?.agents || [];
    const sidsByTitle = new Map<string, string[]>();
    for (const a of agents) sidsByTitle.set(a.title, [...(sidsByTitle.get(a.title) || []), a.id]);
    // Prefer cells of agents already drawn in the fleet graph: they have peers.
    const drawn = new Set(Array.from(document.querySelectorAll("#comms-body svg g[data-node]")).map(g => g.getAttribute("data-node")));
    const titleOf = (r: Element) => (r.querySelector("title")?.textContent || "").split(" · ")[0];
    const cells = Array.from(document.querySelectorAll<SVGRectElement>("#tl-chart rect.seg[data-cell-i]:not(.state-idle)"))
      .filter(r => drawn.has(titleOf(r)));
    for (const cell of cells.slice(0, 60)) {
      cell.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await new Promise(r => setTimeout(r, 700));
      const wins = (window as any)._selectionBus?.lastScope?.windows || [];
      const inSet = new Set(wins.map((w: any) => w.sid));
      const node = Array.from(document.querySelectorAll("#comms-body svg g[data-node]")).find(g => {
        const sids = sidsByTitle.get(g.getAttribute("data-node")!);
        return sids && sids.length && sids.every(s => !inSet.has(s));
      });
      if (node && wins.length) {
        (window as any).__prePinScope = (window as any)._selectionBus?.lastScope;
        node.dispatchEvent(new MouseEvent("click", { bubbles: true }));
        return node.getAttribute("data-node");
      }
    }
    return null;
  });
  test.skip(!pin, "no outside-selection agent node is visible in A2A");
  await page.waitForTimeout(1500);

  // A2A picks may only narrow (CKL): an outside pin highlights locally and publishes nothing.
  const same = await page.evaluate(() => (window as any)._selectionBus?.lastScope === (window as any).__prePinScope);
  expect(same, "outside pin must not publish a new scope").toBe(true);
});
