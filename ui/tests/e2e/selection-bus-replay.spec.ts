import { test, expect } from "@playwright/test";
import { goTimeline } from "./_helpers";

// Direct test of the viz bus late-subscriber replay.
// Regression: before v16 the bus `publish()` only called current subscribers.
// Widgets that initialised AFTER the first fleetchange/orgchange publish never
// saw the scope. The replay guard was added so late subscribers catch up.

test("subscribe() replays lastScope to late subscribers", async ({ page }) => {
  await goTimeline(page, "/timeline?fleet=gamejam&range=7d&bucket=3600");
  // Wait for the bus to exist (set near the top of timeline.astro).
  await expect
    .poll(() => page.evaluate(() => !!(window as any)._selectionBus?.subscribe), {
      timeout: 15_000,
    })
    .toBe(true);
  // Wait for an initial publish from the fleet dropdown handler.
  await expect
    .poll(() => page.evaluate(() => !!(window as any)._selectionBus?.lastScope), {
      timeout: 15_000,
    })
    .toBe(true);

  // Register a late subscriber AFTER the first publish and verify it fires
  // immediately with the stored scope.
  const result = await page.evaluate(() => {
    return new Promise((resolve) => {
      let fired: any = null;
      const bus = (window as any)._selectionBus;
      bus.subscribe((scope: any) => {
        fired = {
          source: scope?.source,
          titleCount: scope?.titles?.size ?? null,
          sticky: scope?.stickyElementKey ?? null,
        };
      });
      // The replay runs synchronously inside subscribe(), so `fired` is set
      // by the time this resolves on the next microtask.
      queueMicrotask(() => resolve(fired));
    });
  });
  expect(result, "late subscriber must receive a replayed scope").not.toBeNull();
  expect((result as any).source).toBeTruthy();
});
