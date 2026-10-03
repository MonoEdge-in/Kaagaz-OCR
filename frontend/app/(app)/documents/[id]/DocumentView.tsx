"use client";

// The document screen: header and status, where it is filed (its folder path,
// its tags, Download original), its versions ("Version 2 of 2", each a link to
// that version's own id), the page viewer and the values table (side by side
// from 1024 px, stacked below). A newer version still being read, or one that
// failed, gets a line linking to it. A document in Trash says so. A reviewer
// or an admin can have it read again (ReadAgain.tsx, PRD 2 X1). While it is
// checked or read, on load too, its summary is asked for again (lib/poll.ts);
// once it settles, the whole detail is asked for once, and the screen says
// what was found. Clicking a value jumps to its page and highlights its spot.
// Every value is shown as plain text; it came from the document. Beside the
// page: the document card, related documents and the invoice
// (DocumentPanels.tsx, PRD 3 Y1 to Y3); a low-resolution page says so above it (Y4).

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { downloadHref, folderTrail } from "@/components/documents/library";
import libraryStyles from "@/components/documents/Library.module.css";
import { ALL_DOCUMENTS, libraryHref } from "@/components/documents/libraryQuery";
import { StatusChip } from "@/components/documents/StatusChip";
import { Icon } from "@/components/ui/Icon";
import { PageViewer } from "@/components/viewer/PageViewer";
import {
  newerVersionText,
  sortedVersions,
  versionNote,
  versionPosition,
  versionText,
} from "@/components/viewer/versions";
import { LabelBadge } from "@/components/viewer/LabelBadge";
import { fieldText, reasonText } from "@/components/viewer/labels";
import { lowResolutionText } from "@/components/viewer/panels";
import type { Spot } from "@/components/viewer/panels";
import { beingRead, canReprocess, settledText, valuesNote } from "@/components/viewer/reprocess";
import { highlightsOf, initialSelection, pageChange } from "@/components/viewer/selection";
import { selectSpot, selectValue } from "@/components/viewer/selection";
import type { Selection } from "@/components/viewer/selection";
import { ApiError, getDocument, getDocumentSummary } from "@/lib/api";
import { formatDate, formatDateTime, typeLabel } from "@/lib/format";
import { progressSignature } from "@/lib/poll";
import { useSession } from "@/lib/session";
import type { DocumentDetail, DocumentSummary, ExtractedValue } from "@/lib/types";
import { usePoll } from "@/lib/usePoll";
import { CardPanel, InvoicePanel, RelatedPanel } from "./DocumentPanels";
import { ReadAgain } from "./ReadAgain";
import styles from "./DocumentView.module.css";

export interface DocumentViewProps {
  documentId: string;
  pageParam: string | null;
  regionParam: string | null;
}

type Loaded = { attempt: number; detail: DocumentDetail } | { attempt: number; error: string };

function BackLink() {
  return (
    <Link href="/documents" className={styles.back}>
      <Icon name="chevron-left" />
      Back to documents
    </Link>
  );
}

