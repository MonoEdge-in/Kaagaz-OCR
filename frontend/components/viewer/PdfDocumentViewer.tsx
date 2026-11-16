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
// the pointer once a frame (#454), never animated; the keys scroll the pages either way.
//
// #523: "Show words to check", a toolbar switch, on at first for a reviewer or an admin: the word
// marks and word flags follow it, each page as PageViewer does (wordMarkView: a chip on a page over
// WORD_MARK_CAP, never a mark far taller than a word).
//
// #589: the pages as this person turned them, kept in this browser (readPageTurns): opened with
// them, and each change kept. #600: a file with turns saved for everyone opens with those instead,
// and "Save for everyone" saves the turns shown (SavePageTurns.tsx); the reply's turns, others'
// included, are handed to the viewer, which shows them keeping the reader's place.

import "@/components/extend/extend.css";
import { DownloadIcon, HandIcon, HighlighterIcon, PrinterIcon, SpellCheckIcon } from "lucide-react";
import { useCallback, useEffect, useEffectEvent, useLayoutEffect, useRef, useState } from "react";
import { PDFViewer } from "@/components/extend/pdf-viewer";
import type { PDFViewerHandle, PDFViewerPageOverlayProps } from "@/components/extend/pdf-viewer";
import { Button, buttonVariants } from "@/components/extend/ui/button";
import { flagDomId, markLabel } from "@/components/review/wordReview";
import { markerId } from "@/components/words/ViewerWords";
import { markerRect, unturnedBox } from "@/components/words/spots";
import type { Marker } from "@/components/words/spots";
import { fileUrl } from "@/lib/api";
import { MOTION_MS, play } from "@/lib/motion";
import type { Frame } from "@/lib/motion";
import type { PageTurnSaved, ReadingMark, Region, Role, WordFlag } from "@/lib/types";
import {
  focusFirstWord,
  isWordMark,
  markChecked,
  markName,
  marksOnPage,
  showWordsByDefault,
  wordMarkView,
  wordsChipText,
} from "./readingMarks";
import styles from "./PageViewer.module.css";
import { SaveForEveryone, usePageTurns } from "./SavePageTurns";
import { centredScroll, mostVisiblePage, panTo } from "./viewer";
import { wordColour } from "./YourWords";

/** How long a scroll the screen asked for takes to settle (a smooth scroll across pages). */
const STEERING_MS = 1000;
/** How many frames the spot's page may take to be drawn, after a jump to it (about 1 s). */
const SPOT_FRAMES = 60;
/** A new spot settles onto its value (PageViewer's spotIn), played once from toSpot (#454). */
const SPOT_IN: Frame[] = [{ opacity: 0, transform: "scale(1.15)" }, { opacity: 1, transform: "none" }];
const NO_WORDS: Region[] = []; // a stable default, so the overlay is not made again each render
const NO_HITS: number[] = [];

