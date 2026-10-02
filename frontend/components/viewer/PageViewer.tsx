"use client";

// Shows one page of a document with its highlighted spots: a PDF page drawn by
// PDF.js, a stored page image, or a spreadsheet's cell grid. With `crop`, it
// shows only that spot (plus a margin), enlarged: the review queue and the
// answer previews use this. Highlight boxes are page fractions, placed with
// lib/regions.ts, so they land on the same spot at any size.
//
// The full page fits the width of its container (measured with a
// ResizeObserver) and zooms from there; beside its number it says how the
// page was read (PRD 2, R1). A crop preview fills its reserved box
// without clipping the spot, and is drawn only once it comes near the screen
// (IntersectionObserver); until then a skeleton holds its space.

import dynamic from "next/dynamic";
import Image from "next/image";
import { useCallback, useState } from "react";
import type { ReactNode } from "react";
import { IconButton } from "@/components/ui/IconButton";
import { pageImageUrl } from "@/lib/api";
import { boxToPixels, cropRect, fitCropView, pageAspect, regionsOnPage } from "@/lib/regions";
import type { BoxRegion, CropView, FractionRect } from "@/lib/regions";
import type { DocumentSummary, PageSummary, Region } from "@/lib/types";
import { SheetGrid } from "./SheetGrid";
import { useObservedBox } from "./useObservedBox";
import { ZOOM_STEPS, fitPageSize, pageMode, routeText, stepZoom } from "./viewer";
import styles from "./PageViewer.module.css";

// PDF.js needs the browser, so its component never renders on the server.
const PdfPage = dynamic(() => import("./PdfPage"), {
  ssr: false,
  loading: () => <span className={`skeleton ${styles.fill}`} aria-hidden="true" />,
});

