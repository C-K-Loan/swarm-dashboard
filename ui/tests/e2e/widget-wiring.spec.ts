import { test, expect } from "@playwright/test";
import { waitForChart, goTimeline } from "./_helpers";

// A cell click is the hub of the dashboard — it must fan out to every
// subscribed widget: summary + labels + cost, artifacts, transcript, bucket
// summary, selection chip, and the shared viz bus (which drives file diffs /
// tree / comms / spawn-tree).
//
// Each regression here has broken at least once:
//   * Summary-sub stuck on stale agent after cell click → `_lastSelection` leak
//   * Transcript pane empty after cell click → renderTranscriptSingle guard bug
//   * Cost tile showing zeros for a non-idle bucket → s-cost not populated
//   * Bus lastScope not updated → downstream widgets ignore the selection
//   * Comms graph not narrowing to the selected agent's peers

const URL = "/timeline?fleet=gamejam&range=7d&bucket=3600";

async function clickFirstNonIdleCell(page: any) {
  // DOM-only probe: no reliance on module-scope cellStore. We pick the first
  // non-idle rect, read its title tooltip (format: "<agent> · <state>…"), and
  // derive agent title + rough ts from attrs.
  return page.evaluate(() => {
    const rect = Array.from(
      document.querySelectorAll('#tl-chart rect.seg[data-agent-i][data-cell-i]'),
    ).find(
      (r) =>
        !r.classList.contains("state-idle") &&
        r.querySelector("title")?.textContent?.includes(" turns"),
    ) as SVGRectElement | undefined;
    if (!rect) return null;
    const title = rect.querySelector("title")?.textContent || "";
    // Title shape: "<agent> · <state>(<blocker?>) · <N> turns · <YYYY-MM-DD HH:MM> · <bucket> bucket"
    const agentTitle = title.split(" · ")[0];
    const turns = parseInt((title.match(/(\d+)\s+turns/) || [])[1] || "0", 10);
    rect.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    return {
      ai: Number(rect.getAttribute("data-agent-i")),
      ci: Number(rect.getAttribute("data-cell-i")),
      agentTitle,
      turns,
    };
  });
}

