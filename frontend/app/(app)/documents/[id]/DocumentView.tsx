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
// Overview (the document card, Values found, the invoice, related documents and the
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

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import type { CSSProperties, KeyboardEvent, ReactNode, RefObject } from "react";
import { tabAfterKey } from "@/components/admin/logic";
import { useMediaQuery } from "@/components/chat/hooks";
import { askAboutHref } from "@/components/chat/logic";
import { downloadHref, folderTrail } from "@/components/documents/library";
import libraryStyles from "@/components/documents/Library.module.css";
import { ALL_DOCUMENTS, libraryHref } from "@/components/documents/libraryQuery";
import { FILED_HERE_TEXT } from "@/components/documents/batchTray";
import { UNDERSTANDING_TEXT } from "@/components/documents/logic";
import { StatusChip } from "@/components/documents/StatusChip";
import { TimeLeft } from "@/components/documents/TimeLeft";
import { AutofileNote } from "@/components/documents/UploadArea";
import { Icon } from "@/components/ui/Icon";
import { PageViewer } from "@/components/viewer/PageViewer";
import {
  DRAG_THRESHOLD_PX,
  SPLIT,
  STICKY_VIEWER_QUERY,
  bringPageForSpot,
  splitAfterDrag,
  splitAfterKey,
} from "@/components/viewer/viewer";
import { ScannedTextPanel } from "@/components/viewer/ScannedTextPanel";
import { valueAt } from "@/components/viewer/scanned";
import { WhatWasRead } from "@/components/viewer/WhatWasRead";
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
} from "@/components/viewer/versions";
import { LabelBadge } from "@/components/viewer/LabelBadge";
import { fieldText, reasonText, voteText } from "@/components/viewer/labels";
import { lowResolutionText, untilRead } from "@/components/viewer/panels";
import type { Spot } from "@/components/viewer/panels";
import { beingRead, canReprocess, loadFailure, settledText, valuesNote } from "@/components/viewer/reprocess";
import { highlightsOf, initialSelection, pageChange } from "@/components/viewer/selection";
import { selectSpot, selectValue } from "@/components/viewer/selection";
import type { Selection } from "@/components/viewer/selection";
import { canReview } from "@/components/review/review";
import { flagDomId, trustText } from "@/components/review/wordReview";
import { getDocument, getDocumentSummary, getPageReadings } from "@/lib/api";
import { formatDate, formatDateTime, typeLabel } from "@/lib/format";
import { progressSignature } from "@/lib/poll";
import { markReceived } from "@/lib/timeLeft";
import { decodeRegion } from "@/lib/regions";
import { useSession } from "@/lib/session";
import type {
  DocumentDetail,
  DocumentSummary,
  ExtractedValue,
  PageSummary,
  ReadingMark,
  Region,
  WordFlag,
} from "@/lib/types";
import { usePoll } from "@/lib/usePoll";
import { AttentionPanel, CardPanel, InvoicePanel, RelatedPanel, attentionCount } from "./DocumentPanels";
import { DocumentMenu } from "./DocumentMenu";
import { PageProblems } from "./PageProblems";
import { ReadAgain } from "./ReadAgain";
import { ReviewPanel } from "./ReviewPanel";
import { StoryPanel } from "./StoryPanel";
import styles from "./DocumentView.module.css";
import { ExportDialog, SummaryBar, useEnsembleStart, useWordReview } from "./WordReview";

export interface DocumentViewProps {
  documentId: string;
  pageParam: string | null;
  regionParam: string | null;
  scannedParam: string | null;
}

