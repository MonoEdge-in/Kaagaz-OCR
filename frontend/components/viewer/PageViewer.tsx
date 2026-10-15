"use client";

// Shows one page of a document with its highlighted spots: a PDF page drawn by
// PDF.js, a stored page image, or a spreadsheet's cell grid. With `crop`, it
// shows only that spot (plus a margin), enlarged: the review queue and the
// answer previews use this. Highlight boxes are page fractions, placed with
// lib/regions.ts, so they land on the same spot at any size.
//
// The full page fits the width of its container (measured with a
// ResizeObserver), or with fit="page" the whole page inside the container
// (the document screen's sticky column), and zooms from there. Beside its
// number a blank page says so. Zoomed in, a PDF page is drawn twice: the whole
// page, softer, and on top of it a sharp drawing of only the part on screen,
// redrawn 150ms after scrolling stops (visibleRect). The user's own words
// (marks) are dashed, numbered outlines, drawn on the full page only. A crop
// preview fills its reserved box without clipping the spot, and is drawn only
// once it comes near the screen (IntersectionObserver); until then a skeleton
// holds its space. Reading problems (PRD 5 U3, readingMarks.ts) are a second
// layer of marks on the full page, each a button with its own pattern, with a
// legend and a "Show reading problems" checkbox, on at first for an uncertain or
// unreadable page. A highlighted spot is zoomed to readable size (focusZoom)
// and scrolled to the middle of the frame, then into view, with a short pulse.

import dynamic from "next/dynamic";
import Image from "next/image";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { IconButton } from "@/components/ui/IconButton";
import { markerId } from "@/components/words/ViewerWords";
import { markerRect } from "@/components/words/spots";
import type { Marker } from "@/components/words/spots";
import { pageImageUrl } from "@/lib/api";
import { boxToPixels, cropRect, fitCropView, pageAspect, regionsOnPage } from "@/lib/regions";
import type { BoxRegion, CropView, FractionRect } from "@/lib/regions";
import type { DocumentSummary, PageSummary, ReadingMark, Region } from "@/lib/types";
import { MARK_WORDS, legend, markName, markRect, marksOnPage, showByDefault } from "./readingMarks";
import { SheetGrid } from "./SheetGrid";
import { useObservedBox } from "./useObservedBox";
import type { ViewRect } from "./canvasPlan";
import { ZOOM_STEPS, centerScroll, fitPageSize, focusZoom, pageMode, stepZoom, visibleRect } from "./viewer";
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
const ZOOMED_BUDGET = 4e6; // device pixels for the soft whole page under the sharp overlay
const MEASURE_DELAY_MS = 150; // the sharp overlay waits until scrolling stops
const IGNORE = () => undefined; // the overlay's failures: the whole page stays

export type ViewerDocument = Pick<DocumentSummary, "id" | "display_name" | "detected_type">;

export interface PageViewerProps {
  document: ViewerDocument;
  page: PageSummary;
  highlights: Region[];
  crop?: Region;
  /** With both of these, the viewer shows previous and next page buttons. */
  pageNumbers?: number[];
  onPageChange?: (pageNo: number) => void;
  /** More buttons at the end of the toolbar, and what they open, right below it. */
  tools?: ReactNode;
  belowToolbar?: ReactNode;
  /** My words on this page (ViewerWords.tsx) and the one its list marked. */
  marks?: Marker[];
  activeMark?: string | null;
  /** "page": fill the parent's height and fit the whole page in it; "width" (default): fit the width. */
  fit?: "page" | "width";
  /** PRD 5 U3: the page's reading problems, and what a mark does when pressed (with its name). */
  readingMarks?: ReadingMark[];
  onMark?: (mark: ReadingMark, name: string) => void;
}