const MAX_CROP_PAGE_WIDTH = 2400; // a tiny spot is not enlarged beyond this
const WHOLE_PAGE: FractionRect = { x0: 0, y0: 0, x1: 1, y1: 1 };
const MIN_ZOOM = ZOOM_STEPS[0];
const MAX_ZOOM = ZOOM_STEPS[ZOOM_STEPS.length - 1] ?? 3;

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
  const isCrop = crop !== undefined;
  const { ref: measureRef, box, near } = useObservedBox(isCrop);
  const [attempt, setAttempt] = useState(0);
  const pageKey = `${document.id}:${page.page_no}:${attempt}`;
  const [pdfFailedFor, setPdfFailedFor] = useState<string | null>(null);
  const [imageFailedFor, setImageFailedFor] = useState<string | null>(null);
  const [drawnFor, setDrawnFor] = useState<string | null>(null);
  const [zoom, setZoom] = useState(1);
  const onPdfFailed = useCallback(() => setPdfFailedFor(pageKey), [pageKey]);

  const pdfFailed = pdfFailedFor === pageKey;
  const imageFailed = imageFailedFor === pageKey;
  let mode = pageMode(document, page, pdfFailed);
  if (mode === "image" && imageFailed) mode = "none";
  const drawnKey = `${pageKey}:${mode}`;
  const onDrawn = useCallback(() => setDrawnFor(drawnKey), [drawnKey]);
  const drawn = drawnFor === drawnKey;

  const onThisPage = regionsOnPage(highlights, page.page_no);
  const cropHere = crop && crop.page === page.page_no ? crop : undefined;
  const label = `Page ${page.page_no} of ${document.display_name}`;
  const aspect = pageAspect(page);
  const boxes = onThisPage.filter((r): r is BoxRegion => r.kind === "box");

  const failed = (
    <div className={styles.failed} role="status">
      <p>This page could not be shown.</p>
      {pdfFailed || imageFailed ? (
        <button type="button" className="btn" onClick={() => setAttempt((n) => n + 1)}>
          Try again
        </button>
      ) : null}
    </div>
  );

  /**
   * The drawn page with its highlight boxes, at width x height pixels. With a
   * crop, the page-size stage is shifted into the crop's frame, and a PDF draws
   * only that frame (a page-size canvas would be some 50 times bigger).
   */
  const stage = (width: number, height: number, crop?: CropView) => (
    <div
      className={styles.stage}
      style={{
        width: `${width}px`,
        height: `${height}px`,
        ...(crop ? { position: "absolute", left: -crop.offsetX, top: -crop.offsetY } : {}),
      }}
    >
      {mode === "pdf" ? (
        <PdfPage
          key={pageKey}
          documentId={document.id}
          pageNo={page.page_no}
          width={width}
          height={height}
          view={
            crop
              ? { x: crop.offsetX, y: crop.offsetY, width: crop.frameWidth, height: crop.frameHeight }
              : undefined
          }
          label={label}
          onFailed={onPdfFailed}
          onRendered={onDrawn}
        />
      ) : (
        <Image
          key={pageKey}
          src={pageImageUrl(document.id, page.page_no)}
          alt={label}
          fill
          unoptimized
          sizes={`${Math.round(width)}px`}
          onLoad={onDrawn}
          onError={() => setImageFailedFor(pageKey)}
        />
      )}
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

  // --- a crop preview: review cards and answer source cards ------------------

  if (isCrop) {
    let preview: ReactNode;
    if (!near) {
      preview = <span className={`skeleton ${styles.fill}`} aria-hidden="true" />;
    } else if (mode === "sheet") {
      preview = (
        <SheetGrid
          documentId={document.id}
          page={page}
          highlights={onThisPage}
          crop={cropHere?.kind === "cell" ? cropHere : undefined}
        />
      );
    } else if (mode === "none") {
      preview = failed;
    } else if (!box || box.width < 1 || box.height < 1) {
      preview = <span className={`skeleton ${styles.fill}`} aria-hidden="true" />;
    } else {
      const rect = (cropHere ? cropRect(cropHere, page) : null) ?? WHOLE_PAGE;
      const view = fitCropView(rect, aspect, box.width, box.height, MAX_CROP_PAGE_WIDTH);
      preview = (
        <>
          <div
            className={styles.cropFrame}
            style={{ width: `${view.frameWidth}px`, height: `${view.frameHeight}px` }}
          >
            {stage(view.pageWidth, view.pageHeight, view)}
          </div>
          {drawn ? null : <span className={`skeleton ${styles.fill}`} aria-hidden="true" />}
        </>
      );
    }
    return (
      <figure className={styles.cropFigure}>
        <div ref={measureRef} className={mode === "sheet" ? styles.cropSheetBox : styles.cropBox}>
          {preview}
        </div>
        <figcaption className="visually-hidden">
          Zoomed in on the highlighted spot, page {page.page_no}.
        </figcaption>
      </figure>
    );
  }

  // --- the full page: the document screen ----------------------------------

  let body: ReactNode;
  if (mode === "sheet") {
    body = <SheetGrid documentId={document.id} page={page} highlights={onThisPage} />;
  } else if (mode === "none") {
    body = <div className={styles.frame}>{failed}</div>;
  } else {
    const size = box ? fitPageSize(box.width, aspect, zoom) : null;
    body = (
      <>
        <div
          ref={measureRef}
          className={styles.frame}
          tabIndex={0}
          role="region"
          aria-label={label}
        >
          {size ? (
            <div className={styles.stageWrap}>
              {stage(size.width, size.height)}
              {drawn ? null : <span className={`skeleton ${styles.fill}`} aria-hidden="true" />}
            </div>
          ) : (
            <span
              className={`skeleton ${styles.pageSkeleton}`}
              aria-hidden="true"
              style={{ aspectRatio: String(aspect) }}
            />
          )}
        </div>
        {boxes.length > 0 ? (
          <p className={`muted ${styles.note}`}>
            {boxes.length === 1
              ? "The highlighted box marks the spot on this page."
              : `${boxes.length} highlighted boxes mark the spots on this page.`}
          </p>
        ) : null}
      </>
    );
  }

  const numbers = pageNumbers ?? [];
  const at = numbers.indexOf(page.page_no);
  const previous = at > 0 ? numbers[at - 1] : undefined;
  const next = at >= 0 && at < numbers.length - 1 ? numbers[at + 1] : undefined;
  const canZoom = mode === "pdf" || mode === "image";
  const showPages = onPageChange !== undefined && numbers.length > 0;
  const route = routeText(page.route);

  return (
    <div className={styles.viewer}>
      {showPages || canZoom ? (
        <div className={styles.toolbar}>
          {showPages ? (
            <div className={styles.pageInfo}>
              <div className={styles.group} role="group" aria-label="Pages">
                <IconButton
                  icon="chevron-left"
                  label="Previous page"
                  disabled={previous === undefined}
                  onClick={() => previous !== undefined && onPageChange?.(previous)}
                />
                <span className={`tabular ${styles.readout}`} aria-live="polite">
                  Page {page.page_no} of {numbers.length}
                </span>
                <IconButton
                  icon="chevron-right"
                  label="Next page"
                  disabled={next === undefined}
                  onClick={() => next !== undefined && onPageChange?.(next)}
                />
              </div>
              {route !== null && <span className={styles.route}>{route}</span>}
            </div>
          ) : null}
          {canZoom ? (
            <div className={styles.group} role="group" aria-label="Zoom">
              <IconButton
                icon="zoom-out"
                label="Zoom out"
                onClick={() => setZoom((z) => stepZoom(z, "out"))}
                disabled={zoom <= MIN_ZOOM}
              />
              <span className={`tabular ${styles.readout}`} aria-live="polite">
                {zoom === 1 ? "Fit width" : `${Math.round(zoom * 100)}%`}
              </span>
              <IconButton
                icon="zoom-in"
                label="Zoom in"
                onClick={() => setZoom((z) => stepZoom(z, "in"))}
                disabled={zoom >= MAX_ZOOM}
              />
              {zoom !== 1 ? (
                <button type="button" className="btn btn-quiet" onClick={() => setZoom(1)}>
                  Fit width
                </button>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}
      {body}
    </div>
  );
}
