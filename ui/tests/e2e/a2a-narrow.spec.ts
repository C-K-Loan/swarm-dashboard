import { test, expect } from "@playwright/test";
import { waitForComms, goTimeline } from "./_helpers";

// A2A picks may only NARROW the shared selection: a pin inside the current
// selection publishes a subset; a pin outside it never widens; a src→dst pair
// narrows every widget to its two agents and breaking it restores the scope.

const busScope = (page: any) =>
  page.evaluate(() => {
    const s = (window as any)._selectionBus?.lastScope;
    return s ? { source: s.source, sticky: s.stickyElementKey ?? null, ids: s.agentIds ? [...s.agentIds] : null, titles: s.titles ? [...s.titles] : null } : null;
  });

test("node pin narrows within the selection and never widens", async ({ page }) => {
  await goTimeline(page, "/timeline?fleet=gamejam&range=7d&bucket=3600");
  await expect.poll(() => page.evaluate(() => !!(window as any).__firstColApplied), { timeout: 120_000 }).toBe(true);
  await waitForComms(page, 1);
  await page.waitForTimeout(2500);
  const ceiling = await busScope(page);
  expect(ceiling?.ids?.length, "first-column default must scope agents").toBeGreaterThan(0);

  const picks = await page.evaluate((ids: string[]) => {
    const agents = (window as any).timelinePageData?.data?.agents || [];
    const inside = new Set(agents.filter((a: any) => ids.includes(a.id)).map((a: any) => a.title));
    const real = new Set(agents.map((a: any) => a.title));
    const names = [...document.querySelectorAll("#comms-body svg g[data-node]")].map((g) => g.getAttribute("data-node")!);
    return { inside: names.find((n) => inside.has(n)) || null, outside: names.find((n) => real.has(n) && !inside.has(n)) || null };
  }, ceiling!.ids!);
  expect(picks.inside, "a node of the selected agents must be drawn").not.toBeNull();

  const click = (name: string) => page.evaluate((n) => document.querySelector(`#comms-body svg g[data-node="${CSS.escape(n)}"]`)!.dispatchEvent(new MouseEvent("click", { bubbles: true })), name);
  await click(picks.inside!);
  await page.waitForTimeout(1500);
  const pinned = await busScope(page);
  expect(pinned!.source).toBe("comms");
  expect(pinned!.sticky).toBe(picks.inside);
  for (const id of pinned!.ids!) expect(ceiling!.ids).toContain(id);
  // The agent column follows the narrowed scope.
  const rows = await page.evaluate(() => document.querySelectorAll("#tl-sticky .sel-hl, #tl-pin-label .sel-hl").length / 2); // tint + accent bar per row (pinned master row included)
  expect(rows).toBeGreaterThan(0);
  expect(rows).toBeLessThanOrEqual(pinned!.ids!.length);

  if (picks.outside) {
    await click(picks.outside);
    await page.waitForTimeout(1500);
    const after = await busScope(page);
    for (const id of after!.ids || []) expect(ceiling!.ids).toContain(id);
  }
});

test("src→dst pair narrows to the two agents; breaking it restores the scope", async ({ page }) => {
  await goTimeline(page, "/timeline?fleet=gamejam&range=7d&bucket=3600&select=none");
  await waitForComms(page, 3);
  await page.waitForTimeout(2500);
  const pair = await page.evaluate(() => {
    const real = new Set(((window as any).timelinePageData?.data?.agents || []).map((a: any) => a.title));
    return [...document.querySelectorAll("#comms-body svg g[data-node]")].map((g) => g.getAttribute("data-node")!).filter((n) => n !== "human" && real.has(n)).slice(0, 2);
  });
  expect(pair.length).toBe(2);
  const rclick = (name: string) => page.evaluate((n) => document.querySelector(`#comms-body svg g[data-node="${CSS.escape(n)}"]`)!.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, cancelable: true })), name);
  await rclick(pair[0]);
  await page.waitForTimeout(800);
  await rclick(pair[1]);
  await page.waitForTimeout(1500);
  const scoped = await busScope(page);
  expect(scoped!.sticky).toBe(`pair:${pair[0]}>${pair[1]}`);
  expect(new Set(scoped!.titles)).toEqual(new Set(pair));
  await rclick(pair[0]);
  await page.waitForTimeout(1500);
  const restored = await busScope(page);
  expect(restored!.sticky).not.toContain("pair:");
  expect(restored!.titles?.length || 0).toBeGreaterThan(2);
});

test("channel row pick filters the chat transcript to that pair; a chart pick clears it", async ({ page }) => {
  await goTimeline(page, "/timeline?fleet=gamejam&range=7d&bucket=3600");
  await expect.poll(() => page.evaluate(() => !!(window as any).__firstColApplied), { timeout: 120_000 }).toBe(true);
  await waitForComms(page, 1);
  await page.waitForTimeout(2500);
  const ch = await page.evaluate(() => {
    const tr = document.querySelector<HTMLElement>("#comms-body tr.comms-ch-row");
    tr?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    return tr ? { from: tr.dataset.from!, to: tr.dataset.to! } : null;
  });
  test.skip(!ch, "no A2A channel in the first column");
  await page.waitForTimeout(2500);
  const tx = () => page.evaluate(() => {
    const b: any = document.getElementById("transcript-body");
    const titleOf = new Map(((window as any).timelinePageData.data.agents as any[]).map((a) => [a.id, a.title]));
    return { channel: (window as any)._selectionBus.lastScope?.channel ?? null, sub: document.getElementById("tx-sub")?.textContent || "", titles: [...new Set((b._rows || []).map((r: any) => titleOf.get(r.sid)))] };
  });
  const picked = await tx();
  expect(picked.channel).toEqual(ch);
  expect(picked.sub).toContain(`${ch!.from} ↔ ${ch!.to}`);
  for (const t of picked.titles) expect([ch!.from, ch!.to]).toContain(t);
  expect(await page.evaluate(() => !!document.getElementById("comms-tr-body")), "A2A has no transcript pane").toBe(false);
  // A chart cell pick replaces the channel scope.
  await page.evaluate(() => document.querySelector("#tl-chart rect.seg[data-agent-i][data-cell-i]:not(.state-idle)")!.dispatchEvent(new MouseEvent("click", { bubbles: true })));
  await page.waitForTimeout(2500);
  expect((await tx()).sub).not.toContain("↔");
});
