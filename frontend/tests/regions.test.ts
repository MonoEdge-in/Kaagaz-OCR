import { describe, expect, it } from "vitest";
import {
  boxToPixels,
  cellName,
  columnName,
  cropRect,
  cropView,
  fitCropView,
  framedCropView,
  decodeRegion,
  encodeRegion,
  highlightedCells,
  pageAspect,
  pageLink,
  parsePageParam,
  regionsOnPage,
} from "@/lib/regions";
import type { PageSummary, Region } from "@/lib/types";

// How the page was read does not matter to where a spot sits on it.
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
const wide: PageSummary = { page_no: 1, width: 1000, height: 500, unit: "px", has_image: true, ...read };

const b64url = (text: string) =>
  btoa(text).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

describe("boxToPixels", () => {
  it("scales page fractions to the drawn size", () => {
    expect(boxToPixels([0.1, 0.2, 0.5, 0.4], 1000, 500)).toEqual({
      left: 100,
      top: 100,
      width: 400,
      height: 100,
    });
  });

  it("clamps to the page and orders swapped corners", () => {
    const px = boxToPixels([1.2, 0.5, -0.1, 0.25], 200, 100);
    expect(px).toEqual({ left: 0, top: 25, width: 200, height: 25 });
  });
});

describe("pageAspect", () => {
  it("is width over height", () => {
    expect(pageAspect(wide)).toBe(2);
  });

  it("falls back to A4 portrait for an unknown size", () => {
    expect(pageAspect({ width: 0, height: 0 })).toBeCloseTo(1 / Math.SQRT2);
    expect(pageAspect({ width: Number.NaN, height: 10 })).toBeCloseTo(1 / Math.SQRT2);
  });
});

describe("cropRect", () => {
  it("pads the box by the same distance across and down", () => {
    const rect = cropRect({ kind: "box", page: 1, box: [0.4, 0.4, 0.6, 0.5] }, wide, 0.1);
    expect(rect).not.toBeNull();
    // The page is twice as wide as tall: 0.1 of the height is 0.05 of the width.
    expect(rect?.x0).toBeCloseTo(0.35);
    expect(rect?.x1).toBeCloseTo(0.65);
    expect(rect?.y0).toBeCloseTo(0.3);
    expect(rect?.y1).toBeCloseTo(0.6);
  });

  it("uses the shorter side on a portrait page", () => {
    const rect = cropRect({ kind: "box", page: 1, box: [0.5, 0.5, 0.5, 0.5] }, a4, 0.1);
    const padX = (rect?.x1 ?? 0) - 0.5;
    const padY = (rect?.y1 ?? 0) - 0.5;
    expect(padX * a4.width).toBeCloseTo(padY * a4.height);
    expect(padX).toBeCloseTo(0.1);
  });

  it("stays inside the page", () => {
    expect(cropRect({ kind: "box", page: 1, box: [0, 0, 0.1, 0.1] }, a4, 0.2)).toMatchObject({
      x0: 0,
      y0: 0,
    });
  });

  it("gives null for a cell", () => {
    expect(cropRect({ kind: "cell", page: 1, row: 2, column: 3 }, a4)).toBeNull();
  });
});

describe("cropView", () => {
  it("scales the page so the crop fills the frame", () => {
    const view = cropView({ x0: 0.25, y0: 0.5, x1: 0.75, y1: 0.75 }, 1, 400, 5000);
    expect(view.pageWidth).toBe(800);
    expect(view.pageHeight).toBe(800);
    expect(view.offsetX).toBe(200);
    expect(view.offsetY).toBe(400);
    expect(view.frameWidth).toBe(400);
    expect(view.frameHeight).toBe(200);
  });

  it("never enlarges the page beyond the limit", () => {
    const view = cropView({ x0: 0.5, y0: 0.5, x1: 0.501, y1: 0.501 }, 1, 400, 2000);
    expect(view.pageWidth).toBe(2000);
    expect(view.frameWidth).toBeCloseTo(2);
  });

  it("shows the whole page for an empty crop", () => {
    const view = cropView({ x0: 0.5, y0: 0.5, x1: 0.5, y1: 0.5 }, 2, 400, 2000);
    expect(view).toMatchObject({ pageWidth: 400, pageHeight: 200, offsetX: 0, offsetY: 0 });
  });
});