export function PageViewer({
  document,
  page,
  highlights,
  crop,
  pageNumbers,
  onPageChange,
  tools,
  belowToolbar,
  marks = [],
  activeMark = null,
  fit = "width",
  readingMarks,
  onMark,
}: PageViewerProps) {
  const isCrop = crop !== undefined;
  const { ref: measureRef, box, near } = useObservedBox(isCrop);
  const [attempt, setAttempt] = useState(0);
  const pageKey = `${document.id}:${page.page_no}:${attempt}`;
  const [pdfFailedFor, setPdfFailedFor] = useState<string | null>(null);
  const [imageFailedFor, setImageFailedFor] = useState<string | null>(null);
  const [drawnFor, setDrawnFor] = useState<string | null>(null);
  // The zoom chosen for this spot (or no spot); until then, the spot's own (focusZoom).
  const [zoomChoice, setZoomChoice] = useState<{ key: string | null; zoom: number } | null>(null);
  const frameRef = useRef<HTMLDivElement | null>(null);
  const spotRef = useRef<HTMLDivElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  // The part of the zoomed page last measured on screen, and the page and zoom it was measured at.
  const [seen, setSeen] = useState<{ key: string; rect: ViewRect } | null>(null);
  const shownSpot = useRef<string | null>(null); // the spot last brought into the window's view
  const centred = useRef<string | null>(null); // the spot and zoom last centred in the frame
  const frameMeasured = useCallback(
    (element: HTMLDivElement | null) => {
      frameRef.current = element;
      const stop = measureRef(element);
      return () => {
        frameRef.current = null;
        stop?.();
      };
    },
    [measureRef],
  );
  const onPdfFailed = useCallback(() => setPdfFailedFor(pageKey), [pageKey]);
  // "Show reading problems", chosen on this page; until then, on for a page with problems.
  const [problemsChoice, setProblemsChoice] = useState<{ key: string; on: boolean } | null>(null);

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
  const problems = isCrop ? [] : marksOnPage(readingMarks, page.page_no);
  const marksKey = `${document.id}:${page.page_no}`;
  const showProblems =
    problemsChoice && problemsChoice.key === marksKey ? problemsChoice.on : showByDefault(page);
  const spot = isCrop ? undefined : boxes[0];
  const spotKey = spot ? `${marksKey}:${spot.box.join(",")}` : null;
  let spotZoom = 1;
  if (spot && box && (mode === "pdf" || mode === "image")) {
    const fitted = fitPageSize(box.width, aspect, 1, fit === "page" ? box.height : undefined);
    const frame = { width: box.width, height: fit === "page" ? box.height : Infinity };
    spotZoom = focusZoom(boxToPixels(spot.box, fitted.width, fitted.height), frame);
  }
  const zoom = zoomChoice && zoomChoice.key === spotKey ? zoomChoice.zoom : spotZoom;
  const setZoom = (next: number) => setZoomChoice({ key: spotKey, zoom: next });

  // A new spot or zoom: the spot to the middle of the frame (a size change alone
  // keeps the user's own scrolling); a new spot also into the window's view (a
  // zoom button never scrolls the window).
  useLayoutEffect(() => {
    const frame = frameRef.current;
    const marked = spotRef.current;
    if (!frame || !marked || centred.current === `${spotKey}@${zoom}`) return;
    centred.current = `${spotKey}@${zoom}`;
    const f = frame.getBoundingClientRect();
    const m = marked.getBoundingClientRect();
    frame.scrollTo(
      centerScroll(
        {
          left: m.left - f.left - frame.clientLeft + frame.scrollLeft,
          top: m.top - f.top - frame.clientTop + frame.scrollTop,
          width: m.width,
          height: m.height,
        },
        { width: frame.scrollWidth, height: frame.scrollHeight },
        { width: frame.clientWidth, height: frame.clientHeight },
      ),
    );
    if (shownSpot.current === spotKey) return;
    shownSpot.current = spotKey;
    marked.scrollIntoView({ block: fit === "page" ? "nearest" : "center", inline: "nearest" });
  }, [spotKey, zoom, box, fit]);

  const size = box ? fitPageSize(box.width, aspect, zoom, fit === "page" ? box.height : undefined) : null;
  // Zoomed in on a PDF page: measure what shows 150ms after the last scroll or
  // resize (no scrollend: too new), for the sharp overlay. A rect measured at
  // another page, zoom or size is never drawn.
  const sharpKey =
    !isCrop && mode === "pdf" && zoom > 1 && size ? `${pageKey}@${zoom}:${size.width}x${size.height}` : null;
  useEffect(() => {
    const frame = frameRef.current;
    if (sharpKey === null || !frame) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const measure = () => {
      const stage = stageRef.current;
      if (!stage) return;
      const rect = visibleRect(stage.getBoundingClientRect(), frame.getBoundingClientRect(), {
        left: 0,
        top: 0,
        right: window.innerWidth,
        bottom: window.innerHeight,
      });
      setSeen(rect ? { key: sharpKey, rect } : null);
    };
    const later = () => {
      clearTimeout(timer);
      timer = setTimeout(measure, MEASURE_DELAY_MS);
    };
    later();
    frame.addEventListener("scroll", later, { passive: true });
    window.addEventListener("scroll", later, { passive: true });
    window.addEventListener("resize", later, { passive: true });
    return () => {
      clearTimeout(timer);
      frame.removeEventListener("scroll", later);
      window.removeEventListener("scroll", later);
      window.removeEventListener("resize", later);
    };
  }, [sharpKey]);
  const sharp = seen && seen.key === sharpKey ? seen.rect : null;

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
      ref={crop ? undefined : stageRef}
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
          budget={zoom > 1 ? ZOOMED_BUDGET : undefined}
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
      {sharp ? (
        <PdfPage
          key={`${pageKey}:sharp`}
          documentId={document.id}
          pageNo={page.page_no}
          width={width}
          height={height}
          view={sharp}
          onFailed={IGNORE}
        />
      ) : null}
      {boxes.map((region, index) => {
        const px = markerRect(region.box, width, height);
        return (
          <div
            key={`${spotKey}:${index}`}
            ref={index === 0 && !crop ? spotRef : undefined}
            className={crop ? styles.box : `${styles.box} ${styles.pulse}`}
            aria-hidden="true"
            style={{ left: px.left, top: px.top, width: px.width, height: px.height }}
          />
        );
      })}
      {crop
        ? null
        : marks.map((mark) => {
            if (mark.region.kind !== "box") return null;
            const px = markerRect(mark.region.box, width, height);
            return (
              <div
                key={mark.id}
                id={markerId(mark.id)}
                className={mark.id === activeMark ? `${styles.mine} ${styles.mineActive}` : styles.mine}
                aria-hidden="true"
                style={{ left: px.left, top: px.top, width: px.width, height: px.height }}
              >
                <span className={styles.mineNumber}>{mark.n}</span>
              </div>
            );
          })}
      {crop || !showProblems
        ? null
        : problems.map((mark, index) => {
            const px = markRect(mark, width, height);
            if (!px) return null;
            const name = markName(mark, index, problems.length);
            return (
              <button
                key={index}
                type="button"
                className={styles.problem}
                data-quality={mark.quality}
                aria-label={name}
                title={name}
                style={{ left: px.left, top: px.top, width: px.width, height: px.height }}
                onClick={() => onMark?.(mark, name)}
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
    body = <SheetGrid documentId={document.id} page={page} highlights={onThisPage} marks={marks} activeMark={activeMark} />;
  } else if (mode === "none") {
    body = <div className={styles.frame}>{failed}</div>;
  } else {
    body = (
      <>
        <div
          ref={frameMeasured}
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
  // On a phone only "Fit" shows; the whole name is still read out.
  const fitName = <>Fit<span className="narrow-hide">{fit === "page" ? " page" : " width"}</span></>;

  return (
    <div className={fit === "page" ? `${styles.viewer} ${styles.fitPage}` : styles.viewer}>
      {showPages || canZoom || tools ? (
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
              {page.route === "blank" && <span className={styles.route}>Blank page</span>}
            </div>
          ) : null}
          {canZoom ? (
            <div className={styles.group} role="group" aria-label="Zoom">
              <IconButton
                icon="zoom-out"
                label="Zoom out"
                onClick={() => setZoom(stepZoom(zoom, "out"))}
                disabled={zoom <= MIN_ZOOM}
              />
              <span className={`tabular ${styles.readout}`} aria-live="polite">
                {zoom === 1 ? fitName : `${Math.round(zoom * 100)}%`}
              </span>
              <IconButton
                icon="zoom-in"
                label="Zoom in"
                onClick={() => setZoom(stepZoom(zoom, "in"))}
                disabled={zoom >= MAX_ZOOM}
              />
              {zoom !== 1 ? (
                <button type="button" className="btn btn-quiet" onClick={() => setZoom(1)}>
                  {fitName}
                </button>
              ) : null}
            </div>
          ) : null}
          {tools}
        </div>
      ) : null}
      {belowToolbar}
      {problems.length > 0 && canZoom ? (
        <div className={styles.problemBar}>
          <label className={styles.problemToggle}>
            <input
              type="checkbox"
              checked={showProblems}
              onChange={() => setProblemsChoice({ key: marksKey, on: !showProblems })}
            />
            Show reading problems
          </label>
          {showProblems ? (
            <ul className={styles.legend} aria-label="What the marks mean">
              {legend(problems).map((quality) => (
                <li key={quality}>
                  <span className={styles.swatch} data-quality={quality} aria-hidden="true" />
                  {MARK_WORDS[quality].legend}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
      {body}
    </div>
  );
}
