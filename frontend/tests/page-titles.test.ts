// Each screen's browser tab names its section (WCAG 2.4.2), "Documents · Kaagaz · MonoEdge",
// and never a document or customer name: browser history syncs off the device
// (UI touch-up council, 15 Oct 2026).

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const read = (path: string) => readFileSync(join(__dirname, "..", path), "utf8");

const TITLES: Record<string, string> = {
  "app/(app)/documents/layout.tsx": "Documents",
  "app/(app)/chat/layout.tsx": "Ask",
  "app/(app)/review/layout.tsx": "Review",
  "app/(app)/words/layout.tsx": "My words",
  "app/(app)/admin/page.tsx": "Admin",
  "app/operator/(console)/page.tsx": "Platform",
  "app/login/page.tsx": "Sign in",
  "app/operator/login/page.tsx": "Sign in",
};

describe("page titles", () => {
  it("adds the section's name to Kaagaz · MonoEdge in the root layout", () => {
    // Changed on purpose (MonoEdge theme, 16 Oct 2026): the tab names MonoEdge too.
    expect(read("app/layout.tsx")).toContain(
      'title: { default: "Kaagaz · MonoEdge", template: "%s · Kaagaz · MonoEdge" }',
    );
  });

  it.each(Object.entries(TITLES))("gives %s the fixed title %s, from a server file", (file, title) => {
    const source = read(file);
    expect(source).not.toMatch(/^"use client"/);
    expect(source).toContain(`export const metadata = { title: "${title}" };`);
    expect(source).not.toMatch(/generateMetadata|title:\s*`/);
  });
});
