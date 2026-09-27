"use client";

// One PDF page drawn by PDF.js on a canvas, at the size the viewer asks for.
// Any failure (the file, the worker, a broken page) calls onFailed, and the
// viewer falls back to the stored page image. onRendered says the page is
// drawn, so the viewer can take its loading skeleton away.

import { useEffect, useRef } from "react";
import type { RenderTask } from "pdfjs-dist";
import { loadPdf } from "./pdfDocuments";

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

  useEffect(() => {
    let cancelled = false;
    let task: RenderTask | null = null;
    const draw = async () => {
      const pdf = await loadPdf(documentId);
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
