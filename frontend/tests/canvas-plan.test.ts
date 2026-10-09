// Finding 5 (crop canvas): a crop preview draws only its frame, not the whole
// page, with the PDF.js transform [r,0,0,r,-x*r,-y*r]. The pixel ratio is
// capped so no canvas passes iOS Safari's 16,777,216-pixel limit (a bigger
// one draws blank there): ratio = min(dpr, 2 (3 for crops), 4096/w,
// sqrt(16e6/(w*h))). This holds for the full page too: zoom 3 drew 23.7 Mpx.

import { describe, expect, it } from "vitest";
import { MAX_CANVAS_PIXELS, canvasPlan } from "@/components/viewer/canvasPlan";
import { cropRect, fitCropView, pageAspect } from "@/lib/regions";
import type { PageSummary } from "@/lib/types";

const pixels = (plan: { width: number; height: number }) => plan.width * plan.height;
const MiB = 1024 * 1024;

describe("canvasPlan", () => {
  it("draws a crop at frame size, shifted by the frame's place on the page", () => {
    const plan = canvasPlan({ x: 100, y: 50, width: 300, height: 200 }, 2, true);
    expect(plan.ratio).toBe(2);
    expect({ width: plan.width, height: plan.height }).toEqual({ width: 600, height: 400 });
    expect(plan.transform).toEqual([2, 0, 0, 2, -200, -100]);
  });

  it("draws the whole page with no shift", () => {
    const plan = canvasPlan({ x: 0, y: 0, width: 800, height: 1000 }, 1, false);
    expect(plan.transform).toEqual([1, 0, 0, 1, 0, 0]);
    expect({ width: plan.width, height: plan.height }).toEqual({ width: 800, height: 1000 });
  });

  it("lets crops go to dpr 3, the full page to dpr 2", () => {
    expect(canvasPlan({ x: 0, y: 0, width: 300, height: 100 }, 3, true).ratio).toBe(3);
    expect(canvasPlan({ x: 0, y: 0, width: 300, height: 400 }, 3, false).ratio).toBe(2);
    expect(canvasPlan({ x: 0, y: 0, width: 300, height: 400 }, 4, true).ratio).toBe(3);
  });

  it("treats a missing or broken dpr as 1", () => {
    expect(canvasPlan({ x: 0, y: 0, width: 300, height: 400 }, 0, false).ratio).toBe(1);
    expect(canvasPlan({ x: 0, y: 0, width: 300, height: 400 }, Number.NaN, true).ratio).toBe(1);
  });

  it("keeps the full page at zoom 3 under the iOS limit (it was 23.7 Mpx)", () => {
    // A 1000 px wide container at zoom 3, an A4 page, dpr 2.
    const plan = canvasPlan({ x: 0, y: 0, width: 3000, height: 3000 * Math.SQRT2 }, 2, false);
    expect(pixels(plan)).toBeLessThanOrEqual(MAX_CANVAS_PIXELS);
    expect(plan.width).toBeLessThanOrEqual(4096);
  });

  it("never passes 16,777,216 pixels or 4096 across (seeded property test)", () => {
    // mulberry32: a small seeded generator, so a failure always comes back the same.
    let seed = 20261002;
    const random = () => {
      seed = (seed + 0x6d2b79f5) | 0;
      let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
    for (let i = 0; i < 2000; i += 1) {
      const view = {
        x: random() * 5000,
        y: random() * 5000,
        width: 1 + random() * 12000,
        height: 1 + random() * 20000,
      };
      const dpr = 0.5 + random() * 3.5;
      const isCrop = random() < 0.5;
      const plan = canvasPlan(view, dpr, isCrop);
      const r = plan.ratio;
      expect(pixels(plan)).toBeLessThanOrEqual(MAX_CANVAS_PIXELS);
      expect(plan.width).toBeLessThanOrEqual(4096);
      expect(r).toBeGreaterThan(0);
      expect(r).toBeLessThanOrEqual(Math.min(dpr, isCrop ? 3 : 2) + 1e-12);
      expect(plan.transform).toEqual([r, 0, 0, r, -view.x * r, -view.y * r]);
    }
  });

  it("makes a review card's crop about 1 MiB of canvas, not the whole page", () => {
    // A small amount on an A4 page, shown in a 560 x 180 card at dpr 2.
    const read: Omit<PageSummary, "page_no" | "width" | "height" | "unit" | "has_image"> = {
      route: "text_layer",
      effective_dpi: null,
      low_resolution: false,
      read_by: "text_layer",
      read_quality: "good",
      read_issues: [],
      unsure_words: 0,
      illegible_words: 0,
      unpinned_lines: 0,
      accepted_by: null,
      accepted_at: null,
      read_again_requested_at: null,
    };
    const a4: PageSummary = { page_no: 1, width: 595, height: 842, unit: "pt", has_image: true, ...read };
    const rect = cropRect({ kind: "box", page: 1, box: [0.6, 0.7, 0.75, 0.72] }, a4);
    if (!rect) throw new Error("no crop");
    const view = fitCropView(rect, pageAspect(a4), 560, 180, 2400);
    const page = canvasPlan({ x: 0, y: 0, width: view.pageWidth, height: view.pageHeight }, 2, false);
    const frame = canvasPlan(
      { x: view.offsetX, y: view.offsetY, width: view.frameWidth, height: view.frameHeight },
      2,
      true,
    );
    expect((pixels(page) * 4) / MiB).toBeGreaterThan(50); // what drawing the whole page costs
    expect((pixels(frame) * 4) / MiB).toBeLessThan(1.5);
  });
});
