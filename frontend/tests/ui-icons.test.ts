import { describe, expect, it } from "vitest";
import { ICON_NAMES, ICONS, sealPath } from "@/components/ui/icons";

describe("icons", () => {
  it("draws every name in the contract, and nothing else", () => {
    expect(Object.keys(ICONS).sort()).toEqual([...ICON_NAMES].sort());
    expect(ICON_NAMES).toHaveLength(29);
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
