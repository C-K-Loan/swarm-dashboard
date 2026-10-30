import { defineConfig, devices } from "@playwright/test";

// Point at the astro dev server already running on :4321 (same data astro-preview
// / [REDACTED_HOST] serve). We do NOT auto-start it: the daemon is long-lived and the
// test runs are read-mostly (we only drive click/keyboard, never mutate server
// state), so sharing is fine.
export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 120_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  // Dev server + big-fleet payloads choke under 3 concurrent navigations.
  // One worker keeps the astro HMR server responsive and tests deterministic.
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"]],
  use: {
    baseURL: process.env.BASE_URL || "http://localhost:4321",
    actionTimeout: 15_000,
    navigationTimeout: 60_000,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chrome",
      // Use the system google-chrome at /usr/bin/google-chrome so we don't need
      // `playwright install` + its ~450MB browser download.
      use: { ...devices["Desktop Chrome"], channel: "chrome" },
    },
  ],
});
