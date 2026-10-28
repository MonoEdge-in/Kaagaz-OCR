"use client";

// Shows one page of a document with its highlighted spots: a PDF page drawn by
// PDF.js, a stored page image, or a spreadsheet's cell grid. With `crop`, it
// shows only that spot (plus a margin), enlarged: the review queue and the
// answer previews use this. Highlight boxes are page fractions, placed with
// lib/regions.ts, so they land on the same spot at any size.
//
// The full page fits the width of its container (measured with a
// ResizeObserver) and zooms from there. With `fill` (the document screen's sticky
// column) the viewer fills its parent's height, and a toggle fits the whole page
// in it instead (UI audit DD5, 16 Oct 2026). Ctrl or Cmd with the wheel zooms; the
// plain wheel scrolls the page, on into <main>, never trapped (DD1). Beside its
// number a blank page says so. Zoomed in, a PDF page is drawn twice: the whole
// page, softer, and on top of it a sharp drawing of only the part on screen,
// redrawn 150ms after scrolling stops (visibleRect). The user's own words
// (marks) are dashed, numbered outlines, drawn on the full page only. A crop
// preview fills its reserved box without clipping the spot, and is drawn only
// once it comes near the screen (IntersectionObserver); until then a skeleton
// holds its space. Reading problems (PRD 5 U3, readingMarks.ts) are a second
// layer of marks on the full page, each a button with its own pattern, with a
// "Show reading problems" toggle in the toolbar (its legend in a tooltip, DD9), on at
// first for an uncertain or unreadable page. A highlighted spot is zoomed to readable size (focusZoom)
// and scrolled to the middle of the frame, then into view, with a short pulse. Words
// flagged for review (scan review UX rules, section 2) are a third layer: each a button
// with its band's underline and glyph ("?" Check, "!" Review) and its words as its name.
// The lines holding your words (package H, YourWords.tsx) are drawn under the spot.

import dynamic from "next/dynamic";
import Image from "next/image";
import { useCallback, useEffect, useEffectEvent, useId, useLayoutEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Icon } from "@/components/ui/Icon";
import { IconButton } from "@/components/ui/IconButton";
import { markerId } from "@/components/words/ViewerWords";
import { markerRect } from "@/components/words/spots";
import type { Marker } from "@/components/words/spots";
import { flagDomId, markLabel } from "@/components/review/wordReview";
import { pageImageUrl } from "@/lib/api";
import { FADE_IN, MOTION_MS, play } from "@/lib/motion";
import { boxToPixels, cropRect, framedCropView, pageAspect, regionsOnPage } from "@/lib/regions";
import type { BoxRegion, CropView, FractionRect } from "@/lib/regions";
import type { DocumentSummary, PageSummary, ReadingMark, Region, WordFlag } from "@/lib/types";
import { MARK_WORDS, legend, markChecked, markName, markRect, marksOnPage, showByDefault } from "./readingMarks";
import { SheetGrid } from "./SheetGrid";
import { useObservedBox } from "./useObservedBox";
import type { ViewRect } from "./canvasPlan";
import { ZOOM_STEPS, centerScroll, fitPageSize, focusZoom, pageMode, stepZoom, visibleRect, wheelZoom } from "./viewer";
import styles from "./PageViewer.module.css";
import { wordColour } from "./YourWords";

// PDF.js needs the browser, so its component never renders on the server.
const PdfPage = dynamic(() => import("./PdfPage"), {
  ssr: false,
  loading: () => <span className={`skeleton ${styles.fill}`} aria-hidden="true" />,
});

