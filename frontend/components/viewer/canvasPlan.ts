// How big a PDF page's canvas is, and how PDF.js draws into it. Pure, so it is
// unit tested in node (tests/canvas-plan.test.ts).
//
// The canvas covers only the part of the page on screen (the view): a crop
// preview draws its frame, not the whole enlarged page. The page keeps its
// full size as a stage, so highlight boxes (lib/regions.ts) stay in one
// coordinate system. PDF.js draws the page at CSS size; the transform scales
// it to device pixels and shifts the view's corner to the canvas's corner.

/** iOS Safari draws a canvas with more pixels than this blank. */
export const MAX_CANVAS_PIXELS = 16_777_216;
const AREA_BUDGET = 16e6; // a little under the limit, so rounding never crosses it
const MAX_SIDE = 4096;
const MAX_RATIO_PAGE = 2;
const MAX_RATIO_CROP = 3; // a crop is small, so it can afford a sharper drawing

/** A rectangle in CSS pixels on the drawn page, origin top left. */
export interface ViewRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface CanvasPlan {
  /** The canvas's size in device pixels. */
  width: number;
  height: number;
  /** Device pixels per CSS pixel. */
  ratio: number;
  /** For PDF.js render(): applied before the page's own viewport transform. */
  transform: [number, number, number, number, number, number];
}

export function canvasPlan(view: ViewRect, dpr: number, isCrop: boolean): CanvasPlan {
  const w = Math.max(1, view.width);
  const h = Math.max(1, view.height);
  const ratio = Math.min(
    Number.isFinite(dpr) && dpr > 0 ? dpr : 1,
    isCrop ? MAX_RATIO_CROP : MAX_RATIO_PAGE,
    MAX_SIDE / w,
    Math.sqrt(AREA_BUDGET / (w * h)),
  );
  return {
    width: Math.max(1, Math.floor(w * ratio)),
    height: Math.max(1, Math.floor(h * ratio)),
    ratio,
    transform: [ratio, 0, 0, ratio, 0 - view.x * ratio, 0 - view.y * ratio],
  };
}
