"use client";

// The document screen: header and status, the page viewer and the values
// table (side by side from 1024 px, stacked below). Clicking a value jumps to
// its page and highlights its spot. Every value is shown as plain text; it came
// from the document.

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { StatusChip } from "@/components/documents/StatusChip";
import { Icon } from "@/components/ui/Icon";
import { PageViewer } from "@/components/viewer/PageViewer";
import { LabelBadge } from "@/components/viewer/LabelBadge";
import { fieldText, reasonText } from "@/components/viewer/labels";
import { initialSelection, selectValue } from "@/components/viewer/selection";
import type { Selection } from "@/components/viewer/selection";
import { ApiError, getDocument } from "@/lib/api";
import { typeLabel } from "@/lib/format";
import type { DocumentDetail, ExtractedValue } from "@/lib/types";
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
  const [attempt, setAttempt] = useState(0);
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  // A choice made on this screen, remembered with the link it was made under,
  // so following a new link (new ?page / ?region) shows that link's spot.
  const linkKey = `${pageParam ?? ""}|${regionParam ?? ""}`;
  const [chosen, setChosen] = useState<{ linkKey: string; selection: Selection } | null>(null);
  const [announcement, setAnnouncement] = useState("");
  const viewerRef = useRef<HTMLElement>(null);

  useEffect(() => {
    let cancelled = false;
    getDocument(documentId)
      .then((detail) => {
        if (!cancelled) setLoaded({ attempt, detail });
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
    };
  }, [documentId, attempt]);

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
  const changePage = (pageNo: number) => {
    const region = selection && selection.region?.page === pageNo ? selection.region : null;
    choose({ pageNo, region, valueId: region ? (selection?.valueId ?? null) : null });
  };

  const pagesText =
    detail.page_count !== null
      ? ` · ${detail.page_count} ${detail.page_count === 1 ? "page" : "pages"}`
      : "";

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
        </div>
      </header>
      <p className="visually-hidden" aria-live="polite">
        {announcement}
      </p>
      <div className={styles.layout}>
        <section ref={viewerRef} className={styles.viewer} aria-label="Page">
          {page ? (
            <PageViewer
              document={detail}
              page={page}
              highlights={selection?.region ? [selection.region] : []}
              pageNumbers={pageNumbers}
              onPageChange={changePage}
            />
          ) : (
            <p className={styles.noPages}>No pages to show yet.</p>
          )}
        </section>
        <section className={styles.values} aria-labelledby="values-heading">
          <h2 id="values-heading" className={styles.valuesHeading}>
            Values found
            {detail.values.length > 0 ? (
              <span className={`${styles.count} tabular`}>{detail.values.length}</span>
            ) : null}
          </h2>
          {detail.values.length === 0 ? (
            <p className="muted">No values were found in this document.</p>
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
