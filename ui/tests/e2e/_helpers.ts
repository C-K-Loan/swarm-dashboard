import { expect } from "@playwright/test";
import type { Page } from "@playwright/test";

// Nav wrapper: `domcontentloaded` returns as soon as HTML is parsed. The dev
// server's full `load` waits for every lazy data shard + font + image, which
// routinely overshoots 20-30s on gamejam. Our tests follow goto with
// `waitForChart` / `waitForComms` anyway, so DCL + explicit wait is strictly
// stricter and ~4x faster.
export async function goTimeline(page: Page, url: string) {
  await page.goto(url, { waitUntil: "domcontentloaded" });
}

// Wait for the timeline to produce visible cell rects. Covers both the raw-data
// fetch (lazy) and the chart's first render.
export async function waitForChart(page: Page, minCells = 10) {
  await expect
    .poll(
      async () =>
        page.evaluate(
          () =>
            document.querySelectorAll("#tl-chart rect.seg[data-agent-i][data-cell-i]").length,
        ),
      { timeout: 30_000, intervals: [500] },
    )
    .toBeGreaterThan(minCells);
}

// Wait for the A2A comms graph to render nodes.
export async function waitForComms(page: Page, minNodes = 2) {
  await expect
    .poll(
      async () =>
        page.evaluate(
          () => document.querySelectorAll("#comms-body svg g[data-node]").length,
        ),
      { timeout: 30_000, intervals: [500] },
    )
    .toBeGreaterThan(minNodes);
}

// Dispatch a bubbling keydown on document from a visible, non-input focus.
// Prefer this over page.keyboard.press when we need to prove the chart
// handlers (not some focused control) got the key.
export async function pressChartKey(
  page: Page,
  key: string,
  opts: { shift?: boolean } = {},
) {
  await page.evaluate(
    ({ key, shift }) => {
      document.dispatchEvent(
        new KeyboardEvent("keydown", { key, bubbles: true, shiftKey: !!shift }),
      );
    },
    { key, shift: opts.shift },
  );
}

// Convenience: click one of the chart's SVG rects via dispatchEvent so we
// don't fight the Astro dev-toolbar / grid-stack overlays. Returns the
// element's attributes we care about.
export async function clickRect(page: Page, selector: string) {
  return page.evaluate((sel) => {
    const el = document.querySelector(sel) as SVGRectElement | null;
    if (!el) return null;
    el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    return {
      agentI: el.getAttribute("data-agent-i"),
      cellI: el.getAttribute("data-cell-i"),
      colTs: el.getAttribute("data-col-ts"),
      agentRowI: el.getAttribute("data-agent-row-i"),
    };
  }, selector);
}
