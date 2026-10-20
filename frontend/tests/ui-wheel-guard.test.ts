// MonoEdge design system (16 Oct 2026, brief B): a wheel never changes a
// number field or a slider; the one document listener takes the focus off it.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { blurIfNumberField } from "@/components/ui/WheelGuard";

const field = (tagName: string, type: string) => {
  const element = { tagName, type, blurred: false, blur: () => (element.blurred = true) };
  return element;
};

describe("the wheel guard", () => {
  it("blurs a focused number field or slider", () => {
    for (const type of ["number", "range"]) {
      const input = field("INPUT", type);
      expect(blurIfNumberField(input as unknown as Element)).toBe(true);
      expect(input.blurred).toBe(true);
    }
  });

  it("leaves text fields, other elements and no focus alone", () => {
    for (const element of [field("INPUT", "text"), field("TEXTAREA", "number"), field("SELECT", "")]) {
      expect(blurIfNumberField(element as unknown as Element)).toBe(false);
      expect(element.blurred).toBe(false);
    }
    expect(blurIfNumberField(null)).toBe(false);
  });

  it("listens once, passively, from the root layout", () => {
    const guard = readFileSync(join(__dirname, "../components/ui/WheelGuard.tsx"), "utf8");
    expect(guard).toContain('document.addEventListener("wheel", onWheel, { passive: true });');
    const layout = readFileSync(join(__dirname, "../app/layout.tsx"), "utf8");
    expect(layout.match(/<WheelGuard \/>/g)).toHaveLength(1);
  });
});
