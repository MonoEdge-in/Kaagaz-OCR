"use client";

// The document screen: the title with Download original and Ask beside it, then
// one meta line (board 12 P4) with the status, where it is filed (its folder
// path, its tags) and its versions ("Version 2 of 2", each a link to that
// version's own id), the page viewer and, beside it, the panels with the
// values table right after Attention (side by side from 1024 px, stacked
// below). A newer version still being read, or one that failed, gets a line
// linking to it. A document in Trash says so. A reviewer
// or an admin can have it read again (ReadAgain.tsx, PRD 2 X1), and anyone can
// "Ask about this document" in chat (PRD 3 H2). While it is checked or read,
// on load too, its summary is asked for again (lib/poll.ts); once it settles,
// the whole detail is asked for once, and the screen says what was found.
// Clicking a value jumps to its page and highlights its spot. Every value is
// shown as plain text; it came from the document. Beside the page: the
// document card, related documents and the invoice (DocumentPanels.tsx,
// PRD 3 Y1 to Y3); a low-resolution page says so above it (Y4). Scanned text:
// see ScannedTextPanel.tsx. My words: "Add meaning" by a value or a scanned
// line, and the user's own words on the page (components/words/ViewerWords.tsx),
// listed first beside it. Last beside it: the user's own story about the
// document (StoryPanel.tsx). On a window at least 1024 px wide and 36rem tall
// the page column sticks and shows the whole page (STICKY_VIEWER_QUERY,
// "Fit page"), so it never needs a scrollbar of its own. A page with reading
// problems (PRD 5) gets a banner with its fixes (PageProblems.tsx, U4), its marks
// drawn on it (U3, from the page's readings) and the Attention panel (U5); a mark
// pressed shows its spot and opens the Scanned text panel, with both readings.
// With the column sticky, the reading problems banner and the Scanned text panel
// sit atop the panels, so the page keeps its height; opened by its button, focus
// goes to the Scanned text panel's heading. A family Kaagaz filed by its type says
// "Filed here by Kaagaz · Undo" by its folder (feature 28, AutofileNote).

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useMediaQuery } from "@/components/chat/hooks";
import { askAboutHref } from "@/components/chat/logic";
import { downloadHref, folderTrail } from "@/components/documents/library";
import libraryStyles from "@/components/documents/Library.module.css";
import { ALL_DOCUMENTS, libraryHref } from "@/components/documents/libraryQuery";
import { FILED_HERE_TEXT } from "@/components/documents/batchTray";
import { StatusChip } from "@/components/documents/StatusChip";
import { TimeLeft } from "@/components/documents/TimeLeft";
import { AutofileNote } from "@/components/documents/UploadArea";
import { Icon } from "@/components/ui/Icon";
import { PageViewer } from "@/components/viewer/PageViewer";
import { STICKY_VIEWER_QUERY, bringPageForSpot } from "@/components/viewer/viewer";
import { SCANNED_PANEL_ID, ScannedTextPanel } from "@/components/viewer/ScannedTextPanel";
import { WhatWasRead } from "@/components/viewer/WhatWasRead";
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
import { getDocument, getDocumentSummary, getPageReadings } from "@/lib/api";
import { formatDate, formatDateTime, typeLabel } from "@/lib/format";
import { progressSignature } from "@/lib/poll";
import { markReceived } from "@/lib/timeLeft";
import { decodeRegion } from "@/lib/regions";
import { useSession } from "@/lib/session";
import type { DocumentDetail, DocumentSummary, ExtractedValue, PageSummary, ReadingMark, Region } from "@/lib/types";
import { usePoll } from "@/lib/usePoll";
import { AttentionPanel, CardPanel, InvoicePanel, RelatedPanel } from "./DocumentPanels";
import { PageProblems } from "./PageProblems";
import { ReadAgain } from "./ReadAgain";
import { StoryPanel } from "./StoryPanel";
import styles from "./DocumentView.module.css";

