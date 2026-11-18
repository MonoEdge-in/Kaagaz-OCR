"use client";

// PRD 9 F-17: Review's By document view. The documents with open values or open word flags
// (the file list's Needs attention, REVIEW_DOCUMENTS), newest first, each with its count; one
// opens on its Needs attention tab, carrying the ones after it for Next document.

import Link from "next/link";
import { useEffect, useState } from "react";
import { ScriptText } from "@/app/(app)/documents/[id]/SummaryCard";
import { EmptyState } from "@/components/ui/EmptyState";
import { ApiError, listDocuments } from "@/lib/api";
import type { DocumentList, DocumentSummary } from "@/lib/types";
import { REVIEW_DOCUMENTS, openDocumentHref, toCheckText } from "./review";
import styles from "./Review.module.css";

export function DocumentRows({ documents, total }: { documents: readonly DocumentSummary[]; total: number }) {
  if (documents.length === 0) {
    return (
      <EmptyState icon="check-seal" title="Nothing to check">
        Every document read so far has no values or words waiting.
      </EmptyState>
    );
  }
  const ids = documents.map((document) => document.id);
  return (
    <>
      <ol className={styles.documents}>
        {documents.map((document, at) => (
          <li key={document.id}>
            <Link href={openDocumentHref(ids, at)}>
              <ScriptText text={document.display_name} />
            </Link>
            <span className="muted">{toCheckText(document)}</span>
          </li>
        ))}
      </ol>
      {total > documents.length ? (
        <p className="muted">
          {`The newest ${documents.length} of ${total}. `}
          <Link href="/documents?attention=1">See them all in Documents</Link>
        </p>
      ) : null}
    </>
  );
}

export function ReviewDocuments() {
  const [attempt, setAttempt] = useState(0);
  const [shown, setShown] = useState<{ attempt: number; list: DocumentList } | null>(null);
  const [failed, setFailed] = useState<{ attempt: number; message: string } | null>(null);

  useEffect(() => {
    let cancelled = false;
    listDocuments(REVIEW_DOCUMENTS)
      .then((list) => {
        if (!cancelled) setShown({ attempt, list });
      })
      .catch((caught: unknown) => {
        if (!cancelled) {
          const message = caught instanceof ApiError ? caught.message : "Check your internet connection, then try again.";
          setFailed({ attempt, message });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  if (failed?.attempt === attempt) {
    return (
      <div role="alert">
        <p className="error-text">{`Could not load the documents to check. ${failed.message}`}</p>
        <button type="button" className="btn" onClick={() => setAttempt((n) => n + 1)}>
          Try again
        </button>
      </div>
    );
  }
  if (shown?.attempt !== attempt) {
    return (
      <p className="muted" role="status">
        Loading the documents to check…
      </p>
    );
  }
  return <DocumentRows documents={shown.list.items} total={shown.list.total} />;
}
