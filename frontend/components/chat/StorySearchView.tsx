// "Search my stories" results (council 3 Oct 2026, "story search mode"): its own
// labelled section in the thread, never in the chat log and never saved. It is
// not an answer, so it has no answer card, no sources, no feedback and none
// of an answer's labels (tests/chat-mode.test.ts reads this file). An error
// is an alert with Try again, never shown as "no match". The screen's one
// status line says how it went (ChatScreen.tsx); this section adds no live
// region. Every text is React text.

import { useId } from "react";
import { StoryMatches } from "@/components/stories/StoryMatches";
import { Icon } from "@/components/ui/Icon";
import { MODE_TEXTS } from "@/lib/chatMode";
import {
  scanNote,
  SEARCH_BANDS,
  SEARCH_CARDS_HEADING,
  SEARCH_EMPTY,
  SEARCH_ERROR,
  SEARCH_HEADING,
  SEARCH_NO_WORDS,
  SEARCH_NOTE,
  SEARCH_PENDING,
} from "@/lib/storyMatches";
import type { StorySearch } from "@/lib/storyMatches";
import styles from "./Chat.module.css";

export type StorySearchState =
  | { kind: "idle" }
  | { kind: "loading"; question: string }
  | { kind: "done"; question: string; found: StorySearch }
  | { kind: "error"; question: string };

function Results({ found }: { found: StorySearch }) {
  if (found.noContentWords) return <p className={styles.plain}>{SEARCH_NO_WORDS}</p>;
  if (found.matches.length === 0) return <p className={styles.plain}>{SEARCH_EMPTY}</p>;
  return (
    <StoryMatches matches={found.matches} headingLevel={2} heading={SEARCH_CARDS_HEADING} bands={SEARCH_BANDS} />
  );
}

export function StorySearchView({ state, onRetry }: { state: StorySearchState; onRetry: () => void }) {
  const headingId = useId();
  const note = state.kind === "done" ? scanNote(state.found) : null;
  return (
    <section
      className={styles.storySearch}
      aria-labelledby={headingId}
      aria-busy={state.kind === "loading" || undefined}
    >
      <h1 id={headingId} className={styles.storySearchHeading}>
        {SEARCH_HEADING}
      </h1>
      <p className={styles.storySearchNote}>{state.kind === "idle" ? MODE_TEXTS.stories.help : SEARCH_NOTE}</p>
      {state.kind !== "idle" ? (
        <p className={styles.plain}>You searched your stories for: {state.question}</p>
      ) : null}
      {state.kind === "loading" ? (
        <p className={`${styles.threadStatus} delayed`}>
          <span className="spinner" aria-hidden="true" />
          {SEARCH_PENDING}
        </p>
      ) : null}
      {state.kind === "error" ? (
        <div className={styles.threadError}>
          <p className="error-text" role="alert">
            {SEARCH_ERROR}
          </p>
          <button type="button" className="btn" onClick={onRetry}>
            <Icon name="refresh" size={16} />
            Try again
          </button>
        </div>
      ) : null}
      {state.kind === "done" ? <Results found={state.found} /> : null}
      {note !== null ? <p className={styles.storySearchNote}>{note}</p> : null}
    </section>
  );
}
