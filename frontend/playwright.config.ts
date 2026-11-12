// Layout checks in real browsers (e2e/): WebKit, for Safari on a Mac, and Chromium, at the
// desktop widths the handoff draws, against a dev server with a fake /api (e2e/fake-api.ts).
// Run: npx playwright install chromium webkit, then npm run e2e.

import { defineConfig, devices } from "@playwright/test";

const PORT = 3123;

export default defineConfig({
  testDir: "e2e",
  outputDir: "e2e-results",
  fullyParallel: true,
  reporter: [["list"]],
  timeout: 60_000,
  use: { baseURL: `http://127.0.0.1:${PORT}` },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "webkit", use: { ...devices["Desktop Safari"] } },
  ],
  webServer: {
    command: `npx next dev -p ${PORT} -H 127.0.0.1`,
    url: `http://127.0.0.1:${PORT}/login`,
    reuseExistingServer: true,
    timeout: 180_000,
  },
});
