// #246 O1: the operator's Release card shows the commit the API runs (GET /operator/health's
// release), shortened like a git log; an image built by hand says so.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { commitWords } from "@/components/operator/platform";

describe("the Release card", () => {
  it("shows the running commit's first 12 characters", () => {
    expect(commitWords("c77586d0123456789abcdef0123456789abcdef0")).toBe("c77586d01234");
  });

  it("says when the image was built by hand, with no commit", () => {
    expect(commitWords("local")).toBe("Built by hand, no commit");
  });

  it("sits on the Health tab, with the full commit on hover and a pointer to the deploy log", () => {
    const view = readFileSync(join(__dirname, "../components/operator/HealthView.tsx"), "utf8");
    expect(view).toContain('<h2 id="health-release-heading" className={admin.sectionHeading}>');
    expect(view).toContain("title={health.release.commit}");
    expect(view).toContain("{commitWords(health.release.commit)}");
    expect(view).toContain("journalctl -u kaagaz-deploy");
  });
});