export interface PdfDocumentViewerProps {
  document: { id: string; display_name: string; page_turns: PageTurnSaved[] };
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
  /** #523: the viewer's role, for "Show words to check" at first (on unless a member). */
  role?: Role;
  /** #589: the signed-in person, whose page turns of this file are kept. */
  userId: string;
  /** #600: after the turns are saved for everyone (the screen reloads the file's detail). */
  onTurnsSaved?: () => void;
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

/** The drawn page most in view in the pages' scroll area (mostVisiblePage), or null. */
function haltedPage(area: HTMLElement): number | null {
  const view = area.getBoundingClientRect();
  const pages = [...area.querySelectorAll<HTMLElement>("[data-pdf-viewer-page]")].map((page) => {
    const box = page.getBoundingClientRect();
    return { page: Number(page.dataset.pdfViewerPage), top: box.top, bottom: box.bottom };
  });
  return mostVisiblePage(pages, view.top, view.bottom);
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
  role,
  userId,
  onTurnsSaved,
}: PdfDocumentViewerProps) {
  const viewer = useRef<PDFViewerHandle>(null);
  const turns = usePageTurns(document.id, document.page_turns, userId);
  const [handedTurns, setHandedTurns] = useState(turns.opening);
  const [showHighlights, setShowHighlights] = useState(true);
  const [showWords, setShowWords] = useState(() => showWordsByDefault(role));
  const [crowdedOpen, setCrowdedOpen] = useState<readonly number[]>([]); // pages whose chip was pressed
  // #523: the chip pressed, focus goes to the first word mark it showed on its page.
  const focusPage = useRef<number | null>(null);
  useEffect(() => {
    const page = focusPage.current;
    focusPage.current = null;
    if (page === null) return;
    focusFirstWord(viewer.current?.getViewportElement()?.querySelector(`[data-pdf-viewer-page="${page}"]`));
  }, [crowdedOpen]);
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
      play(mark, SPOT_IN, MOTION_MS.slow);
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
    const area = viewer.current?.getViewportElement();
    let frame = 0;
    const go = (tries: number) => {
      if (!toSpot(tries === 0) && tries < SPOT_FRAMES) frame = requestAnimationFrame(() => go(tries + 1));
    };
    // #454: the user scrolling, dragging or pressing a key takes over: no more aiming, a smooth
    // scroll still on its way is halted where it is, and the pages they reach are told again. The
    // page it halted on was never told (the engine says a page's scroll's goal at once, and a
    // spot's scroll passes its pages untold), so it is measured and told now.
    const stop = () => {
      cancelAnimationFrame(frame);
      if (area && performance.now() < steeringUntil.current) {
        area.scrollTo({ left: area.scrollLeft, top: area.scrollTop, behavior: "instant" });
        shownPage.current = haltedPage(area) ?? shownPage.current;
        if (shownPage.current !== screen.current.pageNo) screen.current.onPageChange(shownPage.current);
      }
      steeringUntil.current = 0;
    };
    const input = ["wheel", "pointerdown", "touchstart", "keydown"] as const;
    for (const type of input) area?.addEventListener(type, stop, { passive: true });
    go(0);
    return () => {
      cancelAnimationFrame(frame);
      for (const type of input) area?.removeEventListener(type, stop);
    };
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
    let frame = 0; // #454: one scroll a frame, however often the pointer reports; nothing is measured on a move
    let to: { left: number; top: number } | null = null;
    const down = (event: PointerEvent) => {
      if (event.button !== 0 || (event.target instanceof Element && event.target.closest("button, a"))) return;
      press = { id: event.pointerId, x: event.clientX, y: event.clientY, left: area.scrollLeft, top: area.scrollTop };
      area.setPointerCapture(event.pointerId);
      area.dataset.hand = "grabbing";
      event.preventDefault();
    };
    const move = (event: PointerEvent) => {
      if (press?.id !== event.pointerId) return;
      to = panTo(press, event.clientX, event.clientY);
      frame ||= requestAnimationFrame(() => {
        frame = 0;
        if (to) area.scrollTo({ ...to, behavior: "instant" });
      });
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
      cancelAnimationFrame(frame);
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
      // The marks' boxes are of the page as shown (turned): its height over its width, so.
      const aspect = fileRotation % 180 === 0 ? pageHeight / pageWidth : pageWidth / pageHeight;
      const words = wordMarkView(problems, showWords, crowdedOpen.includes(pageNumber), aspect);
      // The marks of pages beside the screen's come too (#454); a hot mark (DD7) is a number on
      // the screen's page, as the Needs attention panel counts them.
      const here = pageNumber === pageNo;
      // #454: no entrance here. The viewer draws only the pages near the view, so a page scrolled
      // back to is drawn anew and a CSS entrance played again on each (the blink while moving);
      // the spot settles once, from toSpot, and the lines holding your words simply show.
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
                  style={{ ...place(region.box), animation: "none" }}
                />
              ) : null,
            )}
          {/* Drawn even with Highlights off, hidden, so toSpot can still centre it (#446). */}
          {highlights.map((region, index) =>
            region.kind === "box" && region.page === pageNumber ? (
              <div
                key={`spot-${index}`}
                data-spot=""
                className={styles.box}
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
          {showHighlights && words.capped ? (
            <button
              type="button"
              className={`${styles.wordsChip} ${styles.pageChip}`}
              onClick={() => {
                focusPage.current = pageNumber;
                setCrowdedOpen((pages) => [...pages, pageNumber]);
              }}
            >
              {wordsChipText(words.total)}
              <span className="visually-hidden">: show their marks</span>
            </button>
          ) : null}
          {flags.map((flag) => {
            if (flag.region.kind !== "box" || flag.page_no !== pageNumber) return null;
            if (flag.kind === "mark" && flag.word_key !== activeFlag && !words.drawn(flag.region)) return null;
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
              if (mark.region.kind !== "box" || (isWordMark(mark) && !words.drawn(mark.region))) return null;
              const checked = markChecked(mark, flags);
              const name = markName(mark, index, problems.length, checked);
              return (
                <button
                  key={`problem-${index}`}
                  type="button"
                  className={styles.problem}
                  data-quality={mark.quality}
                  data-word-mark={isWordMark(mark) ? "" : undefined}
                  data-state={checked ? "checked" : undefined}
                  data-hot={here && hotMark === index ? "true" : undefined}
                  aria-label={name}
                  title={name}
                  style={place(mark.region.box)}
                  onClick={() => onMark?.(mark, name)}
                  onPointerEnter={() => (here ? onHotMark?.(index) : undefined)}
                  onPointerLeave={() => onHotMark?.(null)}
                />
              );
            })}
        </>
      );
    },
    [showHighlights, showWords, crowdedOpen, wordBoxes, wordHits, highlights, marks, activeMark, flags, activeFlag, onFlag, readingMarks, pageNo, hotMark, onHotMark, onMark],
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
        pageRotations={handedTurns}
        onPageRotationsChange={turns.show}
        turnMenuDisabled={turns.saving}
        renderPageOverlay={overlay}
        onActivePageChange={pageChanged}
        onDocumentLoadSuccess={() => setOpen(true)}
        toolbarActions={
          <>
            <SaveForEveryone
              documentId={document.id}
              userId={userId}
              turns={turns}
              onSaved={(saved) => {
                setHandedTurns(saved);
                onTurnsSaved?.();
              }}
              className={buttonVariants({ variant: "ghost", size: "sm" })}
            />
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
            {readingMarks?.some(isWordMark) ? (
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                role="switch"
                aria-checked={showWords}
                aria-label="Show words to check"
                title={showWords ? "Hide words to check" : "Show words to check"}
                onClick={() => setShowWords((on) => !on)}
              >
                <SpellCheckIcon />
              </Button>
            ) : null}
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
      {/* Always laid out, hidden without a spot (#454): the line coming and going resized the
          pages' scroll area, and the engine answers a resize by setting the scroll again, which
          stopped a scroll, a drag or the centring on a spot mid-way. */}
      <p
        className={`muted ${styles.note} ${styles.pdfNote}`}
        style={spot && showHighlights ? undefined : { visibility: "hidden" }}
      >
        {highlightNote ?? "The highlighted box marks the spot."}
      </p>
    </div>
  );
}