export interface DocumentViewProps {
  documentId: string;
  pageParam: string | null;
  regionParam: string | null;
  scannedParam: string | null;
}

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
  // The Scanned text panel: open as the link says (?scanned=1) until it is toggled.
  const [scannedChoice, setScannedChoice] = useState<{ linkKey: string; open: boolean } | null>(null);
  const scannedButton = useRef<HTMLButtonElement>(null);
  const focusScanned = useRef(false); // opened by its button: focus its heading once shown
  const [announcement, setAnnouncement] = useState("");
  const viewerRef = useRef<HTMLElement>(null);
  // Set when a poll sees reading end: the next detail says what was found ("again": after Read again).
  const settling = useRef<"read" | "again" | null>(null);
  const readingAgain = useRef(false);
  // "Add meaning": what it is for, and the button that opened it (focus goes back there).
  const [adding, setAdding] = useState<{ text: string; region: Region } | null>(null);
  const opener = useRef<HTMLElement | null>(null);

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
      settling.current = readingAgain.current ? "again" : "read";
      readingAgain.current = false;
      setReloads((n) => n + 1);
    },
    onFailure: () => undefined, // it keeps trying; the page shows what it has
    again: (summary) => beingRead(summary.status),
    signature: (summary) => progressSignature([summary]),
  });

  useEffect(() => {
    let cancelled = false;
    getDocument(documentId)
      .then((detail) => {
        if (cancelled) return;
        setLoaded({ attempt, detail });
        if (beingRead(detail.status)) poll.later();
        else if (settling.current) setAnnouncement(settledText(detail, settling.current === "again"));
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
  // A reading problem pressed (U3): its spot on the page, and the line with both readings.
  const showMark = (mark: ReadingMark, name: string) => {
    choose(selectSpot(mark.region, null));
    setAnnouncement(`Showing ${name} on page ${mark.region.page}.`);
    if (mark.region.kind === "box") setScanned(true);
  };
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
  const scannedOpen =
    scannedChoice && scannedChoice.linkKey === linkKey ? scannedChoice.open : scannedParam === "1";
  const setScanned = (open: boolean) => setScannedChoice({ linkKey, open });
  const closeScanned = () => {
    setScanned(false);
    scannedButton.current?.focus();
  };
  const hasScannedText = page !== undefined && page.unit !== "cells";
  const lowResolution = page ? lowResolutionText(page) : null;
  const panel = { detail, active: selection?.region ?? null, onShow: showSpot };
  const scannedPanel = hasScannedText && scannedOpen && (
    <ScannedTextPanel
      documentId={detail.id}
      page={page}
      voted={detail.values.filter((value) => value.votes && value.region.page === page.page_no)}
      active={selection?.region ?? null}
      cited={scannedParam === "1" ? decodeRegion(regionParam) : null}
      onShow={showLine}
      onClose={closeScanned}
      onAddMeaning={addMeaning}
      headingRef={(heading) => {
        if (heading && focusScanned.current) heading.focus();
        focusScanned.current = false;
      }}
    />
  );

  const problemsBanner = page && (
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
  );

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

  return (
    <div className="page page-wide">
      <header className={styles.header}>
        <BackLink />
        <div className={styles.titleRow}>
          <h1 className={styles.title}>{detail.display_name}</h1>
          {download && (
            <a className={`btn btn-quiet ${styles.download}`} href={download} download>
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
        </div>
        <div className={styles.status}>
          <StatusChip status={detail.status} />
          <span>{detail.status_message}</span>
          <TimeLeft document={detail} />
          <span className={styles.meta}>
            {typeLabel(detail.detected_type)}
            {pagesText}
          </span>
          <ReadAgain
            documentId={detail.id}
            fixes={detail.reviewer_fixes}
            pages={detail.page_count}
            readModel={detail.read_model}
            offered={canReprocess(me.user.role, detail)}
            reading={beingRead(detail.status)}
            onStarted={(reply) => {
              merge(reply);
              readingAgain.current = true;
              poll.later();
            }}
          />
          <nav aria-label="Folder" className={`${libraryStyles.crumbs} ${styles.placeItem}`}>
            <span className={styles.placeLabel}>Folder</span>
            {trail.length === 0 ? (
              <span className={styles.meta}>Not in a folder</span>
            ) : (
              <ol>
                {trail.map((folder) => (
                  <li key={folder.id}>
                    <Link href={libraryHref({ ...ALL_DOCUMENTS, folderId: folder.id })}>
                      {folder.name}
                    </Link>
                  </li>
                ))}
              </ol>
            )}
            <AutofileNote
              document={detail}
              name={detail.display_name}
              filed={FILED_HERE_TEXT}
              folderHref={null}
              onUndone={() => setReloads((n) => n + 1)}
            />
          </nav>
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
                        className={styles.versionLink}
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
      </header>
      <p className="visually-hidden" aria-live="polite">
        {announcement}
      </p>
      <div className={styles.layout}>
        <section ref={viewerRef} className={styles.viewer} aria-label="Page">
          {!stickyViewer && problemsBanner}
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
              pageNumbers={pageNumbers}
              onPageChange={changePage}
              marks={words.markers}
              activeMark={words.activeId}
              fit={stickyViewer ? "page" : "width"}
              readingMarks={readingMarks}
              onMark={showMark}
              tools={
                hasScannedText && (
                  <button
                    ref={scannedButton}
                    type="button"
                    className="btn"
                    aria-expanded={scannedOpen}
                    aria-controls={scannedOpen ? SCANNED_PANEL_ID : undefined}
                    onClick={() => {
                      focusScanned.current = !scannedOpen;
                      setScanned(!scannedOpen);
                    }}
                  >
                    <Icon name="scan-text" />
                    Scanned<span className="narrow-hide"> text</span>
                  </button>
                )
              }
              belowToolbar={stickyViewer ? undefined : scannedPanel}
            />
          ) : (
            <p className={styles.noPages}>No pages to show yet.</p>
          )}
        </section>
        <div className={styles.panels}>
          {stickyViewer && problemsBanner}
          {stickyViewer && scannedPanel}
          {page && <PageWordsList words={words} />}
          <AttentionPanel
            status={detail.status}
            pages={detail.pages}
            current={page?.page_no ?? null}
            marks={readingMarks}
            onPage={changePage}
            onMark={showMark}
          />
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
                      Label
                    </th>
                    <th scope="col" role="columnheader">
                      Reason
                    </th>
                  </tr>
                </thead>
                <tbody role="rowgroup">
                  {detail.values.map((value) => {
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
                          <LabelBadge label={value.label} />
                        </td>
                        <td role="cell" className={styles.reasonCell}>
                          {reasonText(value.label_reason)}
                          {value.votes && <div className={styles.pageNote}>{voteText(value.votes)}</div>}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </section>
          <CardPanel {...panel} />
          <RelatedPanel detail={detail} />
          <InvoicePanel {...panel} />
          <StoryPanel documentId={detail.id} inTrash={detail.deleted_at !== null} />
        </div>
      </div>
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
