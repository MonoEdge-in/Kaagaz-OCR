// Finding 5 (crop canvas): a crop preview draws only its frame, not the whole
// page, with the PDF.js transform [r,0,0,r,-x*r,-y*r]. The pixel ratio is
// capped so no canvas passes iOS Safari's 16,777,216-pixel limit (a bigger
// one draws blank there): ratio = min(dpr, 3, 4096/w, sqrt(budget/(w*h))),
// budget 16e6 unless given. This holds for the full page too: zoom 3 drew
// 23.7 Mpx. The canvas is whole device pixels, and the transform's scale is
// taken from them (UI quality council, 15 Oct 2026).

import { describe, expect, it } from "vitest";
import { MAX_CANVAS_PIXELS, canvasPlan, resolutionQuery, stretchY } from "@/components/viewer/canvasPlan";
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

  it("lets crops and the full page go to dpr 3", () => {
    expect(canvasPlan({ x: 0, y: 0, width: 300, height: 100 }, 3, true).ratio).toBe(3);
    // Changed on purpose (UI quality council, 15 Oct 2026): the full page was capped at 2, soft on a dpr 3 phone.
    expect(canvasPlan({ x: 0, y: 0, width: 300, height: 400 }, 3, false).ratio).toBe(3);
    expect(canvasPlan({ x: 0, y: 0, width: 300, height: 400 }, 4, true).ratio).toBe(3);
    expect(canvasPlan({ x: 0, y: 0, width: 300, height: 400 }, 4, false).ratio).toBe(3);
  });

  it("rounds to whole device pixels and scales by exactly those (no resampling blur)", () => {
    // 333.5 x 100.25 CSS px at dpr 1.5: 500.25 -> 500 and 150.375 -> 150 device px.
    const plan = canvasPlan({ x: 10, y: 20, width: 333.5, height: 100.25 }, 1.5, true);
    expect({ width: plan.width, height: plan.height }).toEqual({ width: 500, height: 150 });
    expect(plan.transform).toEqual([500 / 333.5, 0, 0, 150 / 100.25, -10 * (500 / 333.5), -20 * (150 / 100.25)]);
  });

  it("draws fewer pixels under a smaller budget (the page under the sharp zoom overlay)", () => {
    const view = { x: 0, y: 0, width: 2000, height: 2828 };
    expect(pixels(canvasPlan(view, 2, false, 4e6))).toBeLessThanOrEqual(4e6 + 2828 + 2000);
    expect(pixels(canvasPlan(view, 2, false))).toBeGreaterThan(8e6);
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
      // Changed on purpose (UI quality council, 15 Oct 2026): the full page may go to 3 too.
      expect(r).toBeLessThanOrEqual(Math.min(dpr, 3) + 1e-12);
      // Changed on purpose (UI quality council, 15 Oct 2026): the scale comes from the rounded pixels.
      const [sx, , , sy] = plan.transform;
      expect(plan.transform).toEqual([sx, 0, 0, sy, -view.x * sx, -view.y * sy]);
      // The canvas's pixels are within 1 device pixel of its CSS size at the ratio,
      // and its CSS size times the transform's scale is exactly its pixels.
      expect(Math.abs(plan.width - view.width * r)).toBeLessThanOrEqual(1);
      expect(Math.abs(plan.height - view.height * r)).toBeLessThanOrEqual(1);
      expect(view.width * sx).toBeCloseTo(plan.width, 6);
      expect(view.height * sy).toBeCloseTo(plan.height, 6);
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

describe("stretchY (the PDF's page shape against the stage's)", () => {
  it("stretches a rounding gap away", () => {
    // fitPageSize floors the stage height: 565 against 565.69.
    expect(stretchY(565, 400 * Math.SQRT2)).toBeCloseTo(565 / (400 * Math.SQRT2));
    expect(stretchY(842, 842)).toBe(1);
  });

  it("refuses a page more than 2% off (rotated or cropped): the stored image is shown", () => {
    expect(stretchY(1030, 1000)).toBeNull();
    expect(stretchY(970, 1000)).toBeNull();
    expect(stretchY(707, 1000)).toBeNull(); // a landscape page on a portrait stage
    expect(stretchY(1019, 1000)).toBeCloseTo(1.019);
    expect(stretchY(100, 0)).toBeNull();
    expect(stretchY(Number.NaN, 100)).toBeNull();
  });
});

describe("resolutionQuery (a redraw when the window moves to another screen)", () => {
  it("matches exactly the current device pixel ratio", () => {
    expect(resolutionQuery(2)).toBe("(resolution: 2dppx)");
    expect(resolutionQuery(1.25)).toBe("(resolution: 1.25dppx)");
  });
});
