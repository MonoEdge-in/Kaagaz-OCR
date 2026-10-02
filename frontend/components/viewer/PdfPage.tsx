"use client";

// One PDF page drawn by PDF.js on a canvas, at the size the viewer asks for.
// Any failure (the file, the worker, a broken page) calls onFailed, and the
// viewer falls back to the stored page image. onRendered says the page is
// drawn, so the viewer can take its loading skeleton away. The document is
// held while it is on screen (pdfDocuments.ts), in an effect of its own: a
// new size or page redraws, and only a new document lets go of the old one.

import { useEffect, useRef } from "react";
import type { PDFDocumentProxy, RenderTask } from "pdfjs-dist";
import { holdPdf, releasePdf } from "./pdfDocuments";

// Sharp on high-density screens, without huge canvases (browsers cap their size).
const MAX_PIXEL_RATIO = 2;
const MAX_CANVAS_WIDTH = 4096;

export interface PdfPageProps {
  documentId: string;
  pageNo: number;
  width: number;
  height: number;
  label: string;
  onFailed: () => void;
  onRendered?: () => void;
}

export default function PdfPage({
  documentId,
  pageNo,
  width,
  height,
  label,
  onFailed,
  onRendered,
}: PdfPageProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const pdfRef = useRef<Promise<PDFDocumentProxy> | null>(null);

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
      const ratio = Math.min(window.devicePixelRatio || 1, MAX_PIXEL_RATIO, MAX_CANVAS_WIDTH / width);
      const viewport = page.getViewport({ scale: (width / natural.width) * ratio });
      canvas.width = Math.floor(viewport.width);
      canvas.height = Math.floor(viewport.height);
      task = page.render({ canvas, viewport });
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
  }, [documentId, pageNo, width, onFailed, onRendered]);

  return (
    <canvas
      ref={canvasRef}
      role="img"
      aria-label={label}
      style={{ width: `${width}px`, height: `${height}px`, display: "block" }}
    />
  );
}