const MAX_CROP_PAGE_WIDTH = 2400; // a tiny spot is not enlarged beyond this
const CROP_MAX_HEIGHT = 176; // 11rem: the tallest a crop preview gets
// 8px of page around the highlight's 4px ring (UI audit R1), so it never touches or clips the text.
const CROP_MARGIN = 12;
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
  /** Fill the parent's height (the sticky column): fit width at first, with a Fit page toggle. */
  fill?: boolean;
  /** PRD 5 U3: the page's reading problems, and what a mark does when pressed (with its name). */
  readingMarks?: ReadingMark[];
  onMark?: (mark: ReadingMark, name: string) => void;
  /** Said under the page while a box is highlighted ("Highlighted: Grand total"); else how many boxes mark spots. */
  highlightNote?: string;
  /** DD7: the reading problem pointed at (its index on the page), here or in its list. */
  hotMark?: number | null;
  onHotMark?: (index: number | null) => void;
  /** Words still flagged for review on this page, the one being reviewed, and what a press does. */
  flags?: WordFlag[];
  activeFlag?: string | null;
  onFlag?: (flag: WordFlag) => void;
  /** A crop's tallest size (the review panel zooms its crop with it). */
  cropMaxHeight?: number;
  /** Package H: the lines holding your words, drawn under the spot on the full page. */
  wordBoxes?: Region[];
  /** For each of wordBoxes, its word (24 Oct 2026): the colour it is drawn in. */
  wordHits?: readonly number[];
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
  fill = false,
  readingMarks,
  onMark,
  highlightNote,
  hotMark = null,
  onHotMark,
  flags = [],
  activeFlag = null,
  onFlag,
  cropMaxHeight = CROP_MAX_HEIGHT,
  wordBoxes = [],
  wordHits = [],
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
  const [fitChoice, setFitChoice] = useState<"width" | "page">("width");
  const fit = fill ? fitChoice : "width";
  const legendId = useId();
  // "Show reading problems", chosen on this page; until then, on for a page with problems.
  const [problemsChoice, setProblemsChoice] = useState<{ key: string; on: boolean } | null>(null);

  const pdfFailed = pdfFailedFor === pageKey;
  const imageFailed = imageFailedFor === pageKey;
  let mode = pageMode(document, page, pdfFailed);
  if (mode === "image" && imageFailed) mode = "none";
  const drawnKey = `${pageKey}:${mode}`;
  const onDrawn = useCallback(() => setDrawnFor(drawnKey), [drawnKey]);
  const drawn = drawnFor === drawnKey;
  // Motion pass (16 Oct 2026): a page fades in once drawn, as its placeholder goes.
  useLayoutEffect(() => {
    if (drawn) play(stageRef.current, FADE_IN, MOTION_MS.med);
  }, [drawn]);

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
    const frame = { width: box.width, height: fill ? box.height : Infinity };
    spotZoom = focusZoom(boxToPixels(spot.box, fitted.width, fitted.height), frame);
  }
  const zoom = zoomChoice && zoomChoice.key === spotKey ? zoomChoice.zoom : spotZoom;
  const setZoom = (next: number) => setZoomChoice({ key: spotKey, zoom: next });

  // A zoom the user chose grows or shrinks the page from its old size (transform only;
  // the new size is already laid out). A spot's own zoom just shows.
  const shownZoom = useRef(zoom);
  useLayoutEffect(() => {
    const was = shownZoom.current;
    shownZoom.current = zoom;
    if (zoomChoice?.key !== spotKey || was === zoom) return;
    play(stageRef.current, [{ transform: `scale(${was / zoom})` }, { transform: "none" }], MOTION_MS.med);
  }, [zoom, zoomChoice, spotKey]);

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
    marked.scrollIntoView({ block: fill ? "nearest" : "center", inline: "nearest" });
  }, [spotKey, zoom, box, fill]);

  // A new page with no spot to show starts at its top-left, even zoomed in.
  const toPageStart = useEffectEvent(() => {
    if (!spotKey) frameRef.current?.scrollTo(0, 0);
  });
  useLayoutEffect(() => toPageStart(), [pageKey]);

  // Ctrl or Cmd with the wheel zooms the page, not the browser; any other wheel is left
  // alone (no preventDefault), so it scrolls the page and then <main> (DD1). Not passive,
  // or the browser's own zoom could not be held back.
  const onWheel = useEffectEvent((event: WheelEvent) => {
    const direction = wheelZoom(event);
    if (direction === null) return;
    event.preventDefault();
    setZoom(stepZoom(zoom, direction));
  });
  const wheelZooms = !isCrop && (mode === "pdf" || mode === "image");
  useEffect(() => {
    const frame = frameRef.current;
    if (!wheelZooms || !frame) return;
    const listener = (event: WheelEvent) => onWheel(event);
    frame.addEventListener("wheel", listener, { passive: false });
    return () => frame.removeEventListener("wheel", listener);
  }, [wheelZooms, pageKey]);

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
    // <main> is the app shell's one scroller (MonoEdge design system); the window too, for other pages.
    const main = window.document.getElementById("main");
    frame.addEventListener("scroll", later, { passive: true });
    main?.addEventListener("scroll", later, { passive: true });
    window.addEventListener("scroll", later, { passive: true });
    window.addEventListener("resize", later, { passive: true });
    return () => {
      clearTimeout(timer);
      frame.removeEventListener("scroll", later);
      main?.removeEventListener("scroll", later);
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
      {crop
        ? null
        : wordBoxes.map((region, index) => {
            if (region.kind !== "box" || region.page !== page.page_no) return null;
            const px = markerRect(region.box, width, height);
            return (
              <div
                key={`word-${index}`}
                className={styles.word}
                data-word={wordColour(wordHits[index])}
                aria-hidden="true"
                style={{ left: px.left, top: px.top, width: px.width, height: px.height }}
              />
            );
          })}
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
      {crop
        ? null
        : flags.map((flag) => {
            if (flag.region.kind !== "box" || flag.page_no !== page.page_no) return null;
            const px = markerRect(flag.region.box, width, height);
            const name = markLabel(flag);
            return (
              <button
                key={flag.word_key}
                id={flagDomId(flag.word_key)}
                type="button"
                className={styles.flag}
                data-band={flag.band}
                data-state={flag.state}
                aria-current={flag.word_key === activeFlag ? "true" : undefined}
                aria-label={name}
                title={name}
                style={{ left: px.left, top: px.top, width: px.width, height: px.height }}
                onClick={() => onFlag?.(flag)}
              />
            );
          })}
      {crop || !showProblems
        ? null
        : problems.map((mark, index) => {
            const px = markRect(mark, width, height);
            if (!px) return null;
            const checked = markChecked(mark, flags);
            const name = markName(mark, index, problems.length, checked);
            return (
              <button
                key={index}
                type="button"
                className={styles.problem}
                data-quality={mark.quality}
                data-state={checked ? "checked" : undefined}
                data-hot={hotMark === index ? "true" : undefined}
                aria-label={name}
                title={name}
                style={{ left: px.left, top: px.top, width: px.width, height: px.height }}
                onClick={() => onMark?.(mark, name)}
                onPointerEnter={() => onHotMark?.(index)}
                onPointerLeave={() => onHotMark?.(null)}
              />
            );
          })}
    </div>
  );

  // --- a crop preview: review cards and answer source cards ------------------

  if (isCrop) {
    let preview: ReactNode;
    if (mode === "sheet") {
      preview = near ? (
        <SheetGrid
          documentId={document.id}
          page={page}
          highlights={onThisPage}
          crop={cropHere?.kind === "cell" ? cropHere : undefined}
        />
      ) : (
        <span className={`skeleton ${styles.fill}`} aria-hidden="true" />
      );
    } else if (mode === "none") {
      preview = failed;
    } else if (!box || box.width < 1) {
      preview = <span className={`skeleton ${styles.fill}`} aria-hidden="true" />;
    } else {
      // The frame's size comes from the page's size and the spot alone, so it holds
      // its place before the page is drawn (or near the screen): nothing jumps.
      const rect = (cropHere ? cropRect(cropHere, page) : null) ?? WHOLE_PAGE;
      const view = framedCropView(rect, aspect, box.width, cropMaxHeight, MAX_CROP_PAGE_WIDTH, CROP_MARGIN);
      preview = (
        <>
          <div
            className={styles.cropFrame}
            style={{ width: `${view.frameWidth}px`, height: `${view.frameHeight}px` }}
          >
            {near ? stage(view.pageWidth, view.pageHeight, view) : null}
          </div>
          {near && drawn ? null : <span className={`skeleton ${styles.fill}`} aria-hidden="true" />}
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
    body = (
      <SheetGrid documentId={document.id} page={page} highlights={onThisPage} marks={marks} activeMark={activeMark} wordCells={wordBoxes} />
    );
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
            {highlightNote ??
              (boxes.length === 1
                ? "The highlighted box marks the spot on this page."
                : `${boxes.length} highlighted boxes mark the spots on this page.`)}
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
  // One span, so the button's gap never adds to the space before "page" / "width".
  const fitName = (
    <span>
      Fit<span className="narrow-hide">{fit === "page" ? " page" : " width"}</span>
    </span>
  );
  const otherFit = fit === "page" ? "width" : "page";

  return (
    <div className={fill ? `${styles.viewer} ${styles.fitPage}` : styles.viewer}>
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
                <span
                  className={`tabular ${styles.readout}`}
                  aria-live="polite"
                  style={{ minWidth: `${9 + 2 * String(numbers.length).length}ch` }}
                >
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
              {/* The readout is the way back to the fit (Tushar, 16 Oct 2026: no hidden button
                  holding room in the row). Its least width keeps the buttons beside it still. */}
              <button
                type="button"
                className={`btn btn-quiet tabular ${styles.readout}`}
                onClick={() => setZoom(1)}
                aria-live="polite"
                title={zoom === 1 ? undefined : `Back to fit ${fit}`}
              >
                {zoom === 1 ? fitName : `${Math.round(zoom * 100)}%`}
              </button>
              <IconButton
                icon="zoom-in"
                label="Zoom in"
                onClick={() => setZoom(stepZoom(zoom, "in"))}
                disabled={zoom >= MAX_ZOOM}
              />
              {fill ? (
                <button
                  type="button"
                  className="btn btn-quiet"
                  onClick={() => {
                    setFitChoice(otherFit);
                    setZoom(1);
                  }}
                >
                  Fit {otherFit}
                </button>
              ) : null}
            </div>
          ) : null}
          {problems.length > 0 && canZoom ? (
            <span className={styles.problemTool}>
              <button
                type="button"
                className="btn btn-quiet"
                aria-pressed={showProblems}
                aria-label="Show reading problems"
                aria-describedby={legendId}
                onClick={() => setProblemsChoice({ key: marksKey, on: !showProblems })}
              >
                <Icon name="eye-alert" />
                <span className="narrow-hide">Reading problems</span>
              </button>
              <span id={legendId} role="tooltip" className={styles.legendTip}>
                <ul className={styles.legend} aria-label="What the marks mean">
                  {legend(problems).map((quality) => (
                    <li key={quality}>
                      <span className={styles.swatch} data-quality={quality} aria-hidden="true" />
                      {MARK_WORDS[quality].legend}
                    </li>
                  ))}
                </ul>
              </span>
            </span>
          ) : null}
          {tools}
        </div>
      ) : null}
      {belowToolbar}
      {body}
    </div>
  );
}
