"use client";

// The documents a search inside documents found (PRD 3 Q3), best first: each
// one's name, type, best passage and its pages, opening the viewer on that
// spot. Every text is React text, so markup in a document or a name shows as
// plain characters. The logic is in insideSearch.ts. Above them, "From your
// stories" shows the reply's story_matches (lib/storyMatches.ts), never mixed
// into the results.

import Link from "next/link";
import { StoryMatches } from "@/components/stories/StoryMatches";
import { EmptyState } from "@/components/ui/EmptyState";
import { Icon } from "@/components/ui/Icon";
import { useBackFrom, withFrom } from "@/lib/backLink";
import { STORY_NOTE_SEARCH, storyMatchesFrom, storyStatusText } from "@/lib/storyMatches";
import type { DocumentSearch } from "@/lib/types";
import { LabelChip } from "@/components/files/LabelChip";
import { alsoSearchedText, foundCountText, highlightParts, resultView } from "./insideSearch";
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
  const from = useBackFrom(); // set on /search, not on the documents screen (#436)
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
  const stories = storyMatchesFrom(found.story_matches);
  // Q2, Q5: the person's own words the search used, and every word searched for, marked.
  const also = alsoSearchedText(found);
  const marked = [q, ...(found.understood_words ?? []).map((word) => word.meaning)];
  const storyBox = <StoryMatches matches={stories} note={STORY_NOTE_SEARCH} headingLevel={3} />;
  if (found.documents.length === 0) {
    return (
      <>
        {note}
        {stories.length > 0 && (
          <p className="visually-hidden" role="status">
            {storyStatusText(stories.length)}
          </p>
        )}
        {storyBox}
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
      <p className="muted" role="status">
        {foundCountText(found)}
        {stories.length > 0 ? ` ${storyStatusText(stories.length)}` : null}
      </p>
      {storyBox}
      {also && <p className={libraryStyles.placeHint}>{also}</p>}
      <ol className={styles.results} aria-label={`Documents with “${q}”`}>
        {found.documents.map((document) => {
          const view = resultView(document, q);
          return (
            <li key={view.id} className={styles.result}>
              <Link href={withFrom(view.href, from)} className={styles.resultName}>
                {view.name}
              </Link>
              <p className={`${styles.resultMeta} ${styles.resultFacts}`}>
                {view.version && <span className="mono">{view.version}</span>}
                {view.label && <LabelChip label={view.label} />}
                <span>
                  {view.type} · {view.place}
                </span>
              </p>
              <p className={styles.resultSnippet}>
                {highlightParts(view.snippet, marked).map((part, index) =>
                  part.hit ? <mark key={index}>{part.text}</mark> : part.text,
                )}
              </p>
            </li>
          );
        })}
      </ol>
    </>
  );
}
