"use client";

// Shows one page of a document with its highlighted spots: a PDF page drawn by
// PDF.js, a stored page image, or a spreadsheet's cell grid. With `crop`, it
// shows only that spot (plus a margin), enlarged: the review queue and the
// answer previews use this. Highlight boxes are page fractions, placed with
// lib/regions.ts, so they land on the same spot at any zoom.

import dynamic from "next/dynamic";
import Image from "next/image";
import { useCallback, useState } from "react";
import type { ReactNode } from "react";
import { pageImageUrl } from "@/lib/api";
import { boxToPixels, cropRect, cropView, pageAspect, regionsOnPage } from "@/lib/regions";
import type { BoxRegion } from "@/lib/regions";
import type { DocumentSummary, PageSummary, Region } from "@/lib/types";
import { SheetGrid } from "./SheetGrid";
import { pageMode, stepZoom } from "./viewer";
import styles from "./PageViewer.module.css";

// PDF.js needs the browser, so its component never renders on the server.
const PdfPage = dynamic(() => import("./PdfPage"), {
  ssr: false,
  loading: () => <p className="muted">Loading the page…</p>,
});

const PAGE_WIDTH = 720; // CSS pixels at 100%
const CROP_WIDTH = 440; // the frame a crop is shown in
const MAX_CROP_PAGE_WIDTH = 2400; // a tiny spot is not enlarged beyond this

export type ViewerDocument = Pick<DocumentSummary, "id" | "display_name" | "detected_type">;

export interface PageViewerProps {
  document: ViewerDocument;
  page: PageSummary;
  highlights: Region[];
  crop?: Region;
  /** With both of these, the viewer shows previous and next page buttons. */
  pageNumbers?: number[];
  onPageChange?: (pageNo: number) => void;
}

export function PageViewer({
  document,
  page,
  highlights,
  crop,
  pageNumbers,
  onPageChange,
}: PageViewerProps) {
  const pageKey = `${document.id}:${page.page_no}`;
  const [pdfFailedFor, setPdfFailedFor] = useState<string | null>(null);
  const [imageFailedFor, setImageFailedFor] = useState<string | null>(null);
  const [zoom, setZoom] = useState(1);
  const onPdfFailed = useCallback(() => setPdfFailedFor(pageKey), [pageKey]);

  const onThisPage = regionsOnPage(highlights, page.page_no);
  const cropHere = crop && crop.page === page.page_no ? crop : undefined;
  let mode = pageMode(document, page, pdfFailedFor === pageKey);
  if (mode === "image" && imageFailedFor === pageKey) mode = "none";
  const label = `Page ${page.page_no} of ${document.display_name}`;

  let body: ReactNode;
  if (mode === "sheet") {
    body = (
      <SheetGrid
        documentId={document.id}
        page={page}
        highlights={onThisPage}
        crop={cropHere?.kind === "cell" ? cropHere : undefined}
      />
    );
  } else if (mode === "none") {
    body = <p className="muted">This page can&apos;t be shown.</p>;
  } else {
    const aspect = pageAspect(page);
    const boxes = onThisPage.filter((r): r is BoxRegion => r.kind === "box");
    const rect = cropHere ? cropRect(cropHere, page) : null;
    const view = rect ? cropView(rect, aspect, CROP_WIDTH, MAX_CROP_PAGE_WIDTH) : null;
    const width = view ? view.pageWidth : PAGE_WIDTH * zoom;
    const height = view ? view.pageHeight : width / aspect;
    const surface =
      mode === "pdf" ? (
        <PdfPage
          documentId={document.id}
          pageNo={page.page_no}
          width={width}
          height={height}
          label={label}
          onFailed={onPdfFailed}
        />
      ) : (
        <Image
          src={pageImageUrl(document.id, page.page_no)}
          alt={label}
          fill
          unoptimized
          sizes={`${Math.round(width)}px`}
          onError={() => setImageFailedFor(pageKey)}
        />
      );
    const stage = (
      <div
        className={styles.stage}
        style={{
          width: `${width}px`,
          height: `${height}px`,
          ...(view ? { position: "absolute", left: -view.offsetX, top: -view.offsetY } : {}),
        }}
      >
        {surface}
        {boxes.map((region, index) => {
          const px = boxToPixels(region.box, width, height);
          return (
            <div
              key={index}
              className={styles.box}
              aria-hidden="true"
              style={{ left: px.left, top: px.top, width: px.width, height: px.height }}
            />
          );
        })}
      </div>
    );
    body = view ? (
      <figure className={styles.cropFigure}>
        <div
          className={styles.cropFrame}
          style={{ width: `${view.frameWidth}px`, height: `${view.frameHeight}px` }}
        >
          {stage}
        </div>
        <figcaption className="muted">Zoomed in on the highlighted spot, page {page.page_no}.</figcaption>
      </figure>
    ) : (
      <>
        <div className={styles.scroll}>{stage}</div>
        {boxes.length > 0 ? (
          <p className="muted">
            {boxes.length === 1
              ? "The highlighted box marks the spot on this page."
              : `${boxes.length} highlighted boxes mark the spots on this page.`}
          </p>
        ) : null}
      </>
    );
  }

  if (crop) return <div className={styles.viewer}>{body}</div>;

  const numbers = pageNumbers ?? [];
  const at = numbers.indexOf(page.page_no);
  const previous = at > 0 ? numbers[at - 1] : undefined;
  const next = at >= 0 && at < numbers.length - 1 ? numbers[at + 1] : undefined;
  const canZoom = mode === "pdf" || mode === "image";

  return (
    <div className={styles.viewer}>
      <div className={`row ${styles.toolbar}`}>
        {onPageChange && numbers.length > 0 ? (
          <div className="row" role="group" aria-label="Pages">
            <button
              type="button"
              className="btn"
              disabled={previous === undefined}
              onClick={() => previous !== undefined && onPageChange(previous)}
            >
              Previous page
            </button>
            <span aria-live="polite">
              Page {page.page_no} of {numbers.length}
            </span>
            <button
              type="button"
              className="btn"
              disabled={next === undefined}
              onClick={() => next !== undefined && onPageChange(next)}
            >
              Next page
            </button>
          </div>
        ) : null}
        {canZoom ? (
          <div className="row" role="group" aria-label="Zoom">
            <button
              type="button"
              className="btn"
              onClick={() => setZoom((z) => stepZoom(z, "out"))}
              disabled={zoom <= 0.5}
            >
              Zoom out
            </button>
            <span aria-live="polite">{Math.round(zoom * 100)}%</span>
            <button
              type="button"
              className="btn"
              onClick={() => setZoom((z) => stepZoom(z, "in"))}
              disabled={zoom >= 3}
            >
              Zoom in
            </button>
          </div>
        ) : null}
      </div>
      {body}
    </div>
  );
}
