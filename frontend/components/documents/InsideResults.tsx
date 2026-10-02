"use client";

// The documents a search inside documents found (PRD 3 Q3), best first: each
// one's name, type, best passage and its pages, opening the viewer on that
// spot. Every text is React text, so markup in a document or a name shows as
// plain characters. The logic is in insideSearch.ts.

import Link from "next/link";
import { EmptyState } from "@/components/ui/EmptyState";
import { Icon } from "@/components/ui/Icon";
import type { DocumentSearch } from "@/lib/types";
import { resultView } from "./insideSearch";
import styles from "./Documents.module.css";
import libraryStyles from "./Library.module.css";

export interface InsideSearchResultsProps {
  found: DocumentSearch | null; // null while searching
  error: string | null;
  q: string;
  noResults: string;
  datesIgnored: boolean; // the upload dates narrow the list only
  onRetry(): void;
  onClear(): void;
}

export function InsideSearchResults({
  found,
  error,
  q,
  noResults,
  datesIgnored,
  onRetry,
  onClear,
}: InsideSearchResultsProps) {
  const note = datesIgnored && (
    <p className={libraryStyles.placeHint}>The upload dates narrow the list of names only, not this search.</p>
  );
  if (error !== null) {
    return (
      <div className={styles.errorBanner} role="alert">
        <Icon name="alert" className={styles.errorIcon} />
        <div className={styles.errorBody}>
          <p className={styles.errorTitle}>Could not search inside your documents.</p>
          <p>{error}</p>
        </div>
        <button type="button" className="btn" onClick={onRetry}>
          <Icon name="refresh" />
          Try again
        </button>
      </div>
    );
  }
  if (found === null) {
    return (
      <p className="muted" role="status">
        Searching inside your documents…
      </p>
    );
  }
  if (found.documents.length === 0) {
    return (
      <>
        {note}
        <EmptyState
          icon="search-off"
          title={noResults}
          headingLevel={3}
          action={
            <button type="button" className="btn" onClick={onClear}>
              Clear search
            </button>
          }
        />
      </>
    );
  }
  return (
    <>
      {note}
      <p className="visually-hidden" role="status">
        {found.documents.length === 1 ? "1 document found." : `${found.documents.length} documents found.`}
      </p>
      <ol className={styles.results} aria-label={`Documents with “${q}”`}>
        {found.documents.map((document) => {
          const view = resultView(document);
          return (
            <li key={view.id} className={styles.result}>
              <Link href={view.href} className={styles.resultName}>
                {view.name}
              </Link>
              <p className={styles.resultMeta}>
                {view.type} · {view.pages}
              </p>
              <p className={styles.resultSnippet}>{view.snippet}</p>
            </li>
          );
        })}
      </ol>
    </>
  );
}
