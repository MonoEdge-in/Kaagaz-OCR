import { describe, expect, it } from "vitest";
import { ICON_NAMES, ICONS, sealPath } from "@/components/ui/icons";

describe("icons", () => {
  it("draws every name in the contract, and nothing else", () => {
    expect(Object.keys(ICONS).sort()).toEqual([...ICON_NAMES].sort());
    // Changed on purpose (UI quality council, 15 Oct 2026): "download" for Download original on phones.
    // Changed on purpose (PRD 1, screens): "home" for the dashboard's nav link (File management D1).
    // Changed on purpose (owner, 29 Oct 2026): "ring-dashed" for a value not checked yet (LabelBadge).
    expect(ICON_NAMES).toHaveLength(36); // 29 + "scan-text" + "words" + "download" + "to-top" (16 Oct 2026) + "more" (17 Oct 2026) + "home" + "ring-dashed"
  });

  it("gives every icon at least one shape, inside the 24 x 24 grid", () => {
    for (const name of ICON_NAMES) {
      const shapes = ICONS[name];
      expect(shapes.length, name).toBeGreaterThan(0);
      for (const shape of shapes) {
        if (shape.kind === "path") {
          expect(shape.d, name).toMatch(/^M[\d.\s,A-Za-z-]+$/);
          for (const n of shape.d.match(/-?\d*\.?\d+/g) ?? []) {
            expect(Math.abs(Number(n)), name).toBeLessThanOrEqual(24);
          }
        } else if (shape.kind === "circle") {
          expect(shape.cx - shape.r, name).toBeGreaterThanOrEqual(0);
          expect(shape.cx + shape.r, name).toBeLessThanOrEqual(24);
        } else {
          expect(shape.x + shape.width, name).toBeLessThanOrEqual(24);
          expect(shape.y + shape.height, name).toBeLessThanOrEqual(24);
        }
      }
    }
  });

  it("builds the seal as a closed star", () => {
    const d = sealPath(8, 9.5, 8);
    expect(d.startsWith("M12.00 2.50")).toBe(true);
    expect(d.endsWith("Z")).toBe(true);
    expect(d.match(/[ML]/g)).toHaveLength(16);
  });
});