export function DocumentView({ documentId, pageParam, regionParam }: DocumentViewProps) {
  const { me } = useSession();
  const [attempt, setAttempt] = useState(0);
  // Asks again but keeps showing what is loaded until the answer comes (once reading settles).
  const [reloads, setReloads] = useState(0);
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  // A choice made on this screen, remembered with the link it was made under,
  // so following a new link (new ?page / ?region) shows that link's spot.
  const linkKey = `${pageParam ?? ""}|${regionParam ?? ""}`;
  const [chosen, setChosen] = useState<{ linkKey: string; selection: Selection } | null>(null);
  const [announcement, setAnnouncement] = useState("");
  const viewerRef = useRef<HTMLElement>(null);
  // Set when a poll sees reading end: the next detail says what was found ("again": after Read again).
  const settling = useRef<"read" | "again" | null>(null);
  const readingAgain = useRef(false);

  // While it is read: merge each summary; once it settles, ask for the detail once.
  const merge = (summary: DocumentSummary) =>
    setLoaded((shown) =>
      shown && "detail" in shown && shown.detail.id === summary.id
        ? { ...shown, detail: { ...shown.detail, ...summary } }
        : shown,
    );
  const poll = usePoll<DocumentSummary>({
    load: () => getDocumentSummary(documentId),
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
        const message =
          error instanceof ApiError
            ? error.message
            : "Check your internet connection, then try again.";
        if (!cancelled) setLoaded({ attempt, error: message });
      });
    return () => {
      cancelled = true;
      poll.stop();
    };
  }, [poll, documentId, attempt, reloads]);

  // A retry shows the loading state again until its own answer arrives.
  const current = loaded && loaded.attempt === attempt ? loaded : null;

  if (!current) {
    return (
      <div className="page">
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
      <div className="page">
        <BackLink />
        <div className={styles.errorBanner} role="alert">
          <Icon name="alert" className={styles.errorIcon} />
          <div className={styles.errorBody}>
            <p className={styles.errorTitle}>This document could not be shown.</p>
            <p>{current.error}</p>
          </div>
          <button type="button" className="btn" onClick={() => setAttempt((n) => n + 1)}>
            <Icon name="refresh" />
            Try again
          </button>
        </div>
      </div>
    );
  }

  const detail = current.detail;
  const selection =
    chosen && chosen.linkKey === linkKey
      ? chosen.selection
      : initialSelection(detail, pageParam, regionParam);
  const page = selection ? detail.pages.find((p) => p.page_no === selection.pageNo) : undefined;
  const pageNumbers = detail.pages.map((p) => p.page_no);

  const choose = (next: Selection) => setChosen({ linkKey, selection: next });
  const showValue = (value: ExtractedValue) => {
    choose(selectValue(value));
    setAnnouncement(`Showing ${fieldText(value.field)} on page ${value.region.page}.`);
    // Stacked on a phone, the page is above the list: bring it into view.
    viewerRef.current?.scrollIntoView({ block: "nearest" });
  };
  const showSpot = (spot: Spot) => {
    choose(selectSpot(spot.region, spot.label));
    setAnnouncement(`Showing ${spot.name} on page ${spot.region.page}.`);
    viewerRef.current?.scrollIntoView({ block: "nearest" });
  };
  const changePage = (pageNo: number) => choose(pageChange(selection, pageNo));
  const lowResolution = page ? lowResolutionText(page) : null;
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

  return (
    <div className="page">
      <header className={styles.header}>
        <BackLink />
        <h1 className={styles.title}>{detail.display_name}</h1>
        <div className={styles.status}>
          <StatusChip status={detail.status} />
          <span>{detail.status_message}</span>
          <span className={styles.meta}>
            {typeLabel(detail.detected_type)}
            {pagesText}
          </span>
          <ReadAgain
            documentId={detail.id}
            fixes={detail.reviewer_fixes}
            offered={canReprocess(me.user.role, detail)}
            reading={beingRead(detail.status)}
            onStarted={(reply) => {
              merge(reply);
              readingAgain.current = true;
              poll.later();
            }}
          />
        </div>
        <div className={styles.place}>
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
          {download && (
            <a className={`btn ${styles.download}`} href={download} download>
              Download original
            </a>
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
          {lowResolution && (
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
            />
          ) : (
            <p className={styles.noPages}>No pages to show yet.</p>
          )}
        </section>
        <div className={styles.panels}>
          <CardPanel {...panel} />
          <RelatedPanel detail={detail} />
          <InvoicePanel {...panel} />
        </div>
        <section className={styles.values} aria-labelledby="values-heading">
          <h2 id="values-heading" className={styles.valuesHeading}>
            Values found
            {detail.values.length > 0 ? (
              <span className={`${styles.count} tabular`}>{detail.values.length}</span>
            ) : null}
          </h2>
          {readingNote && <p className="muted">{readingNote}</p>}
          {detail.values.length === 0 ? (
            !readingNote && <p className="muted">No values were found in this document.</p>
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
                        <div className={styles.pageNote}>
                          Page {value.region.page}
                          {active ? ", highlighted on the page" : ""}
                        </div>
                      </td>
                      <td role="cell" className={styles.labelCell}>
                        <LabelBadge label={value.label} />
                      </td>
                      <td role="cell" className={styles.reasonCell}>
                        {reasonText(value.label_reason)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </section>
      </div>
    </div>
  );
}
