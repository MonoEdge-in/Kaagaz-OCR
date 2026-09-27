"use client";

// The document screen: header and status, the page viewer on the left and the
// values table on the right. Clicking a value jumps to its page and highlights
// its spot. Every value is shown as plain text; it came from the document.

import Link from "next/link";
import { useEffect, useState } from "react";
import { PageViewer } from "@/components/viewer/PageViewer";
import { LabelBadge } from "@/components/viewer/LabelBadge";
import { fieldText, reasonText } from "@/components/viewer/labels";
import { initialSelection, selectValue } from "@/components/viewer/selection";
import type { Selection } from "@/components/viewer/selection";
import { ApiError, getDocument } from "@/lib/api";
import { statusChip, typeLabel } from "@/lib/format";
import type { DocumentDetail, ExtractedValue } from "@/lib/types";
import styles from "./DocumentView.module.css";

export interface DocumentViewProps {
  documentId: string;
  pageParam: string | null;
  regionParam: string | null;
}

type Loaded = { detail: DocumentDetail } | { error: string };

export function DocumentView({ documentId, pageParam, regionParam }: DocumentViewProps) {
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  // A choice made on this screen, remembered with the link it was made under,
  // so following a new link (new ?page / ?region) shows that link's spot.
  const linkKey = `${pageParam ?? ""}|${regionParam ?? ""}`;
  const [chosen, setChosen] = useState<{ linkKey: string; selection: Selection } | null>(null);
  const [announcement, setAnnouncement] = useState("");

  useEffect(() => {
    let cancelled = false;
    getDocument(documentId)
      .then((detail) => {
        if (!cancelled) setLoaded({ detail });
      })
      .catch((error: unknown) => {
        const message =
          error instanceof ApiError ? error.message : "Could not load this document.";
        if (!cancelled) setLoaded({ error: message });
      });
    return () => {
      cancelled = true;
    };
  }, [documentId]);

  if (!loaded) return <p className="muted" aria-live="polite">Loading the document…</p>;
  if ("error" in loaded) {
    return (
      <div className="stack">
        <p className="error-text" role="alert">
          {loaded.error}
        </p>
        <p>
          <Link href="/documents">Back to documents</Link>
        </p>
      </div>
    );
  }

  const detail = loaded.detail;
  const selection =
    chosen && chosen.linkKey === linkKey
      ? chosen.selection
      : initialSelection(detail, pageParam, regionParam);
  const page = selection ? detail.pages.find((p) => p.page_no === selection.pageNo) : undefined;
  const chip = statusChip(detail.status);
  const pageNumbers = detail.pages.map((p) => p.page_no);

  const choose = (next: Selection) => setChosen({ linkKey, selection: next });
  const showValue = (value: ExtractedValue) => {
    choose(selectValue(value));
    setAnnouncement(`Showing ${fieldText(value.field)} on page ${value.region.page}.`);
  };
  const changePage = (pageNo: number) => {
    const region = selection && selection.region?.page === pageNo ? selection.region : null;
    choose({ pageNo, region, valueId: region ? (selection?.valueId ?? null) : null });
  };

  return (
    <div className="stack">
      <header className="stack">
        <p>
          <Link href="/documents">Back to documents</Link>
        </p>
        <h1>{detail.display_name}</h1>
        <div className="row">
          <span className={chip.className}>{chip.text}</span>
          <span>{detail.status_message}</span>
          <span className="muted">
            {typeLabel(detail.detected_type)}
            {detail.page_count !== null ? `, ${detail.page_count} page${detail.page_count === 1 ? "" : "s"}` : ""}
          </span>
        </div>
      </header>
      <p className="visually-hidden" aria-live="polite">
        {announcement}
      </p>
      <div className={styles.layout}>
        <section className={`card ${styles.viewer}`} aria-label="Page">
          {page ? (
            <PageViewer
              document={detail}
              page={page}
              highlights={selection?.region ? [selection.region] : []}
              pageNumbers={pageNumbers}
              onPageChange={changePage}
            />
          ) : (
            <p className="muted">No pages to show yet.</p>
          )}
        </section>
        <section className={`card ${styles.values}`} aria-labelledby="values-heading">
          <h2 id="values-heading">Values found</h2>
          {detail.values.length === 0 ? (
            <p className="muted">No values were found in this document.</p>
          ) : (
            <div className={styles.tableWrap}>
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">Field</th>
                    <th scope="col">Value</th>
                    <th scope="col">Label</th>
                    <th scope="col">Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {detail.values.map((value) => {
                    const active = selection?.valueId === value.id;
                    return (
                      <tr key={value.id} className={active ? styles.activeRow : undefined}>
                        <td>{fieldText(value.field)}</td>
                        <td>
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
                          <div className="muted">
                            Page {value.region.page}
                            {active ? ", highlighted on the page" : ""}
                          </div>
                        </td>
                        <td>
                          <LabelBadge label={value.label} />
                        </td>
                        <td>{reasonText(value.label_reason)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
