"use client";

// One PDF page drawn by PDF.js on a canvas, at the size the viewer asks for.
// With `view`, only that part of the page is drawn (a crop preview's frame):
// the canvas sits at the view's place on a page-size stage (canvasPlan.ts).
// Any failure (the file, the worker, a broken page) calls onFailed, and the
// viewer falls back to the stored page image. onRendered says the page is
// drawn, so the viewer can take its loading skeleton away. The document is
// held while it is on screen (pdfDocuments.ts), in an effect of its own: a
// new size or page redraws, and only a new document lets go of the old one.

import { useEffect, useRef } from "react";
import type { PDFDocumentProxy, RenderTask } from "pdfjs-dist";
import { canvasPlan } from "./canvasPlan";
import type { ViewRect } from "./canvasPlan";
import { holdPdf, releasePdf } from "./pdfDocuments";

export interface PdfPageProps {
  documentId: string;
  pageNo: number;
  width: number;
  height: number;
  /** The part of the page to draw, in CSS pixels on the page; the whole page if left out. */
  view?: ViewRect;
  label: string;
  onFailed: () => void;
  onRendered?: () => void;
}

export default function PdfPage({
  documentId,
  pageNo,
  width,
  height,
  view,
  label,
  onFailed,
  onRendered,
}: PdfPageProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const pdfRef = useRef<Promise<PDFDocumentProxy> | null>(null);
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

  useEffect(() => {
    let cancelled = false;
    let task: RenderTask | null = null;
    const draw = async () => {
      const held = pdfRef.current;
      if (!held) throw new Error("no PDF held");
      const pdf = await held;
      if (cancelled) return;
      const page = await pdf.getPage(pageNo);
      const canvas = canvasRef.current;
      if (cancelled || !canvas) return;
      const natural = page.getViewport({ scale: 1 });
      const viewport = page.getViewport({ scale: width / natural.width });
      const rect = { x: vx, y: vy, width: vw, height: vh };
      const plan = canvasPlan(rect, window.devicePixelRatio, isCrop);
      canvas.width = plan.width;
      canvas.height = plan.height;
      task = page.render({ canvas, viewport, transform: plan.transform });
      await task.promise;
      if (!cancelled) onRendered?.();
    };
    draw().catch((error: unknown) => {
      const name = error instanceof Error ? error.name : "";
      if (!cancelled && name !== "RenderingCancelledException") onFailed();
    });
    return () => {
      cancelled = true;
      task?.cancel();
    };
  }, [documentId, pageNo, width, vx, vy, vw, vh, isCrop, onFailed, onRendered]);

  return (
    <canvas
      ref={canvasRef}
      role="img"
      aria-label={label}
      style={{
        position: "absolute",
        left: `${vx}px`,
        top: `${vy}px`,
        width: `${vw}px`,
        height: `${vh}px`,
      }}
    />
  );
}