describe("fitCropView", () => {
  it("fills the width when the crop is wide", () => {
    // A strip twice as wide as tall, in a 400 x 225 box.
    const view = fitCropView({ x0: 0, y0: 0, x1: 0.5, y1: 0.25 }, 1, 400, 225, 5000);
    expect(view.frameWidth).toBeCloseTo(400);
    expect(view.frameHeight).toBeCloseTo(200);
  });

  it("fits the height when the crop is tall, so nothing is clipped", () => {
    const view = fitCropView({ x0: 0.4, y0: 0, x1: 0.5, y1: 0.5 }, 1, 400, 176, 5000);
    expect(view.frameHeight).toBeCloseTo(176);
    expect(view.frameWidth).toBeLessThanOrEqual(400);
    expect(view.frameWidth).toBeCloseTo(35.2);
  });

  it("stays inside the box on a portrait page", () => {
    const a4Aspect = 595 / 842;
    const view = fitCropView({ x0: 0.1, y0: 0.1, x1: 0.6, y1: 0.4 }, a4Aspect, 320, 176, 2400);
    expect(view.frameWidth).toBeLessThanOrEqual(320 + 1e-9);
    expect(view.frameHeight).toBeLessThanOrEqual(176 + 1e-9);
  });

  it("keeps the page size limit", () => {
    const view = fitCropView({ x0: 0.5, y0: 0.5, x1: 0.501, y1: 0.501 }, 1, 400, 400, 2000);
    expect(view.pageWidth).toBe(2000);
  });

  it("matches cropView when the height does not limit it", () => {
    const crop = { x0: 0.25, y0: 0.5, x1: 0.75, y1: 0.75 };
    expect(fitCropView(crop, 1, 400, 1000, 5000)).toEqual(cropView(crop, 1, 400, 5000));
  });

  it("shows the whole page for an empty crop, within the box", () => {
    const view = fitCropView({ x0: 0.5, y0: 0.5, x1: 0.5, y1: 0.5 }, 2, 400, 100, 2000);
    expect(view).toMatchObject({ offsetX: 0, offsetY: 0 });
    expect(view.frameHeight).toBeCloseTo(100);
    expect(view.frameWidth).toBeCloseTo(200);
  });
});

describe("cells", () => {
  it("names columns like a spreadsheet", () => {
    expect(columnName(1)).toBe("A");
    expect(columnName(26)).toBe("Z");
    expect(columnName(27)).toBe("AA");
    expect(columnName(703)).toBe("AAA");
    expect(cellName({ row: 7, column: 2 })).toBe("B7");
  });

  it("collects highlighted cells on one sheet only", () => {
    const regions: Region[] = [
      { kind: "cell", page: 1, row: 2, column: 3 },
      { kind: "cell", page: 2, row: 5, column: 1 },
      { kind: "box", page: 1, box: [0, 0, 1, 1] },
    ];
    expect([...highlightedCells(regions, 1)]).toEqual(["2:3"]);
    expect(regionsOnPage(regions, 1)).toHaveLength(2);
  });
});

