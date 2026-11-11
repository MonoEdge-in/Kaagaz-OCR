import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: { alias: { "@": fileURLToPath(new URL(".", import.meta.url)) } },
  // 20 s, not 5 s: a test's first `await import()` of a large module (upload-follow, palette)
  // took over 5 s when the whole suite loads the machine (production QA, 5 Nov 2026).
  test: { include: ["tests/**/*.test.ts"], environment: "node", testTimeout: 20_000 },
});
