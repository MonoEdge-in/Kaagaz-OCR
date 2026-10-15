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
const MAX_SIDE = 4096; // also what most limits a zoomed full page; the sharp zoom overlay draws only what shows
const MAX_RATIO_PAGE = 3;
const MAX_RATIO_CROP = 3; // a crop is small, so it can afford a sharper drawing
const MAX_STRETCH = 0.02; // the stage may differ from the PDF's own page shape by this much

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
  /** Device pixels per CSS pixel, before rounding to whole device pixels. */
  ratio: number;
  /** For PDF.js render(): applied before the page's own viewport transform. */
  transform: [number, number, number, number, number, number];
}

/**
 * `budget` is the most device pixels the canvas may have: less for the
 * blurred page under the sharp zoom overlay (PageViewer.tsx).
 */
export function canvasPlan(view: ViewRect, dpr: number, isCrop: boolean, budget = AREA_BUDGET): CanvasPlan {
  const w = Math.max(1, view.width);
  const h = Math.max(1, view.height);
  const ratio = Math.min(
    Number.isFinite(dpr) && dpr > 0 ? dpr : 1,
    isCrop ? MAX_RATIO_CROP : MAX_RATIO_PAGE,
    MAX_SIDE / w,
    Math.sqrt(budget / (w * h)),
  );
  // Whole device pixels, and the scale taken from them, so the canvas's CSS
  // size times the scale is exactly its pixels: no resampling blur.
  const width = Math.max(1, Math.round(w * ratio));
  const height = Math.max(1, Math.round(h * ratio));
  const sx = width / w;
  const sy = height / h;
  return { width, height, ratio, transform: [sx, 0, 0, sy, 0 - view.x * sx, 0 - view.y * sy] };
}

/**
 * How much to stretch the drawing downwards so it fills a stage `stageHeight`
 * tall, when PDF.js says the page is `pageHeight` tall at the same width; null
 * when the two shapes differ by more than 2% (a rotated or cropped page: the
 * stored page image is shown instead, so highlight boxes stay on their spot).
 */
export function stretchY(stageHeight: number, pageHeight: number): number | null {
  const k = stageHeight / pageHeight;
  return Number.isFinite(k) && Math.abs(k - 1) <= MAX_STRETCH ? k : null;
}

/** The media query that stops matching when the window's device pixel ratio changes. */
export function resolutionQuery(dpr: number): string {
  return `(resolution: ${dpr}dppx)`;
}