const FIRST_VALUES = 6; // shown before "Show all N"
// Said after "Highlighted: <field>" when the spot came in a link (a source card, a search
// hit or a review item), not from a pick on this screen.
const FROM_LINK = ", from the link you opened";

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

  // While it is read: merge each summary; once it settles, ask for the detail once.
  const merge = (summary: DocumentSummary) =>
    setLoaded((shown) =>
      shown && "detail" in shown && shown.detail.id === summary.id
        ? { ...shown, detail: { ...shown.detail, ...summary } }
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

  if (!current) {
    return (
      <div className="page page-wide">
        <BackLink />
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
  const tabs: TabItem[] = [
    { id: "overview", label: "Overview" },
    { id: "attention", label: "Needs attention", count: attentionCount(detail.pages) },
    ...(hasScannedText ? [{ id: "scanned" as const, label: "Scanned text" }] : []),
    { id: "words", label: "My words" },
  ];
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
  const exitReview = () => {
    setReviewing(false);
    const word = currentFlag;
    requestAnimationFrame(() => {
      (document.getElementById(flagDomId(word ?? "")) ?? reviewButton.current)?.focus();
    });
  };
  const panel = { detail, active: selection?.region ?? null, onShow: showSpot };

  const pagesText =
    detail.page_count !== null
      ? ` · ${detail.page_count} ${detail.page_count === 1 ? "page" : "pages"}`
      : "";
  const trail = detail.folder ? folderTrail(detail.folder) : [];
  // Trash means deleted: no download from there, only Restore (the Trash view).
  const download = detail.deleted_at === null ? downloadHref(detail) : null;
  const position = versionPosition(detail);
  const note = versionNote(detail);
  const readingNote = valuesNote(detail.status, detail.values.length);
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
    setAnnouncement(message);
    setReloads((n) => n + 1);
  };

  const values = (
    <section className={styles.values} aria-labelledby="values-heading">
      <h2 id="values-heading" className={styles.valuesHeading}>
        Values found
        {detail.values.length > 0 ? (
          <span className={`${styles.count} tabular`}>{detail.values.length}</span>
        ) : null}
      </h2>
      {readingNote && <p className="muted">{readingNote}</p>}
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
                        className={styles.valueButton}
                        aria-pressed={active}
                        onClick={() => showValue(value)}
                      >
                        {value.value}
                        <span className="visually-hidden">
                          {`, show on page ${value.region.page}`}
                        </span>
                      </button>
                      <button
                        type="button"
                        className={`btn btn-quiet ${wordStyles.target}`}
                        onClick={(event) => addMeaning(value.value, value.region, event.currentTarget)}
                      >
                        Add meaning
                        <span className="visually-hidden">{` for ${fieldText(value.field)}`}</span>
                      </button>
                      <div className={styles.pageNote}>
                        Page {value.region.page}
                        {active ? ", highlighted on the page" : ""}
                      </div>
                      <WhatWasRead
                        value={value}
                        page={detail.pages.find((p) => p.page_no === value.region.page)}
                      />
                    </td>
                    <td role="cell" className={styles.labelCell}>
                      <LabelBadge label={value.label} confidence={value.confidence ?? null} />
                      <div className={styles.reason}>
                        {reasonText(value.label_reason)}
                        {value.votes && <div className={styles.pageNote}>{voteText(value.votes)}</div>}
                      </div>
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
      <header className={styles.header}>
        {/* DD2: where it is filed, as a path: Documents / Folder / name. */}
        <nav aria-label="Breadcrumb" className={styles.crumbs}>
          <ol>
            <li>
              <Link href={detail.deleted_at === null ? "/documents" : "/documents?trash=1"}>
                {detail.deleted_at === null ? "Documents" : "Trash"}
              </Link>
            </li>
            {trail.map((folder) => (
              <li key={folder.id}>
                <Link href={libraryHref({ ...ALL_DOCUMENTS, folderId: folder.id })}>{folder.name}</Link>
              </li>
            ))}
            <li aria-current="page" title={detail.display_name}>
              <span className="mono">{detail.display_name}</span>
            </li>
          </ol>
        </nav>
        {/* DD2: one row. The title and its status; then Download, Ask (the one primary), the menu. */}
        <div className={styles.titleRow}>
          <h1 className={styles.title}>{detail.display_name}</h1>
          <StatusChip status={detail.status} />
          <div className={styles.headActions}>
            {download && (
              <a className={`btn ${styles.download}`} href={download} download>
                <Icon name="download" />
                <span className="narrow-hide">Download original</span>
              </a>
            )}
            {usedInChat(detail) && (
              <Link className={`btn btn-primary ${styles.ask}`} href={askAboutHref(detail.id)}>
                <Icon name="chat" />
                Ask about this document
              </Link>
            )}
            <DocumentMenu
              buttonRef={menuButton}
              detail={detail}
              readAgain={canReprocess(me.user.role, detail)}
              onReadAgain={() => setReadAgainKey((n) => n + 1)}
              onChanged={reloadWith}
            />
          </div>
        </div>
        <div className={styles.status}>
          {/* D4 / DD2: a ready document is its badge alone; other states keep the server's words. */}
          {detail.status !== "ready" && <span>{detail.status_message}</span>}
          {detail.status === "ready" && detail.understanding === true && <span>{UNDERSTANDING_TEXT}</span>}
          <TimeLeft document={detail} />
          <span className={styles.meta}>
            {typeLabel(detail.detected_type)}
            <span className="mono">{pagesText}</span>
          </span>
          {review.status?.summary && <span className={`${styles.meta} tabular`}>{trustText(review.status)}</span>}
          {trail.length === 0 && <span className={styles.meta}>Not in a folder</span>}
          <AutofileNote
            document={detail}
            name={detail.display_name}
            filed={FILED_HERE_TEXT}
            folderHref={null}
            onUndone={() => setReloads((n) => n + 1)}
          />
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
              <span className={styles.placeLabel}>{versionText(position)}</span>
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
      </header>
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
        />
      )}
      <p className="visually-hidden" aria-live="polite">
        {announcement}
      </p>
      <div ref={layoutRef} className={styles.layout} style={{ "--split": `${split}%` } as CSSProperties}>
        <section ref={viewerRef} id="document-page" className={styles.viewer} aria-label="Page">
          {lowResolution && !page?.problems?.length && (
            <p className={styles.trashNotice} role="note">
              <Icon name="alert" />
              <span>{lowResolution}</span>
            </p>
          )}
          {page ? (
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
              onFlag={startReview}
            />
          ) : (
            <p className={styles.noPages}>No pages to show yet.</p>
          )}
        </section>
        <SplitHandle split={split} onSplit={setSplit} layout={layoutRef} />
        <div className={styles.panels}>
          <PanelTabs tabs={tabs} active={tab} onChange={setTab}>
            {(shownTab) =>
              shownTab === "overview" ? (
                <>
                  <CardPanel {...panel} />
                  {values}
                  <InvoicePanel {...panel} />
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
                />
              ) : shownTab === "attention" ? (
                <>
                  {page && (
                    <PageProblems
                      key={`${page.page_no}:${reloads}`}
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
                  )}
                  <AttentionPanel
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
                  />
                )
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
          download={download}
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

type TabId = "overview" | "attention" | "scanned" | "words";
type TabItem = { id: TabId; label: string; count?: number };
const tabId = (id: TabId) => `document-tab-${id}`;
const tabPanelId = (id: TabId) => `document-panel-${id}`;

/**
 * The panels' tabs (UI audit DD6), the MonoEdge tabs: role="tablist" of role="tab"
 * buttons (roving tabindex; Left/Right, Home/End), the bold width kept (data-label),
 * the count in tabular figures; one row that scrolls sideways, never wraps. Every
 * panel reserves the same least height, and only the open one is drawn.
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
  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const next = tabAfterKey(index, event.key, tabs.length);
    const to = next === null ? undefined : tabs[next];
    if (!to) return;
    event.preventDefault();
    onChange(to.id);
    document.getElementById(tabId(to.id))?.focus();
  }
  const list = useRef<HTMLDivElement>(null);
  useTabMotion(list, active);
  return (
    <>
      <div ref={list} role="tablist" aria-label="About this document" className={`tabs ${styles.tabs}`}>
        {tabs.map((item, index) => (
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
              <span className={`tab-count ${styles.tabCount}`} data-zero={item.count === 0 ? "true" : undefined}>
                {item.count.toLocaleString("en-IN")}
              </span>
            )}
          </button>
        ))}
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
