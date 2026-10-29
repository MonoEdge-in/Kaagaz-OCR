"use client";

// The document page's viewer for a PDF (SoW 19 Oct 2026): extend-hq/ui's PDF viewer
// (components/extend), every page in one scroll, with its pages sidebar, zoom, fit, rotate and
// search, and Highlights, Print and Download added here (Download is the screen's own address,
// so the download is logged as from the header). On each page it draws what
// PageViewer draws: the highlighted spot, your words (under it), my words, reading problems and flagged words, each
// in PageViewer's own style and doing what it does there. Highlights off hides the spot and
// the marks (flagged words stay: they are the review's targets). Photos and sheets keep
// PageViewer.
//
// Page sync: scrolling to another page tells the screen (onPageChange); the screen choosing a
// page or a spot scrolls the viewer to it, once the file is open. While the viewer travels
// there (STEERING_MS) the pages it passes are not told back, or the screen would leave its
// choice for one of them.
//
// The hand (Vrushit UAT 2, 27 Oct 2026, #419): a toolbar toggle. While it is on, a drag on the
// pages moves them (a mouse, a pen or a finger) with a grab cursor, and the engine's own pointer
// work (selecting text) is left out through its exclusion rule (data-no-interaction); a mark on
// the page is still pressed as before. Off, the pages select text as they did. The scroll follows
// the pointer at once, never animated; the keys scroll the pages either way.

import "@/components/extend/extend.css";
import { DownloadIcon, HandIcon, HighlighterIcon, PrinterIcon } from "lucide-react";
import { useCallback, useEffect, useEffectEvent, useLayoutEffect, useRef, useState } from "react";
import { PDFViewer } from "@/components/extend/pdf-viewer";
import type { PDFViewerHandle, PDFViewerPageOverlayProps } from "@/components/extend/pdf-viewer";
import { Button } from "@/components/extend/ui/button";
import { flagDomId, markLabel } from "@/components/review/wordReview";
import { markerId } from "@/components/words/ViewerWords";
import { markerRect, unturnedBox } from "@/components/words/spots";
import type { Marker } from "@/components/words/spots";
import { fileUrl } from "@/lib/api";
import type { ReadingMark, Region, WordFlag } from "@/lib/types";
import { markChecked, markName, marksOnPage } from "./readingMarks";
import styles from "./PageViewer.module.css";
import { centredScroll, panTo } from "./viewer";
import { wordColour } from "./YourWords";

/** How long a scroll the screen asked for takes to settle (a smooth scroll across pages). */
const STEERING_MS = 1000;
/** How many frames the spot's page may take to be drawn, after a jump to it (about 1 s). */
const SPOT_FRAMES = 60;
const NO_WORDS: Region[] = []; // a stable default, so the overlay is not made again each render
const NO_HITS: number[] = [];

export interface PdfDocumentViewerProps {
  document: { id: string; display_name: string };
  /** The page the screen shows (its selection's page). */
  pageNo: number;
  onPageChange: (pageNo: number) => void;
  highlights: Region[];
  /** What the spot is, under the viewer ("Highlighted: Grand total"); else a plain line. */
  highlightNote?: string;
  /** The file's download address (downloadHref); none, no Download. */
  download?: string | null;
  marks?: Marker[];
  activeMark?: string | null;
  readingMarks?: ReadingMark[];
  onMark?: (mark: ReadingMark, name: string) => void;
  hotMark?: number | null;
  onHotMark?: (index: number | null) => void;
  flags?: WordFlag[];
  activeFlag?: string | null;
  onFlag?: (flag: WordFlag) => void;
  /** Package H: the lines holding your words, drawn under the spot. */
  wordBoxes?: Region[];
  /** For each of wordBoxes, its word: the colour it is drawn in. */
  wordHits?: readonly number[];
}

/**
 * The file's full address: the PDF engine fetches it from a worker started from a blob: URL,
 * where a path like /api/... has nothing to resolve against. Only drawn in the browser (the
 * document page loads its detail there), so window is always there.
 */
export const fileAddress = (documentId: string) => new URL(fileUrl(documentId), window.location.href).toString();

/**
 * Print the original from a hidden frame. The file address itself answers as a download
 * (Content-Disposition: attachment, CSP sandbox), which a frame would save, not show; so the
 * bytes are fetched and framed from a blob: address (CSP frame-src 'self' blob:). False when
 * the file could not be fetched.
 */
async function printFile(documentId: string): Promise<boolean> {
  const response = await fetch(fileUrl(documentId)).catch(() => null);
  if (!response?.ok) return false;
  const address = URL.createObjectURL(new Blob([await response.arrayBuffer()], { type: "application/pdf" }));
  const frame = window.document.createElement("iframe");
  frame.hidden = true;
  frame.src = address;
  frame.onload = () => {
    frame.contentWindow?.focus();
    frame.contentWindow?.print();
    // Printing reads the frame; it and its address go after.
    setTimeout(() => {
      frame.remove();
      URL.revokeObjectURL(address);
    }, 60_000);
  };
  window.document.body.append(frame);
  return true;
}