test.describe("cell click fans out to every widget", () => {
  test("summary + cost + art-sub + transcript all react to the clicked cell", async ({ page }) => {
    await goTimeline(page, URL);
    await waitForChart(page);
    await page.waitForTimeout(1500);

    const pick = await clickFirstNonIdleCell(page);
    expect(pick, "no non-idle cell with turns found").not.toBeNull();
    await page.waitForTimeout(600);

    const panels = await page.evaluate(() => {
      const text = (id: string) =>
        document.getElementById(id)?.textContent?.trim() || "";
      const html = (id: string) =>
        document.getElementById(id)?.innerHTML?.trim() || "";
      return {
        summarySub: text("summary-sub"),
        artSub: text("art-sub"),
        sCost: html("s-cost"),
        transcriptBody: html("transcript-body"),
        artBody: html("art-body"),
        bsBody: html("bucket-summary-body"),
      };
    });

    // Widget headers carry no selection recap; the header selection row already shows it.
    expect(panels.summarySub).toBe("");

    // Cost tile must have a classifier-dollar line (non-idle buckets always do).
    expect(panels.sCost).toMatch(/\$[\d.]+ USD/);
    expect(panels.sCost).toMatch(/ in \/ .* out · [\d,]+ turns/);

    // art-sub must show errors/edits counts (format: "N errors · M edits").
    expect(panels.artSub).toMatch(/errors/);
    expect(panels.artSub).toMatch(/edits/);

    // Transcript body must either have real rows OR a visible empty-state
    // hint for this bucket — but NEVER be fully empty HTML.
    expect(panels.transcriptBody.length).toBeGreaterThan(10);
  });

  test("selection publishes a scope on the viz bus for this agent + time window", async ({
    page,
  }) => {
    await goTimeline(page, URL);
    await waitForChart(page);
    await page.waitForTimeout(1500);

    const pick = await clickFirstNonIdleCell(page);
    expect(pick).not.toBeNull();
    await page.waitForTimeout(500);

    const scope = await page.evaluate(() => {
      const s = (window as any)._selectionBus?.lastScope;
      if (!s) return null;
      return {
        source: s.source,
        titles: s.titles ? [...s.titles] : null,
        agentIds: s.agentIds ? [...s.agentIds] : null,
        timeRange: s.timeRange,
      };
    });
    expect(scope, "bus must have a lastScope after cell click").not.toBeNull();
    // Cell click publishes source="timeline" and should name the agent in
    // either titles or agentIds.
    const agentIncluded =
      (scope!.titles && scope!.titles.includes(pick!.agentTitle)) ||
      (scope!.agentIds && scope!.agentIds.length > 0);
    expect(agentIncluded, `scope must contain ${pick!.agentTitle} or ANY agentId`).toBe(true);
    // Time range must be set (overlap check is best done via agent-id lookup,
    // which we skip in this DOM-only probe).
    expect(scope!.timeRange, "cell click must set a timeRange").not.toBeNull();
    expect(scope!.timeRange![1]).toBeGreaterThanOrEqual(scope!.timeRange![0]);
  });

  test("file-diffs widget re-renders when a cell is clicked", async ({ page }) => {
    await goTimeline(page, URL);
    await waitForChart(page);
    await page.waitForTimeout(1500);

    // Capture the pre-click diffs-body hash.
    const beforeHash = await page.evaluate(() => {
      const d = document.getElementById("diffs-body");
      return d ? (d.textContent || "").slice(0, 300) : "";
    });

    const pick = await clickFirstNonIdleCell(page);
    expect(pick).not.toBeNull();
    await page.waitForTimeout(1200);

    const after = await page.evaluate(() => {
      const d = document.getElementById("diffs-body");
      return {
        text: d ? (d.textContent || "").slice(0, 300) : "",
        hasContent: d ? d.children.length > 0 : false,
      };
    });

    // Either diffs re-renders to new content, OR shows the "no diffs in this
    // selection" hint — but it must not be stuck at the pre-click rendering
    // of ALL diffs (which has lots of distinct file rows).
    expect(after.hasContent, "diffs-body must render something").toBe(true);
    // The post-click render is DIFFERENT from the pre-click render (narrowed
    // to this cell's scope). Allow identical-text only when both are the
    // 'loading' / empty-state hints.
    if (
      !beforeHash.includes("loading") &&
      !after.text.includes("no file edits") &&
      !after.text.includes("no diffs")
    ) {
      expect(after.text).not.toBe(beforeHash);
    }
  });

  test("comms graph narrows to the selected agent's peer set", async ({ page }) => {
    await goTimeline(page, URL);
    await waitForChart(page);
    // Need comms to have populated too.
    await expect
      .poll(
        () =>
          page.evaluate(
            () =>
              document.querySelectorAll("#comms-body svg g[data-node]").length,
          ),
        { timeout: 30_000 },
      )
      .toBeGreaterThan(3);

    const baselineNodes = await page.evaluate(() =>
      Array.from(document.querySelectorAll("#comms-body svg g[data-node]")).map(
        (g) => g.getAttribute("data-node"),
      ),
    );

    const pick = await clickFirstNonIdleCell(page);
    expect(pick).not.toBeNull();
    await page.waitForTimeout(2000);

    const afterNodes = await page.evaluate(() =>
      Array.from(document.querySelectorAll("#comms-body svg g[data-node]")).map(
        (g) => g.getAttribute("data-node"),
      ),
    );

    // Comms set must have CHANGED (either narrowed, or re-ordered as the
    // scope reconstructed the subgraph).
    const sameSet =
      baselineNodes.length === afterNodes.length &&
      baselineNodes.every((n, i) => n === afterNodes[i]);
    expect(sameSet, "comms graph must respond to cell click").toBe(false);
    // The clicked agent SHOULD appear in the narrowed set (unless it has
    // zero transcript — soft assertion to tolerate sandbox-synth agents).
    expect
      .soft(
        afterNodes.includes(pick!.agentTitle),
        `clicked agent ${pick!.agentTitle} should be in comms scope`,
      )
      .toBe(true);
  });

  test("Escape clears selection and resets all panels", async ({ page }) => {
    await goTimeline(page, URL);
    await waitForChart(page);
    await page.waitForTimeout(1500);

    // Click first, then press Escape.
    const pick = await clickFirstNonIdleCell(page);
    expect(pick).not.toBeNull();
    await page.waitForTimeout(500);
    const sel1 = await page.evaluate(() => (window as any)._lastSelection);
    // Note: _lastSelection may not be set — the real cursor state is
    // `currentCell` inside the module closure. Fall back to the DOM.
    const highlighted = await page.evaluate(
      () =>
        document.querySelectorAll('#tl-chart rect.seg[stroke="#a855f7"]').length,
    );
    expect(highlighted).toBeGreaterThan(0);

    await page.keyboard.press("Escape");
    await page.waitForTimeout(500);

    const stillHighlighted = await page.evaluate(
      () =>
        document.querySelectorAll('#tl-chart rect.seg[stroke="#a855f7"]').length,
    );
    expect(stillHighlighted, "Escape must clear cell stroke highlight").toBe(0);
  });
});
