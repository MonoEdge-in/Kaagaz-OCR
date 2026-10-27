"use client";

// The document screen. UI audit (Tushar, 16 Oct 2026): the header is a breadcrumb
// (Documents / its folders / its name, DD2), then one row: the title and its status
// chip, then Download original, "Ask about this document" (the one primary, PRD 3 H2)
// and the document's menu (DocumentMenu.tsx: Read again for a reviewer or an admin,
// ReadAgain.tsx and PRD 2 X1; Rename, Move, Tags, Move to Trash). One meta line under
// it: the type and pages, where it is filed, its tags and versions ("Version 2 of 2",
// each a link to that version's own id). A newer version still being read, or one
// that failed, gets a line linking to it. A document in Trash says so.
// Below: the page viewer and, beside it from 1024 px, the panels as tabs (DD6):
// Overview (the document card, Values found, related documents and the
// user's story), Needs attention (N) (the page's reading problems as an alert, DD4,
// then the Attention panel, U5), Scanned text (ScannedTextPanel.tsx) and My words
// (components/words/ViewerWords.tsx). The same tabs in the same order on every load
// (DD8): Overview first, or Scanned text when the link says ?scanned=1. On a window at
// least 1024 px wide and 36rem tall the page column sticks, as tall as <main>, with
// the page fitted to its width (DD5, STICKY_VIEWER_QUERY), and a handle between the
// columns resizes them (by drag past 5px, or the arrow keys). Values found shows about
// six values, then "Show all N" (never hiding a Ready to verify value); its third column
// is the label with its reason under it. Under the page: "Highlighted: <field>".
// While it is checked or read, on load too, its summary is asked for again
// (lib/poll.ts); once it settles, the whole detail is asked for once, and the screen
// says what was found. Clicking a value jumps to its page and highlights its spot.
// Every value is shown as plain text; it came from the document. A low-resolution page
// says so above it (Y4). A mark pressed on the page shows its spot and opens Scanned
// text, with both readings; pointing at a mark or its chip lights the other (DD7).
// A family Kaagaz filed by its type says "Filed here by Kaagaz · Undo" (feature 28).
// Word-level review (scan review UX rules, 16 Oct 2026): the summary bar above the page
// (WordReview.tsx), the header's trust signals (words, flagged, reviewed, last change),
// flagged words marked on the page and in Scanned text, and review mode: the Needs
// attention tab becomes the review panel (ReviewPanel.tsx); leaving it puts focus back
// on the word on the page.
// Your words (package H): a link with ?words= lights every line of the page holding one
// (YourWords.tsx); Vrushit UAT 2 (#412): no panel about them under the page.

// Document panel UX (25 Oct 2026): the panel answers, in order, what is this (the name, one
// status in words, the AI summary and its key facts), can I trust it (one status line, its
// counts under More details) and what can I do (Ask, the one primary; Download and Clean
// copy in one quiet row; the rest in the "…" menu). At most four tabs show, the rest under
// More. On a phone the same column comes first, above the page. This replaces the breadcrumb,
// title row and meta line described above.
// Vrushit UAT (25 Oct 2026): no Invoice section and no "What was read" under a value; a value
// is one compact row (its name, the value, its page, Add meaning, then its badge) in 0.75rem;
// Needs attention's count is a filled circle; Export as Data saves the read text as a .txt.

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import type { CSSProperties, KeyboardEvent, ReactNode, RefObject } from "react";
import { tabAfterKey } from "@/components/admin/logic";
import { RowMenu } from "@/components/admin/RowMenu";
import { useMediaQuery } from "@/components/chat/hooks";
import { linkWords } from "@/components/chat/answer";
import { askAboutHref } from "@/components/chat/logic";
import { downloadHref, folderTrail } from "@/components/documents/library";
import libraryStyles from "@/components/documents/Library.module.css";
import { ALL_DOCUMENTS, libraryHref } from "@/components/documents/libraryQuery";
import { FILED_HERE_TEXT } from "@/components/documents/batchTray";
import { UNDERSTANDING_TEXT } from "@/components/documents/logic";
import { TimeLeft } from "@/components/documents/TimeLeft";
import { AutofileNote } from "@/components/documents/UploadArea";
import { Icon } from "@/components/ui/Icon";
import { Icon as KIcon } from "@/components/ui/Icon/Icon";
import type { KIconName } from "@/components/ui/Icon/Icon";
import { Toast } from "@/components/ui/Toast";
import { PageViewer } from "@/components/viewer/PageViewer";
import {
  DRAG_THRESHOLD_PX,
  SPLIT,
  STICKY_VIEWER_QUERY,
  bringPageForSpot,
  splitAfterDrag,
  splitAfterKey,
  tabsThatFit,
} from "@/components/viewer/viewer";
import { CLEAN_COPY } from "@/components/viewer/clearCopy";
import { ReadClearlyPanel } from "@/components/viewer/ReadClearlyPanel";
import { ScannedTextPanel } from "@/components/viewer/ScannedTextPanel";
import { valueAt } from "@/components/viewer/scanned";
import { loadDocumentWords } from "@/components/viewer/YourWords";
import { useTabMotion } from "@/lib/motion";
import { AddMeaningDialog, PageWordsList, useViewerWords } from "@/components/words/ViewerWords";
import wordStyles from "@/components/words/Words.module.css";
import {
  newerVersionText,
  sortedVersions,
  usedInChat,
  versionNote,
  versionPosition,
  versionText,
  withSummary,
} from "@/components/viewer/versions";
import { LabelBadge } from "@/components/viewer/LabelBadge";
import { PdfDocumentViewer } from "@/components/viewer/PdfDocumentViewer";
import { ViewerBoundary } from "@/components/viewer/ViewerBoundary";
import { fieldText, reasonText, voteText } from "@/components/viewer/labels";
import { lowResolutionText, untilRead } from "@/components/viewer/panels";
import type { Spot } from "@/components/viewer/panels";
import { beingRead, canReprocess, loadFailure, settledText, valuesNote } from "@/components/viewer/reprocess";
import { highlightsOf, initialSelection, pageChange } from "@/components/viewer/selection";
import { selectSpot, selectValue } from "@/components/viewer/selection";
import type { Selection } from "@/components/viewer/selection";
import { canReview, verifiedText, verifyEach } from "@/components/review/review";
import { flagDomId, trustText } from "@/components/review/wordReview";
import { Timeline } from "@/components/activity/Timeline";
import { ACTIVITY_TEXT } from "@/components/activity/words";
import { DetailsPanel } from "@/components/files/DetailsPanel";
import { LABEL_DESCRIPTIONS, labelName } from "@/components/files/labels";
import { QUESTIONS_TEXT, QuestionsPanel } from "@/components/files/QuestionsPanel";
import { FILES_TEXT } from "@/components/files/words";
import { VersionBanner } from "@/components/versions/VersionBanner";
import { VersionsPanel } from "@/components/versions/VersionsPanel";
import { VERSION_TEXT } from "@/components/versions/words";
import {
  ApiError,
  documentActivity,
  getDocument,
  getDocumentSummary,
  getPageReadings,
  resolveReview,
} from "@/lib/api";
import { formatDate, formatDateTime, statusChip, typeLabel } from "@/lib/format";
import type { StatusTone } from "@/lib/format";
import { progressSignature } from "@/lib/poll";
import { markReceived } from "@/lib/timeLeft";
import { decodeRegion } from "@/lib/regions";
import { useSession } from "@/lib/session";
import type {
  DocumentDetail,
  DocumentSummary,
  DocumentWords,
  ExtractedValue,
  PageSummary,
  ReadingMark,
  Region,
  WordFlag,
} from "@/lib/types";
import { usePoll } from "@/lib/usePoll";
import { AttentionPanel, RelatedPanel, attentionCount } from "./DocumentPanels";
import { DocumentMenu } from "./DocumentMenu";
import { PageProblems } from "./PageProblems";
import { ReadAgain } from "./ReadAgain";
import { ReviewPanel } from "./ReviewPanel";
import { StoryPanel } from "./StoryPanel";
import { ScriptText, SummaryCard } from "./SummaryCard";
import styles from "./DocumentView.module.css";
import { ExportDialog, SummaryBar, documentMoreItems, exportData, useEnsembleStart, useWordReview } from "./WordReview";