describe("encodeRegion and decodeRegion", () => {
  const box: Region = { kind: "box", page: 3, box: [0.1, 0.2, 0.3, 0.4] };
  const cell: Region = { kind: "cell", page: 1, row: 12, column: 4 };

  it("round-trips boxes and cells through URL-safe text", () => {
    for (const region of [box, cell]) {
      const text = encodeRegion(region);
      expect(text).toMatch(/^[A-Za-z0-9_-]+$/);
      expect(decodeRegion(text)).toEqual(region);
    }
  });

  it("keeps only the region's own fields", () => {
    const text = b64url(JSON.stringify({ ...cell, extra: "<b>x</b>" }));
    expect(decodeRegion(text)).toEqual(cell);
  });

  it.each([
    ["missing", null],
    ["empty", ""],
    ["not base64url", "abc+/="],
    ["not JSON", b64url("hello")],
    ["an array", b64url("[1,2]")],
    ["null", b64url("null")],
    ["an unknown kind", b64url(JSON.stringify({ kind: "circle", page: 1 }))],
    ["page zero", b64url(JSON.stringify({ kind: "cell", page: 0, row: 1, column: 1 }))],
    ["a fractional row", b64url(JSON.stringify({ kind: "cell", page: 1, row: 1.5, column: 1 }))],
    ["a string page", b64url(JSON.stringify({ kind: "box", page: "1", box: [0, 0, 1, 1] }))],
    ["three corners", b64url(JSON.stringify({ kind: "box", page: 1, box: [0, 0, 1] }))],
    ["a corner off the page", b64url(JSON.stringify({ kind: "box", page: 1, box: [0, 0, 1.5, 1] }))],
    ["reversed corners", b64url(JSON.stringify({ kind: "box", page: 1, box: [0.5, 0, 0.1, 1] }))],
    ["a text corner", b64url(JSON.stringify({ kind: "box", page: 1, box: [0, 0, "1", 1] }))],
    ["far too long", "A".repeat(600)],
  ])("gives null for %s", (_name, text) => {
    expect(decodeRegion(text)).toBeNull();
  });
});

describe("links and ?page=", () => {
  it("builds the document link with the page and region", () => {
    const link = pageLink("doc-1", 2, { kind: "cell", page: 2, row: 3, column: 1 });
    const url = new URL(link, "http://x");
    expect(url.pathname).toBe("/documents/doc-1");
    expect(url.searchParams.get("page")).toBe("2");
    expect(decodeRegion(url.searchParams.get("region"))).toEqual({
      kind: "cell",
      page: 2,
      row: 3,
      column: 1,
    });
    expect(pageLink("doc-1", 1)).toBe("/documents/doc-1?page=1");
  });

  it("accepts only a page that exists", () => {
    expect(parsePageParam("2", [1, 2, 3])).toBe(2);
    expect(parsePageParam("4", [1, 2, 3])).toBeNull();
    expect(parsePageParam("1e1", [10])).toBeNull();
    expect(parsePageParam("-1", [1])).toBeNull();
    expect(parsePageParam(null, [1])).toBeNull();
  });
});

// UI audit R1 (16 Oct 2026): no empty band, and a margin so the highlight never touches the edge.
describe("framedCropView", () => {
  const a4Aspect = 595 / 842;

  it("stays inside the box and its height limit, margin included", () => {
    const view = framedCropView({ x0: 0.1, y0: 0.1, x1: 0.9, y1: 0.15 }, a4Aspect, 600, 176, 2400, 12);
    expect(view.frameWidth).toBeLessThanOrEqual(600 + 1e-9);
    expect(view.frameHeight).toBeLessThanOrEqual(176 + 1e-9);
  });

  it("shows the margin of page all round a spot at the page's very edge", () => {
    const crop = { x0: 0, y0: 0, x1: 1, y1: 0.05 }; // a full-width line at the top: cropRect cannot pad it
    const view = framedCropView(crop, a4Aspect, 600, 176, 2400, 12);
    // The crop itself starts 12px inside the frame on the left and the top.
    expect(view.offsetX).toBeCloseTo(-12);
    expect(view.offsetY).toBeCloseTo(-12);
    // and ends 12px before its right and bottom edges.
    expect(view.offsetX + view.frameWidth - crop.x1 * view.pageWidth).toBeCloseTo(12);
    expect(view.offsetY + view.frameHeight - crop.y1 * view.pageHeight).toBeCloseTo(12);
  });

  it("is fitCropView with no margin", () => {
    const crop = { x0: 0.2, y0: 0.3, x1: 0.6, y1: 0.35 };
    expect(framedCropView(crop, a4Aspect, 500, 176, 2400, 0)).toEqual(fitCropView(crop, a4Aspect, 500, 176, 2400));
  });
});