export function PdfDocumentViewer({
  document,
  pageNo,
  onPageChange,
  highlights,
  highlightNote,
  download = null,
  marks = [],
  activeMark = null,
  readingMarks,
  onMark,
  hotMark = null,
  onHotMark,
  flags = [],
  activeFlag = null,
  onFlag,
  wordBoxes = NO_WORDS,
  wordHits = NO_HITS,
}: PdfDocumentViewerProps) {
  const viewer = useRef<PDFViewerHandle>(null);
  const [showHighlights, setShowHighlights] = useState(true);
  const [hand, setHand] = useState(false); // #419: a drag moves the pages
  const [printing, setPrinting] = useState(false);
  const [printFailed, setPrintFailed] = useState(false);
  const [open, setOpen] = useState(false); // the file is open, so the viewer can scroll
  const shownPage = useRef(1); // the page the viewer last said it is on
  const steeringUntil = useRef(0);
  const screen = useRef({ pageNo, onPageChange });
  useLayoutEffect(() => {
    screen.current = { pageNo, onPageChange };
  });

  // The screen chose a spot: scroll to it; else to its page, when that is not the one in view.
  const spot = highlights.find((region): region is Extract<Region, { kind: "box" }> => region.kind === "box");
  const spotKey = spot ? `${spot.page}:${spot.box.join(",")}` : null;
  // Fixed (#446): centred on its mark as drawn, every turn and the zoom applied. The engine's own
  // scrollToPageArea knows the file's turn but not the user's, so after a turn it missed. True when done.
  const toSpot = useEffectEvent((first: boolean) => {
    if (!open || !spot || !viewer.current) return true;
    steeringUntil.current = performance.now() + STEERING_MS;
    const area = viewer.current.getViewportElement();
    const mark = area?.querySelector(`[data-pdf-viewer-page="${spot.page}"] [data-spot]`);
    if (area && mark) {
      area.scrollTo({ ...centredScroll(mark.getBoundingClientRect(), area.getBoundingClientRect(), area), behavior: "smooth" });
      return true;
    }
    if (first) viewer.current.scrollToPage(spot.page); // a page far off is drawn once there
    return false;
  });
  const toPage = useEffectEvent(() => {
    if (!open || spot || pageNo === shownPage.current) return;
    steeringUntil.current = performance.now() + STEERING_MS;
    viewer.current?.scrollToPage(pageNo, { behavior: "smooth" });
  });
  useEffect(() => {
    let frame = 0;
    const go = (tries: number) => {
      if (!toSpot(tries === 0) && tries < SPOT_FRAMES) frame = requestAnimationFrame(() => go(tries + 1));
    };
    go(0);
    return () => cancelAnimationFrame(frame);
  }, [spotKey, open]);
  useEffect(() => toPage(), [pageNo, open]);

  // Stable, so the viewer tells only a change of its page, never its old page again when the
  // screen's page changes.
  const pageChanged = useCallback((page: number) => {
    shownPage.current = page;
    if (performance.now() < steeringUntil.current) return;
    if (page !== screen.current.pageNo) screen.current.onPageChange(page);
  }, []);

  // The hand (#419), on the viewer's own scroll area once the file is open.
  useEffect(() => {
    const area = viewer.current?.getViewportElement();
    if (!hand || !open || !area) return;
    let press: { id: number; x: number; y: number; left: number; top: number } | null = null;
    const down = (event: PointerEvent) => {
      if (event.button !== 0 || (event.target instanceof Element && event.target.closest("button, a"))) return;
      press = { id: event.pointerId, x: event.clientX, y: event.clientY, left: area.scrollLeft, top: area.scrollTop };
      area.setPointerCapture(event.pointerId);
      area.dataset.hand = "grabbing";
      event.preventDefault();
    };
    const move = (event: PointerEvent) => {
      if (press?.id === event.pointerId) area.scrollTo({ ...panTo(press, event.clientX, event.clientY), behavior: "instant" });
    };
    const end = (event: PointerEvent) => {
      if (press?.id !== event.pointerId) return;
      press = null;
      area.dataset.hand = "grab";
    };
    area.dataset.noInteraction = "";
    area.dataset.hand = "grab"; // its cursor (PageViewer.module.css .hand)
    area.addEventListener("pointerdown", down);
    area.addEventListener("pointermove", move);
    area.addEventListener("pointerup", end);
    area.addEventListener("pointercancel", end);
    return () => {
      delete area.dataset.noInteraction;
      delete area.dataset.hand;
      area.removeEventListener("pointerdown", down);
      area.removeEventListener("pointermove", move);
      area.removeEventListener("pointerup", end);
      area.removeEventListener("pointercancel", end);
    };
  }, [hand, open]);

  const overlay = useCallback(
    // The page's size here is already zoomed, and before its file's own turn (#442).
    ({ pageNumber, pageWidth, pageHeight, fileRotation }: PDFViewerPageOverlayProps) => {
      const place = (box: readonly [number, number, number, number]) => {
        const px = markerRect(unturnedBox(box, fileRotation), pageWidth, pageHeight);
        return { left: px.left, top: px.top, width: px.width, height: px.height };
      };
      const problems = marksOnPage(readingMarks, pageNumber);
      return (
        <>
          {showHighlights &&
            wordBoxes.map((region, index) =>
              region.kind === "box" && region.page === pageNumber ? (
                <div
                  key={`word-${index}`}
                  className={styles.word}
                  data-word={wordColour(wordHits[index])}
                  aria-hidden="true"
                  style={place(region.box)}
                />
              ) : null,
            )}
          {/* Drawn even with Highlights off, hidden, so toSpot can still centre it (#446). */}
          {highlights.map((region, index) =>
            region.kind === "box" && region.page === pageNumber ? (
              <div
                key={`spot-${index}`}
                data-spot=""
                className={`${styles.box} ${styles.pulse}`}
                aria-hidden="true"
                style={{ ...place(region.box), visibility: showHighlights ? undefined : "hidden" }}
              />
            ) : null,
          )}
          {showHighlights &&
            marks.map((mark) =>
              mark.region.kind === "box" && mark.region.page === pageNumber ? (
                <div
                  key={mark.id}
                  id={markerId(mark.id)}
                  className={mark.id === activeMark ? `${styles.mine} ${styles.mineActive}` : styles.mine}
                  aria-hidden="true"
                  style={place(mark.region.box)}
                >
                  <span className={styles.mineNumber}>{mark.n}</span>
                </div>
              ) : null,
            )}
          {flags.map((flag) => {
            if (flag.region.kind !== "box" || flag.page_no !== pageNumber) return null;
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
                style={place(flag.region.box)}
                onClick={() => onFlag?.(flag)}
              />
            );
          })}
          {showHighlights &&
            problems.map((mark, index) => {
              if (mark.region.kind !== "box") return null;
              const checked = markChecked(mark, flags);
              const name = markName(mark, index, problems.length, checked);
              return (
                <button
                  key={`problem-${index}`}
                  type="button"
                  className={styles.problem}
                  data-quality={mark.quality}
                  data-state={checked ? "checked" : undefined}
                  data-hot={hotMark === index ? "true" : undefined}
                  aria-label={name}
                  title={name}
                  style={place(mark.region.box)}
                  onClick={() => onMark?.(mark, name)}
                  onPointerEnter={() => onHotMark?.(index)}
                  onPointerLeave={() => onHotMark?.(null)}
                />
              );
            })}
        </>
      );
    },
    [showHighlights, wordBoxes, wordHits, highlights, marks, activeMark, flags, activeFlag, onFlag, readingMarks, hotMark, onHotMark, onMark],
  );

  return (
    <div className={`extend-ui flex min-h-0 flex-1 flex-col ${hand ? styles.hand : ""}`}>
      <PDFViewer
        ref={viewer}
        className="min-h-0 flex-1"
        src={fileAddress(document.id)}
        fileName={document.display_name}
        defaultZoom="fit-width"
        showDownload={false}
        renderPageOverlay={overlay}
        onActivePageChange={pageChanged}
        onDocumentLoadSuccess={() => setOpen(true)}
        toolbarActions={
          <>
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              aria-pressed={hand}
              aria-label="Hand"
              title={hand ? "Hand on: drag to move the page" : "Hand: drag to move the page"}
              onClick={() => setHand((on) => !on)}
            >
              <HandIcon />
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              aria-pressed={showHighlights}
              aria-label="Highlights"
              title={showHighlights ? "Hide highlights" : "Show highlights"}
              onClick={() => setShowHighlights((on) => !on)}
            >
              <HighlighterIcon />
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              aria-label="Print"
              title="Print"
              disabled={printing}
              onClick={async () => {
                setPrinting(true);
                setPrintFailed(!(await printFile(document.id)));
                setPrinting(false);
              }}
            >
              <PrinterIcon />
            </Button>
            {printFailed ? (
              <span role="status" className="text-xs">
                Could not print
              </span>
            ) : null}
            {download ? (
              <Button asChild variant="ghost" size="icon-sm">
                <a href={download} download aria-label="Download" title="Download">
                  <DownloadIcon />
                </a>
              </Button>
            ) : null}
          </>
        }
      />
      {spot && showHighlights ? (
        <p className={`muted ${styles.note} ${styles.pdfNote}`}>{highlightNote ?? "The highlighted box marks the spot."}</p>
      ) : null}
    </div>
  );
}