export interface DocumentViewProps {
  documentId: string;
  pageParam: string | null;
  regionParam: string | null;
  scannedParam: string | null;
  wordsParam: string | null;
}

const FIRST_VALUES = 6; // shown before "Show all N"
// Said after "Highlighted: <field>" when the spot came in a link (a source card, a search
// hit or a review item), not from a pick on this screen.
const FROM_LINK = ", from the link you opened";
// The status under the title: its words (lib/format.ts statusChip) with an icon.
const STATUS_ICONS: Record<StatusTone, KIconName> = { ready: "check-circle", working: "clock", neutral: "info", danger: "alert" };
const DETAILS_ID = "document-details"; // More details: the kind, pages, counts, tags and versions
const valueDomId = (id: string) => `document-value-${id}`; // a value's "show on page" button

type Loaded =
  | { attempt: number; detail: DocumentDetail }
  | { attempt: number; error: ReturnType<typeof loadFailure> };

function BackLink() {
  return (
    <Link href="/documents" className={styles.back}>
      <Icon name="chevron-left" />
      Back to documents
    </Link>
  );
}

export function DocumentView({
  documentId,
  pageParam,
  regionParam,
  scannedParam,
  wordsParam,
}: DocumentViewProps) {
  const { me } = useSession();
  const [attempt, setAttempt] = useState(0);
  // Asks again but keeps showing what is loaded until the answer comes (once reading settles).
  const [reloads, setReloads] = useState(0);
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  // A choice made on this screen, remembered with the link it was made under,
  // so following a new link (new ?page / ?region) shows that link's spot.
  const linkKey = `${pageParam ?? ""}|${regionParam ?? ""}|${scannedParam ?? ""}`;
  const [chosen, setChosen] = useState<{ linkKey: string; selection: Selection } | null>(null);
  // The panels' tab: as the link says (?scanned=1: Scanned text) until one is picked.
  const [tabChoice, setTabChoice] = useState<{ linkKey: string; tab: TabId } | null>(null);
  const [split, setSplit] = useState<number>(SPLIT.start); // the page column's share, in percent
  const layoutRef = useRef<HTMLDivElement>(null);
  const menuButton = useRef<HTMLButtonElement | null>(null);
  const [hotMark, setHotMark] = useState<number | null>(null); // DD7: the mark pointed at
  const [readAgainKey, setReadAgainKey] = useState(0); // Read again picked from the menu: its question
  const [announcement, setAnnouncement] = useState("");
  const [toast, setToast] = useState<string | null>(null); // "Owner saved.", "Download started."
  const [moreOpen, setMoreOpen] = useState(false); // the status line's More details
  const viewerRef = useRef<HTMLElement>(null);
  // Set when a poll sees reading end: the next detail says what was found ("again": after Read again).
  const settling = useRef<"read" | "again" | null>(null);
  const readingAgain = useRef(false);
  // Ready first: the detail shown came while the document was still being understood.
  const loadedUnderstanding = useRef(false);
  // "Add meaning": what it is for, and the button that opened it (focus goes back there).
  const [adding, setAdding] = useState<{ text: string; region: Region } | null>(null);
  const opener = useRef<HTMLElement | null>(null);
  const [allValues, setAllValues] = useState(false); // "Show all N" pressed
  const [verifying, setVerifying] = useState<string | "all" | null>(null);
  const [verifyProgress, setVerifyProgress] = useState({ done: 0, total: 0 });
  // After Verify all: what it says, and the ids of the values that could not be saved.
  const [verifyNotice, setVerifyNotice] = useState<{ text: string; failed: boolean; retry?: string[] } | null>(null);
  // Word-level review: on or off, the word being reviewed, the export dialog.
  const [reviewing, setReviewing] = useState(false);
  const [currentFlag, setCurrentFlag] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const reviewButton = useRef<HTMLButtonElement | null>(null);
  const reviewHeading = useRef<HTMLHeadingElement | null>(null);
  // KZ-007: a rejected file has no words to review, so nothing is asked for it.
  const loadedStatus = loaded && "detail" in loaded ? loaded.detail.status : null;
  const review = useWordReview(documentId, reloads, loadedStatus !== null && loadedStatus !== "rejected");
  const ensemble = useEnsembleStart(documentId, review, setAnnouncement);
  // File management A5: the Activity tab's timeline, every version of the family; asked
  // again after each reload (a label or owner change shows at once).
  const loadActivity = useCallback(
    (cursor?: string) => documentActivity(documentId, cursor),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reloads: ask again after a change
    [documentId, reloads],
  );

  // While it is read: merge each summary; once it settles, ask for the detail once.
  const merge = (summary: DocumentSummary) =>
    setLoaded((shown) =>
      shown && "detail" in shown && shown.detail.id === summary.id
        ? { ...shown, detail: withSummary(shown.detail, summary) }
        : shown,
    );
  const poll = usePoll<DocumentSummary>({
    load: () =>
      getDocumentSummary(documentId).then((summary) => {
        markReceived([summary], performance.now());
        return summary;
      }),
    onResult: (summary) => {
      merge(summary);
      if (beingRead(summary.status)) return;
      // Ready first: the detail once ready (said aloud), and again once understood (its card).
      if (summary.understanding === true && loadedUnderstanding.current) return;
      if (!loadedUnderstanding.current) {
        settling.current = readingAgain.current ? "again" : "read";
        readingAgain.current = false;
      }
      setReloads((n) => n + 1);
    },
    onFailure: () => undefined, // it keeps trying; the page shows what it has
    again: (summary) => beingRead(summary.status) || summary.understanding === true,
    signature: (summary) => progressSignature([summary]),
  });

  useEffect(() => {
    let cancelled = false;
    getDocument(documentId)
      .then((detail) => {
        if (cancelled) return;
        setLoaded({ attempt, detail });
        loadedUnderstanding.current = detail.understanding === true;
        if (beingRead(detail.status) || loadedUnderstanding.current) poll.later();
        if (!beingRead(detail.status) && settling.current) {
          setAnnouncement(settledText(detail, settling.current === "again"));
        }
        settling.current = null;
      })
      .catch((error: unknown) => {
        if (!cancelled) setLoaded({ attempt, error: loadFailure(error) });
      });
    return () => {
      cancelled = true;
      poll.stop();
    };
  }, [poll, documentId, attempt, reloads]);

  // A retry shows the loading state again until its own answer arrives.
  const current = loaded && loaded.attempt === attempt ? loaded : null;
  const shown = current && "detail" in current ? current.detail : null;
  const selection = !shown
    ? null
    : chosen && chosen.linkKey === linkKey
      ? chosen.selection
      : initialSelection(shown, pageParam, regionParam);
  const words = useViewerWords(documentId, selection ? selection.pageNo : null);
  const stickyViewer = useMediaQuery(STICKY_VIEWER_QUERY);
  const markedPage = shown && selection ? shown.pages.find((p) => p.page_no === selection.pageNo) : undefined;
  const readingMarks = usePageMarks(documentId, markedPage, reloads);
  const yourWords = linkWords(wordsParam);
  const wordsFound = useDocumentWords(documentId, yourWords, reloads);

  // While it loads, only the skeleton (SoW 19 Oct: "Back to documents" flashed for half a second).
  if (!current) {
    return (
      <div className="page page-wide">
        <p className="visually-hidden" role="status">
          Loading the document…
        </p>
        <div className={`delayed ${styles.loading}`} aria-hidden="true">
          <span className={`skeleton ${styles.titleSkeleton}`} />
          <div className={styles.layout}>
            <span className={`skeleton ${styles.pageSkeleton}`} />
            <span className={`skeleton ${styles.valuesSkeleton}`} />
          </div>
        </div>
      </div>
    );
  }

  if ("error" in current) {
    return (
      <div className="page page-wide">
        <BackLink />
        <div className={styles.errorBanner} role="alert">
          <Icon name="alert" className={styles.errorIcon} />
          <div className={styles.errorBody}>
            <p className={styles.errorTitle}>This document could not be shown.</p>
            <p>{current.error.message}</p>
          </div>
          {current.error.retry && (
            <button type="button" className="btn" onClick={() => setAttempt((n) => n + 1)}>
              <Icon name="refresh" />
              Try again
            </button>
          )}
        </div>
      </div>
    );
  }

  const detail = current.detail;
  const page = selection ? detail.pages.find((p) => p.page_no === selection.pageNo) : undefined;
  const pageNumbers = detail.pages.map((p) => p.page_no);

  const choose = (next: Selection) => setChosen({ linkKey, selection: next });
  // Stacked on a phone, the page is above the list: bring it into view, unless PageViewer will.
  const bringPage = (region: Region) => {
    if (bringPageForSpot(selection?.region ?? null, region)) viewerRef.current?.scrollIntoView({ block: "nearest" });
  };
  const showValue = (value: ExtractedValue) => {
    choose(selectValue(value));
    setAnnouncement(`Showing ${fieldText(value.field)} on page ${value.region.page}.`);
    bringPage(value.region);
  };
  const showSpot = (spot: Spot, said = `Showing ${spot.name} on page ${spot.region.page}.`) => {
    choose(selectSpot(spot.region, spot.label));
    setAnnouncement(said);
    bringPage(spot.region);
  };
  // A scanned line ("line 14"): "Line 14 highlighted on page 1."
  const showLine = (spot: Spot) =>
    showSpot(spot, `${spot.name.charAt(0).toUpperCase()}${spot.name.slice(1)} highlighted on page ${spot.region.page}.`);
  const changePage = (pageNo: number) => choose(pageChange(selection, pageNo));
  // Keep as is (F5): the page's new summary, still flagged, off the attention list.
  const keepPage = (kept: PageSummary) =>
    setLoaded((now) =>
      now && "detail" in now
        ? {
            ...now,
            detail: {
              ...now.detail,
              pages: now.detail.pages.map((p) => (p.page_no === kept.page_no ? kept : p)),
            },
          }
        : now,
    );
  const addMeaning = (text: string, region: Region, from: HTMLElement) => {
    opener.current = from;
    setAdding({ text, region });
  };
  const hasScannedText = page !== undefined && page.unit !== "cells";
  const attention = attentionCount(detail.pages);
  // File management P1 (Changed on purpose, PRD 1 screens): Details (was Overview), Text (was
  // Scanned text), and Questions, Versions and Activity; Needs attention and My words stay.
  // Document panel UX (25 Oct 2026): Needs attention only with something in it (or while
  // reviewing); the first four show, the rest are under More (PanelTabs).
  const tabs: TabItem[] = [
    { id: "overview", label: FILES_TEXT.details },
    ...(attention > 0 || reviewing ? [{ id: "attention" as const, label: "Needs attention", count: attention || undefined }] : []),
    ...(hasScannedText ? [{ id: "scanned" as const, label: "Text" }] : []),
    { id: "questions", label: QUESTIONS_TEXT.heading },
    ...(hasScannedText ? [{ id: "clear" as const, label: CLEAN_COPY }] : []),
    { id: "versions", label: VERSION_TEXT.versions, count: detail.versions.length },
    { id: "activity", label: ACTIVITY_TEXT.heading },
    { id: "words", label: "My words" },
  ];
  const openClear = () => {
    setTab("clear");
    document.getElementById(TAB_BAR_ID)?.scrollIntoView({ block: "nearest" });
  };
  const chosenTab = tabChoice && tabChoice.linkKey === linkKey ? tabChoice.tab : scannedParam === "1" ? "scanned" : "overview";
  const tab = tabs.some((each) => each.id === chosenTab) ? chosenTab : "overview";
  const setTab = (next: TabId) => setTabChoice({ linkKey, tab: next });
  // A reading problem pressed (U3): its spot on the page, and the line with both readings.
  const showMark = (mark: ReadingMark, name: string) => {
    choose(selectSpot(mark.region, null));
    setAnnouncement(`Showing ${name} on page ${mark.region.page}.`);
    if (mark.region.kind === "box" && hasScannedText) setTab("scanned");
  };
  const lowResolution = page ? lowResolutionText(page) : null;
  // The words still to settle are marked on the page and in Scanned text (High: no mark).
  const openFlags = review.flags.filter((flag) => flag.state !== "reviewed" && flag.state !== "resolved");
  const pageFlags = page ? openFlags.filter((flag) => flag.page_no === page.page_no) : [];
  const toWord = (key: string) => {
    setCurrentFlag(key);
    const flag = review.flags.find((each) => each.word_key === key);
    if (!flag) return;
    choose(selectSpot(flag.region, null));
    bringPage(flag.region);
  };
  const startReview = (flag?: WordFlag) => {
    const first = flag ?? openFlags[0] ?? review.flags[0];
    if (reviewing && tab === "attention" && !flag) reviewHeading.current?.focus();
    setReviewing(true);
    setTab("attention");
    if (first) toWord(first.word_key);
  };
  // A word box pressed on the page (24 Oct 2026): with Text open, its line is shown there;
  // otherwise it starts checking that word, as before.
  const pressFlag = (flag: WordFlag) => {
    if (tab === "scanned") {
      choose(selectSpot(flag.region, null));
      setAnnouncement(`Showing the line with this word on page ${flag.region.page}.`);
      return;
    }
    startReview(flag);
  };
  const exitReview = () => {
    setReviewing(false);
    const word = currentFlag;
    requestAnimationFrame(() => {
      (document.getElementById(flagDomId(word ?? "")) ?? reviewButton.current)?.focus();
    });
  };
  const wordBoxes = wordsFound ? wordsFound.boxes : NO_REGIONS;
  const wordHits = wordsFound?.hits ?? NO_HITS;

  const pagesText =
    detail.page_count !== null
      ? ` · ${detail.page_count} ${detail.page_count === 1 ? "page" : "pages"}`
      : "";
  const trail = detail.folder ? folderTrail(detail.folder) : [];
  // "Back to Land & Legal": the folder it is filed in, else Documents, or Trash.
  const folder = trail.at(-1);
  const back =
    detail.deleted_at !== null
      ? { href: "/documents?trash=1", name: "Trash" }
      : folder
        ? { href: libraryHref({ ...ALL_DOCUMENTS, folderId: folder.id }), name: folder.name }
        : { href: "/documents", name: "documents" };
  const chip = statusChip(detail.status);
  // Trash means deleted: no download from there, only Restore (the Trash view).
  const download = detail.deleted_at === null ? downloadHref(detail) : null;
  const position = versionPosition(detail);
  const note = versionNote(detail);
  const readingNote = valuesNote(detail.status, detail.values.length);
  const canVerifyValues =
    canReview(me.user.role) &&
    detail.status === "ready" &&
    detail.deleted_at === null;
  const pendingValues = detail.values.filter((value) => value.label === "needs_review");
  // About six values, then "Show all N"; a Ready to verify value and the one highlighted always show.
  const shownValues =
    allValues || detail.values.length <= FIRST_VALUES
      ? detail.values
      : detail.values.filter(
          (value, index) => index < FIRST_VALUES || value.label === "needs_review" || value.id === selection?.valueId,
        );
  // "Highlighted: Grand total, from the link you opened" under the page; else PageViewer's own words.
  const highlighted =
    selection?.region && (detail.values.find((v) => v.id === selection.valueId) ?? valueAt(detail.values, selection.region));
  const fromLink = regionParam !== null && !(chosen && chosen.linkKey === linkKey);
  const highlightNote = highlighted
    ? `Highlighted: ${fieldText(highlighted.field)}${fromLink ? FROM_LINK : ""}`
    : undefined;
  const reloadWith = (message: string) => {
    setToast(message); // its own live region reads it out
    setReloads((n) => n + 1);
  };

  // Verify and Verify all are disabled while they save and are gone once nothing is left to
  // verify, which dropped focus to the page (QA pass, 25 Oct 2026): back to the value, or to
  // the Values found heading.
  const afterVerify = (valueId: string | null) => {
    requestAnimationFrame(() => {
      (document.getElementById(valueDomId(valueId ?? "")) ?? document.getElementById("values-heading"))?.focus();
    });
  };
  const verifyValue = async (value: ExtractedValue) => {
    if (!canVerifyValues || verifying !== null || value.label !== "needs_review") return;
    setVerifying(value.id);
    setVerifyNotice(null);
    try {
      await resolveReview(value.id, "typed", false, value.value);
      setVerifyNotice({ text: `Verified ${fieldText(value.field)}.`, failed: false });
    } catch (error) {
      setVerifyNotice({
        text: error instanceof ApiError ? error.message : "Could not verify this value. Check your connection, then try again.",
        failed: true,
      });
    } finally {
      setVerifying(null);
      setReloads((n) => n + 1);
      // The pressed Verify was disabled, then goes: keep the keyboard on this value's row.
      afterVerify(value.id);
    }
  };

  // Verify all: every value still Ready to verify, or after a partial failure (Try again)
  // only those that could not be saved and are still Ready to verify.
  const verifyAllValues = async (values: ExtractedValue[]) => {
    if (!canVerifyValues || verifying !== null || values.length === 0) return;
    const total = values.length;
    setVerifying("all");
    setVerifyProgress({ done: 0, total });
    setVerifyNotice(null);
    const failed = await verifyEach(
      values,
      (value) => resolveReview(value.id, "typed", false, value.value),
      (done) => setVerifyProgress({ done, total }),
    );
    setVerifyNotice({
      text: verifiedText(total, failed.length),
      failed: failed.length > 0,
      retry: failed.map((value) => value.id),
    });
    setVerifying(null);
    setReloads((n) => n + 1);
    afterVerify(null);
  };
  const retryValues = pendingValues.filter((value) => verifyNotice?.retry?.includes(value.id));

  const values = (
    <section className={styles.values} aria-labelledby="values-heading">
      <div className={styles.valuesHeader}>
        <h2 id="values-heading" className={styles.valuesHeading} tabIndex={-1}>
          Values found
          {detail.values.length > 0 ? (
            <span className={`${styles.count} tabular`}>{detail.values.length}</span>
          ) : null}
        </h2>
        {canVerifyValues && pendingValues.length > 0 && (
          <button
            type="button"
            className="btn btn-quiet"
            onClick={() => void verifyAllValues(pendingValues)}
            disabled={verifying !== null}
          >
            {verifying === "all"
              ? `Verifying ${verifyProgress.done} of ${verifyProgress.total}…`
              : `Verify all ${pendingValues.length}`}
          </button>
        )}
      </div>
      {verifyNotice && (
        <p className={verifyNotice.failed ? "error-text" : "muted"} role={verifyNotice.failed ? "alert" : "status"}>
          {verifyNotice.text}
        </p>
      )}
      {canVerifyValues && retryValues.length > 0 && (
        <button
          type="button"
          className={`btn btn-quiet ${styles.showAll}`}
          onClick={() => void verifyAllValues(retryValues)}
          disabled={verifying !== null}
        >
          Try again
          <span className="visually-hidden">{` to verify the ${retryValues.length} not saved`}</span>
        </button>
      )}
      {readingNote && <p className="muted">{readingNote}</p>}
      {/* Said once, not on every row (Tushar, 24 Oct 2026: "minimize this"). */}
      {detail.values.some((value) => value.label_reason === "model_chosen") && (
        <p className={styles.valuesNote}>{AI_FOUND_NOTE}</p>
      )}
      {detail.values.length === 0 ? (
        !readingNote && <p className="muted">{untilRead(detail.status, "No values were found in this document.")}</p>
      ) : (
        <div className={styles.valuesWrap}>
          <table className={`table ${styles.valuesTable}`} role="table">
            <caption className="visually-hidden">
              Values found. Pick a value to show it on its page.
            </caption>
            <thead role="rowgroup">
              <tr role="row">
                <th scope="col" role="columnheader">
                  Field
                </th>
                <th scope="col" role="columnheader">
                  Value
                </th>
                <th scope="col" role="columnheader">
                  Label and reason
                </th>
              </tr>
            </thead>
            <tbody role="rowgroup">
              {shownValues.map((value) => {
                const active = selection?.valueId === value.id;
                return (
                  <tr
                    key={value.id}
                    role="row"
                    className={active ? styles.activeRow : undefined}
                    data-label={value.label}
                  >
                    <td role="cell" className={styles.fieldCell}>
                      {fieldText(value.field)}
                    </td>
                    <td role="cell" className={styles.valueCell}>
                      <button
                        type="button"
                        id={valueDomId(value.id)}
                        className={styles.valueButton}
                        aria-pressed={active}
                        onClick={() => showValue(value)}
                      >
                        {value.value}
                        <span className="visually-hidden">
                          {`, show on page ${value.region.page}`}
                        </span>
                      </button>
                      <span className={styles.valuePage}>
                        Page {value.region.page}
                        {active ? ", highlighted" : ""}
                      </span>
                      <button
                        type="button"
                        className={`btn btn-quiet ${wordStyles.target}`}
                        onClick={(event) => addMeaning(value.value, value.region, event.currentTarget)}
                      >
                        Add meaning
                        <span className="visually-hidden">{` for ${fieldText(value.field)}`}</span>
                      </button>
                    </td>
                    <td role="cell" className={styles.labelCell}>
                      <LabelBadge label={value.label} confidence={value.confidence ?? null} />
                      {(value.label_reason !== "model_chosen" || value.votes) && (
                        <div className={styles.reason}>
                          {value.label_reason !== "model_chosen" && reasonText(value.label_reason)}
                          {value.votes && <div className={styles.pageNote}>{voteText(value.votes)}</div>}
                        </div>
                      )}
                      {canVerifyValues && value.label === "needs_review" && (
                        <button
                          type="button"
                          className="btn btn-quiet"
                          onClick={() => void verifyValue(value)}
                          disabled={verifying !== null}
                        >
                          {verifying === value.id ? "Verifying…" : "Verify"}
                          <span className="visually-hidden">{` ${fieldText(value.field)} on page ${value.region.page}`}</span>
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {detail.values.length > FIRST_VALUES && (
        <button
          type="button"
          className={`btn btn-quiet ${styles.showAll}`}
          aria-expanded={allValues}
          onClick={() => setAllValues(!allValues)}
        >
          {allValues ? "Show fewer" : `Show all ${detail.values.length.toLocaleString("en-IN")}`}
        </button>
      )}
    </section>
  );

  return (
    <div className="page page-wide">
      <p className="visually-hidden" aria-live="polite">
        {announcement}
      </p>
      <Toast message={toast} onClose={() => setToast(null)} />
      <div ref={layoutRef} className={styles.layout} style={{ "--split": `${split}%` } as CSSProperties}>
        <section ref={viewerRef} id="document-page" className={styles.viewer} aria-label="Page">
          {lowResolution && !page?.problems?.length && (
            <p className={styles.trashNotice} role="note">
              <Icon name="alert" />
              <span>{lowResolution}</span>
            </p>
          )}
          {/* SoW 19 Oct 2026: a PDF in extend-hq/ui's viewer, every page in one scroll; photos
              and sheets keep PageViewer. */}
          {page && detail.detected_type === "pdf" ? (
            <div className={styles.pdfPane}>
              {/* #246 A8: a throw in the viewer shows in this pane only, not the whole screen. */}
              <ViewerBoundary key={detail.id} download={download}>
                <PdfDocumentViewer
                  key={detail.id}
                  document={detail}
                  pageNo={page.page_no}
                  onPageChange={changePage}
                  highlights={highlightsOf(selection)}
                  highlightNote={highlightNote}
                  download={download}
                  marks={words.markers}
                  activeMark={words.activeId}
                  readingMarks={readingMarks}
                  onMark={showMark}
                  hotMark={hotMark}
                  onHotMark={setHotMark}
                  flags={openFlags}
                  activeFlag={reviewing ? currentFlag : null}
                  onFlag={pressFlag}
                  wordBoxes={wordBoxes}
                  wordHits={wordHits}
                />
              </ViewerBoundary>
            </div>
          ) : page ? (
            <PageViewer
              document={detail}
              page={page}
              highlights={highlightsOf(selection)}
              highlightNote={highlightNote}
              pageNumbers={pageNumbers}
              onPageChange={changePage}
              marks={words.markers}
              activeMark={words.activeId}
              fill={stickyViewer}
              readingMarks={readingMarks}
              onMark={showMark}
              hotMark={hotMark}
              onHotMark={setHotMark}
              flags={pageFlags}
              activeFlag={reviewing ? currentFlag : null}
              onFlag={pressFlag}
              wordBoxes={wordBoxes}
              wordHits={wordHits}
            />
          ) : (
            <p className={styles.noPages}>No pages to show yet.</p>
          )}
        </section>
        <SplitHandle split={split} onSplit={setSplit} layout={layoutRef} />
        <div className={styles.panels}>
          <header className={styles.header}>
            {/* What is this: back to where it is filed, the name, one status in words. */}
            <Link href={back.href} className={styles.back}>
              <Icon name="chevron-left" />
              <span>
                Back to <ScriptText text={back.name} />
              </span>
            </Link>
            <h1 className={styles.title}>
              <ScriptText text={detail.display_name} />
            </h1>
            <p className={styles.state}>
              <span className={styles.stateItem}>
                <KIcon name={STATUS_ICONS[chip.tone]} size="sm" />
                {chip.text}
              </span>
              {detail.status !== "ready" && <span className={styles.meta}>{detail.status_message}</span>}
              {detail.status === "ready" && detail.understanding === true && <span className={styles.meta}>{UNDERSTANDING_TEXT}</span>}
              <TimeLeft document={detail} />
              {/* File management P1: the label, quiet, with a lock when it is kept close. */}
              {detail.sensitivity && (
                <span className={styles.stateItem} title={LABEL_DESCRIPTIONS[detail.sensitivity]}>
                  {(detail.sensitivity === "confidential" || detail.sensitivity === "restricted") && <KIcon name="lock" size="sm" />}
                  <span className="visually-hidden">Label: </span>
                  {labelName(detail.sensitivity)}
                </span>
              )}
              {position && <span className={styles.meta}>{versionText(position)}</span>}
            </p>
            <AutofileNote
              document={detail}
              name={detail.display_name}
              filed={FILED_HERE_TEXT}
              folderHref={null}
              onUndone={() => setReloads((n) => n + 1)}
            />
            {readAgainKey > 0 && (
              <ReadAgain
                key={readAgainKey}
                documentId={detail.id}
                fixes={detail.reviewer_fixes}
                pages={detail.page_count}
                readModel={detail.read_model}
                reading={beingRead(detail.status)}
                onStarted={(reply) => {
                  merge(reply);
                  readingAgain.current = true;
                  poll.later();
                }}
                onClosed={() => menuButton.current?.focus()}
              />
            )}
            <SummaryCard detail={detail} active={selection?.region ?? null} onShow={showSpot} />
            {/* Can I trust it: one status line; the counts and this document's facts under More details. */}
            {detail.status !== "rejected" && (
              <SummaryBar
                documentId={detail.id}
                review={review}
                scanning={detail.status_message}
                reviewing={reviewing}
                reviewRef={reviewButton}
                onReview={() => startReview()}
                onEnsemble={(scope) => void ensemble.ask(scope)}
                ensembleBusy={ensemble.busy}
                ensembleError={ensemble.error}
                onExport={() => setExporting(true)}
                download={download}
                say={setAnnouncement}
                detailsId={DETAILS_ID}
                detailsOpen={moreOpen}
                onDetails={() => setMoreOpen(!moreOpen)}
              />
            )}
            <div id={DETAILS_ID} className={styles.status} hidden={!moreOpen && detail.status !== "rejected"}>
              <span className={styles.meta}>
                {typeLabel(detail.detected_type)}
                {/* The count line after it starts with the pages: said once ("PDF · 3 pages 3 pages · 180 words", QA pass, 25 Oct 2026). */}
                {!review.status?.summary && <span className="mono">{pagesText}</span>}
              </span>
              {review.status?.summary && <span className={`${styles.meta} tabular`}>{trustText(review.status)}</span>}
              {trail.length === 0 && <span className={styles.meta}>Not in a folder</span>}
              {detail.tags.length > 0 && (
                <div className={styles.placeItem}>
                  <span className={styles.placeLabel} id="document-tags-label">
                    Tags
                  </span>
                  <ul className={libraryStyles.tags} aria-labelledby="document-tags-label">
                    {detail.tags.map((tag) => (
                      <li key={tag.id} className={libraryStyles.tag}>
                        {tag.name}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {position && (
                <nav aria-label="Versions" className={styles.placeItem}>
                  <span className={styles.placeLabel}>{VERSION_TEXT.versions}</span>
                  <ol className={styles.versions}>
                    {sortedVersions(detail.versions).map((version) => {
                      const here = version.id === detail.id;
                      const words = `Version ${version.version_no}, uploaded ${formatDateTime(version.created_at)}${version.is_current ? ", used in search and chat" : ""}`;
                      return (
                        <li key={version.id}>
                          <Link
                            href={`/documents/${version.id}`}
                            className={`${styles.versionLink} mono`}
                            aria-current={here ? "page" : undefined}
                            aria-label={words}
                            title={words}
                          >
                            {version.version_no}
                          </Link>
                        </li>
                      );
                    })}
                  </ol>
                </nav>
              )}
            </div>
            {note && <p className={styles.note}>{note}</p>}
            {detail.newer_version && (
              <p className={styles.note}>
                <Link href={`/documents/${detail.newer_version.id}`}>
                  {newerVersionText(detail.newer_version)}
                </Link>
              </p>
            )}
            {detail.deleted_at !== null && (
              <p className={styles.trashNotice} role="status">
                <Icon name="trash" />
                <span>
                  In Trash since {formatDate(detail.deleted_at)}, so it is left out of search and chat.{" "}
                  <Link href="/documents?trash=1">Open Trash</Link> to restore it.
                </span>
              </p>
            )}
            {/* What can I do: Ask, the one primary; then one quiet row; the rest in the menu. */}
            {usedInChat(detail) && (
              <Link className={`btn btn-primary ${styles.ask}`} href={askAboutHref(detail.id)}>
                <Icon name="chat" />
                Ask about this document
              </Link>
            )}
            <div className={styles.headActions}>
              {download && (
                <a className="btn btn-quiet" href={download} download onClick={() => setToast("Download started.")}>
                  <Icon name="download" />
                  Download
                </a>
              )}
              {hasScannedText && (
                <button type="button" className="btn btn-quiet" onClick={openClear}>
                  <Icon name="scan-text" />
                  {CLEAN_COPY}
                </button>
              )}
              <DocumentMenu
                buttonRef={menuButton}
                detail={detail}
                readAgain={canReprocess(me.user.role, detail)}
                onReadAgain={() => setReadAgainKey((n) => n + 1)}
                onChanged={reloadWith}
                more={documentMoreItems(review.status, {
                  onEnsemble: (scope) => void ensemble.ask(scope),
                  onExport: () => setExporting(true),
                  download,
                })}
              />
            </div>
          </header>
          {/* File management V6, V7: a suggestion that this is a new version of another document. */}
          <VersionBanner detail={detail} onChanged={reloadWith} />
          <PanelTabs tabs={tabs} active={tab} onChange={setTab}>
            {(shownTab) =>
              shownTab === "overview" ? (
                <>
                  <DetailsPanel detail={detail} onSaved={reloadWith} />
                  {values}
                  <RelatedPanel detail={detail} />
                  <StoryPanel documentId={detail.id} inTrash={detail.deleted_at !== null} />
                </>
              ) : shownTab === "attention" && reviewing && review.status ? (
                <ReviewPanel
                  detail={detail}
                  review={review}
                  current={currentFlag}
                  onCurrent={toWord}
                  onExit={exitReview}
                  ensembleAllowed={review.status.ensemble.allowed && review.status.can_review}
                  onEnsemble={(scope, keys) => void ensemble.ask(scope, keys)}
                  say={setAnnouncement}
                  headingRef={reviewHeading}
                  onReadAgain={(reply) => {
                    merge(reply);
                    readingAgain.current = true;
                    poll.later();
                    exitReview();
                  }}
                />
              ) : shownTab === "attention" ? (
                <>
                  <AttentionPanel
                    fix={
                      page && (
                        <PageProblems
                          key={`${page.page_no}:${reloads}`}
                          inline
                          documentId={detail.id}
                          page={page}
                          userId={me.user.id}
                          canFix={canReview(me.user.role) && detail.deleted_at === null}
                          canUpload={detail.deleted_at === null}
                          onReadAgain={(reply) => {
                            merge(reply);
                            readingAgain.current = true;
                            poll.later();
                          }}
                          onKept={keepPage}
                        />
                      )
                    }
                    status={detail.status}
                    pages={detail.pages}
                    current={page?.page_no ?? null}
                    marks={readingMarks}
                    onPage={changePage}
                    onMark={showMark}
                    hot={hotMark}
                    onHot={setHotMark}
                  />
                </>
              ) : shownTab === "scanned" ? (
                page && (
                  <ScannedTextPanel
                    documentId={detail.id}
                    page={page}
                    voted={detail.values.filter((value) => value.votes && value.region.page === page.page_no)}
                    active={selection?.region ?? null}
                    cited={scannedParam === "1" ? decodeRegion(regionParam) : null}
                    onShow={showLine}
                    onClose={() => document.getElementById(tabId("scanned"))?.focus()}
                    onAddMeaning={addMeaning}
                    flags={pageFlags}
                    onFix={review.status?.can_review && detail.deleted_at === null ? startReview : undefined}
                    // Every flagged word holds its place; only a person's edit changes its text (an
                    // Ensemble proposal is not shown as fixed until a person takes it).
                    fixed={review.flags
                      .filter((flag) => flag.page_no === page.page_no)
                      .map((flag) => (flag.change?.action === "edit" ? flag : { ...flag, text: flag.as_read }))}
                  />
                )
              ) : shownTab === "clear" ? (
                <ReadClearlyPanel
                  documentId={detail.id}
                  active={selection?.region ?? null}
                  onShow={showLine}
                />
              ) : shownTab === "questions" ? (
                <QuestionsPanel documentId={detail.id} />
              ) : shownTab === "versions" ? (
                <VersionsPanel detail={detail} onChanged={reloadWith} />
              ) : shownTab === "activity" ? (
                <Timeline load={loadActivity} label={ACTIVITY_TEXT.heading} />
              ) : (
                page && <PageWordsList words={words} />
              )
            }
          </PanelTabs>
        </div>
      </div>
      {ensemble.sheet}
      {exporting && review.status && (
        <ExportDialog
          status={review.status}
          onSave={() => {
            // The dialog closes at once; a long document takes a moment, so say it is on its way.
            setToast("Preparing the text file…");
            exportData(detail).then(
              () => setToast("Saved as a text file."),
              () => setToast("The text could not be saved. Check your connection, then try again."),
            );
          }}
          onReview={() => {
            setExporting(false);
            startReview();
          }}
          onClose={() => setExporting(false)}
        />
      )}
      {adding && (
        <AddMeaningDialog
          documentId={detail.id}
          userId={me.user.id}
          text={adding.text}
          region={adding.region}
          onClose={() => setAdding(null)}
          onClosed={() => opener.current?.focus()}
          onSaved={(message) => {
            setAdding(null);
            setAnnouncement(message);
            words.reload();
          }}
        />
      )}
    </div>
  );
}

type TabId = "overview" | "attention" | "scanned" | "clear" | "questions" | "versions" | "activity" | "words";
type TabItem = { id: TabId; label: string; count?: number };
const tabId = (id: TabId) => `document-tab-${id}`;
const tabPanelId = (id: TabId) => `document-panel-${id}`;
const TAB_BAR_ID = "document-tabs";

/**
 * The panels' tabs (UI audit DD6), the MonoEdge tabs: role="tablist" of role="tab"
 * buttons (roving tabindex; Left/Right, Home/End), the bold width kept (data-label),
 * the count in tabular figures. Every panel reserves the same least height, and only the
 * open one is drawn. Document panel UX (25 Oct 2026): as many as fit show, four at most
 * (tabsThatFit), the open one always among them; the rest are under More, so the row never runs off.
 */
function PanelTabs({
  tabs,
  active,
  onChange,
  children,
}: {
  tabs: TabItem[];
  active: TabId;
  onChange: (id: TabId) => void;
  children: (tab: TabId) => ReactNode;
}) {
  const bar = useRef<HTMLDivElement>(null);
  const room = useTabRoom(bar, tabs.some((item) => item.id === "attention"));
  const at = tabs.findIndex((item) => item.id === active);
  const shown = at >= room ? [...tabs.slice(0, room - 1), ...tabs.slice(at, at + 1)] : tabs.slice(0, room);
  const more = tabs.filter((item) => !shown.includes(item));
  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const next = tabAfterKey(index, event.key, shown.length);
    const to = next === null ? undefined : shown[next];
    if (!to) return;
    event.preventDefault();
    onChange(to.id);
    document.getElementById(tabId(to.id))?.focus();
  }
  const list = useRef<HTMLDivElement>(null);
  useTabMotion(list, active);
  return (
    <>
      <div ref={bar} id={TAB_BAR_ID} className={styles.tabBar}>
        <div ref={list} role="tablist" aria-label="About this document" className={`tabs ${styles.tabs}`}>
          {shown.map((item, index) => (
            <button
              key={item.id}
              type="button"
              role="tab"
              id={tabId(item.id)}
              className="tab"
              aria-selected={item.id === active}
              aria-controls={tabPanelId(item.id)}
              tabIndex={item.id === active ? 0 : -1}
              onClick={() => onChange(item.id)}
              onKeyDown={(event) => onKeyDown(event, index)}
            >
              <span data-label={item.label}>{item.label}</span>
              {item.count !== undefined && (
                <span
                  className={`tab-count ${styles.tabCount}`}
                  data-zero={item.count === 0 ? "true" : undefined}
                  data-alert={item.id === "attention" ? "true" : undefined}
                >
                  {item.count.toLocaleString("en-IN")}
                </span>
              )}
            </button>
          ))}
        </div>
        <RowMenu
          name="the other tabs"
          compact={room < 3}
          items={more.map((item) => ({
            label: item.count === undefined ? item.label : `${item.label} ${item.count.toLocaleString("en-IN")}`,
            onSelect: () => onChange(item.id),
          }))}
        />
      </div>
      <div
        role="tabpanel"
        id={tabPanelId(active)}
        aria-labelledby={tabId(active)}
        tabIndex={0}
        className={styles.tabPanel}
      >
        {children(active)}
      </div>
    </>
  );
}

/**
 * The handle between the page and the panels (UI audit DD5): a vertical separator. A
 * press becomes a drag only past 5px (DRAG_THRESHOLD_PX), so a click never resizes;
 * while dragging, only a CSS variable changes, and the share is kept when it ends.
 * Left/Right move it a step, Home/End to its ends. Shown only beside the sticky column.
 */
function SplitHandle({
  split,
  onSplit,
  layout,
}: {
  split: number;
  onSplit: (next: number) => void;
  layout: RefObject<HTMLDivElement | null>;
}) {
  const drag = useRef<{ x: number; width: number; moving: boolean } | null>(null);
  const at = (clientX: number) => {
    const press = drag.current;
    return press ? splitAfterDrag(split, clientX - press.x, press.width) : split;
  };
  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize the page and the panels"
      aria-controls="document-page"
      aria-valuemin={SPLIT.min}
      aria-valuemax={SPLIT.max}
      aria-valuenow={split}
      tabIndex={0}
      className={styles.handle}
      onPointerDown={(event) => {
        if (event.button !== 0) return;
        drag.current = { x: event.clientX, width: layout.current?.clientWidth ?? 0, moving: false };
        event.currentTarget.setPointerCapture(event.pointerId);
      }}
      onPointerMove={(event) => {
        const press = drag.current;
        if (!press) return;
        if (!press.moving && Math.abs(event.clientX - press.x) < DRAG_THRESHOLD_PX) return;
        press.moving = true;
        layout.current?.style.setProperty("--split", `${at(event.clientX)}%`);
      }}
      onPointerUp={(event) => {
        if (drag.current?.moving) onSplit(at(event.clientX));
        drag.current = null;
      }}
      onPointerCancel={() => {
        drag.current = null;
        layout.current?.style.setProperty("--split", `${split}%`);
      }}
      onKeyDown={(event) => {
        const next = splitAfterKey(split, event.key);
        if (next === null) return;
        event.preventDefault();
        onSplit(next);
      }}
    />
  );
}

/**
 * The reading problems of the page shown (PRD 5 U3), from its readings: only for an
 * uncertain or unreadable page, asked again after each reload. [] until they come, and
 * if they can't be loaded (the banner still says what is wrong).
 */
function usePageMarks(documentId: string, page: PageSummary | undefined, version: number): ReadingMark[] {
  const pageNo = page && page.read_quality !== "good" ? page.page_no : null;
  const key = pageNo === null ? null : `${documentId}:${pageNo}:${version}`;
  const [loaded, setLoaded] = useState<{ key: string; marks: ReadingMark[] } | null>(null);
  useEffect(() => {
    if (key === null || pageNo === null) return;
    let cancelled = false;
    getPageReadings(documentId, pageNo).then(
      (readings) => {
        if (!cancelled) setLoaded({ key, marks: readings.marks ?? [] });
      },
      () => undefined,
    );
    return () => {
      cancelled = true;
    };
  }, [documentId, pageNo, key]);
  return loaded && loaded.key === key ? loaded.marks : NO_MARKS;
}

const NO_MARKS: ReadingMark[] = [];

/** How many tabs fit the tab row (tabsThatFit), from its width in rem; four until it is measured. */
function useTabRoom(bar: RefObject<HTMLDivElement | null>, long: boolean): number {
  const [width, setWidth] = useState<number | null>(null);
  useEffect(() => {
    const element = bar.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry) setWidth(entry.contentRect.width / parseFloat(getComputedStyle(document.documentElement).fontSize));
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [bar]);
  return tabsThatFit(width, long);
}

/**
 * Package H: the lines holding the link's words on every page, and every page with one;
 * asked once a document, words and reload (loadDocumentWords), never per page, and kept
 * while the pages change (a PDF's scroll changes them page by page). null until they come,
 * with no words, and if they can't be loaded (the page shows as it would without them).
 */
function useDocumentWords(documentId: string, words: readonly string[], version: number): DocumentWords | null {
  const wanted = words.join(" ");
  const key = wanted === "" ? null : `${documentId}:${version}:${wanted}`;
  const [loaded, setLoaded] = useState<{ key: string; found: DocumentWords } | null>(null);
  useEffect(() => {
    if (key === null) return;
    let cancelled = false;
    loadDocumentWords(documentId, wanted.split(" "), version).then(
      (found) => {
        if (!cancelled) setLoaded({ key, found });
      },
      () => undefined,
    );
    return () => {
      cancelled = true;
    };
  }, [documentId, key, wanted, version]);
  return loaded && loaded.key === key ? loaded.found : null;
}

/** Said once over Values found, for the values the AI picked (24 Oct 2026: not on every row). */
export const AI_FOUND_NOTE = "Values marked Ready to verify were found by the AI. Check each one against its page.";
const NO_REGIONS: Region[] = [];
const NO_HITS: number[] = [];
