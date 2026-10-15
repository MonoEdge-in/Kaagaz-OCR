"use client";

// One PDF page drawn by PDF.js on a canvas, at the size the viewer asks for.
// With `view`, only that part of the page is drawn (a crop preview's frame, or
// the sharp zoom overlay): the canvas sits at the view's place on a page-size
// stage (canvasPlan.ts). Any failure (the file, the worker, a broken page, a
// page whose shape differs from the stage's) calls onFailed, and the viewer
// falls back to the stored page image. onRendered says the page is drawn, so
// the viewer can take its loading skeleton away. The document is held while it
// is on screen (pdfDocuments.ts), in an effect of its own: a new size or page
// redraws, and only a new document lets go of the old one.
//
// Each drawing goes to a canvas off screen and is copied over once done, so
// the page never goes blank; until then the old drawing is stretched to the
// new size. A redraw waits until the size has been still for 150ms (a resize
// drag draws once), and a new device pixel ratio (the window moved to another
// screen) redraws too.

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import type { PDFDocumentProxy, RenderTask } from "pdfjs-dist";
import { canvasPlan, resolutionQuery, stretchY } from "./canvasPlan";
import type { ViewRect } from "./canvasPlan";
import { holdPdf, releasePdf } from "./pdfDocuments";

export interface PdfPageProps {
  documentId: string;
  pageNo: number;
  width: number;
  height: number;
  /** The part of the page to draw, in CSS pixels on the page; the whole page if left out. */
  view?: ViewRect;
  /** The most device pixels to draw (canvasPlan.ts); its own default if left out. */
  budget?: number;
  /** Left out for a copy screen readers skip (the sharp zoom overlay). */
  label?: string;
  onFailed: () => void;
  onRendered?: () => void;
}

const REDRAW_DELAY_MS = 150;

/** Calls back when the device pixel ratio changes; the query is set up again for the new ratio. */
function subscribeRatio(changed: () => void): () => void {
  let query: MediaQueryList | null = null;
  const onChange = () => {
    arm();
    changed();
  };
  const arm = () => {
    query?.removeEventListener("change", onChange);
    query = window.matchMedia(resolutionQuery(window.devicePixelRatio));
    query.addEventListener("change", onChange);
  };
  arm();
  return () => query?.removeEventListener("change", onChange);
}

export default function PdfPage({
  documentId,
  pageNo,
  width,
  height,
  view,
  budget,
  label,
  onFailed,
  onRendered,
}: PdfPageProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const pdfRef = useRef<Promise<PDFDocumentProxy> | null>(null);
  const drawnOnce = useRef(false);
  // The part of the page a view's canvas holds now, and the page width it was drawn at.
  const [drawn, setDrawn] = useState<{ view: ViewRect; width: number } | null>(null);
  const dpr = useSyncExternalStore(subscribeRatio, () => window.devicePixelRatio);
  const isCrop = view !== undefined;
  const vx = view?.x ?? 0;
  const vy = view?.y ?? 0;
  const vw = view?.width ?? width;
  const vh = view?.height ?? height;

  // Runs before the drawing effect below, so it always finds the held PDF.
  useEffect(() => {
    const held = holdPdf(documentId);
    pdfRef.current = held;
    return () => releasePdf(documentId, held);
  }, [documentId]);

  // Gone from the screen: the browser gets the canvas's memory back.
  useEffect(() => {
    const canvas = canvasRef.current;
    return () => {
      if (!canvas) return;
      canvas.width = 0;
      canvas.height = 0;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    let task: RenderTask | null = null;
    const off = document.createElement("canvas");
    const draw = async () => {
      const held = pdfRef.current;
      if (!held) throw new Error("no PDF held");
      const pdf = await held;
      if (cancelled) return;
      const page = await pdf.getPage(pageNo);
      if (cancelled) return;
      const natural = page.getViewport({ scale: 1 });
      const viewport = page.getViewport({ scale: width / natural.width });
      // A small rounding gap is stretched away, so boxes on the stage match the drawing.
      const stretch = stretchY(height, viewport.height);
      if (stretch === null) throw new Error("the page's shape differs from the stage's");
      const rect = { x: vx, y: vy, width: vw, height: vh };
      const plan = canvasPlan(rect, dpr, isCrop, budget);
      const [a, b, c, d, e, f] = plan.transform;
      off.width = plan.width;
      off.height = plan.height;
      const started = performance.mark("kaagaz:pdf-render-start");
      task = page.render({ canvas: off, viewport, transform: [a, b, c, d * stretch, e, f] });
      await task.promise;
      const canvas = canvasRef.current;
      if (cancelled || !canvas) return;
      performance.measure("kaagaz:pdf-render", { start: started.startTime });
      performance.clearMarks("kaagaz:pdf-render-start");
      canvas.width = plan.width;
      canvas.height = plan.height;
      canvas.getContext("2d")?.drawImage(off, 0, 0);
      off.width = 0;
      off.height = 0;
      drawnOnce.current = true;
      if (isCrop) setDrawn({ view: rect, width });
      onRendered?.();
    };
    const timer = setTimeout(
      () => {
        draw().catch((error: unknown) => {
          const name = error instanceof Error ? error.name : "";
          if (!cancelled && name !== "RenderingCancelledException") onFailed();
        });
      },
      drawnOnce.current ? REDRAW_DELAY_MS : 0,
    );
    return () => {
      cancelled = true;
      clearTimeout(timer);
      task?.cancel();
      off.width = 0;
      off.height = 0;
    };
  }, [documentId, pageNo, width, height, vx, vy, vw, vh, isCrop, budget, dpr, onFailed, onRendered]);

  // The whole page always fills the stage. A view stays where its drawing
  // belongs (scaled with the page) until the new one is ready, so the
  // overlay's old drawing never slides to where the user has scrolled since.
  const scale = drawn ? width / drawn.width : 1;
  const shown =
    isCrop && drawn
      ? {
          x: drawn.view.x * scale,
          y: drawn.view.y * scale,
          width: drawn.view.width * scale,
          height: drawn.view.height * scale,
        }
      : { x: vx, y: vy, width: vw, height: vh };
  return (
    <canvas
      ref={canvasRef}
      role={label === undefined ? undefined : "img"}
      aria-label={label}
      aria-hidden={label === undefined ? true : undefined}
      style={{
        position: "absolute",
        left: `${shown.x}px`,
        top: `${shown.y}px`,
        width: `${shown.width}px`,
        height: `${shown.height}px`,
      }}
    />
  );
}
