"use client";

// Your words on the page (package H, 24 Oct 2026): a link from an Ask answer or a search
// result carries the asker's key words (?words=, linkWords), and the page lights every line
// holding one, drawn under the cited spot in their own colour (PageViewer's .word). The lines
// of every page come in one request a document (GET /documents/{id}/words, loadDocumentWords),
// kept while the pages change, so "Next page with your words" keeps its place and the focus.
// Under the page: "Highlighted: your words (krishna, maval) on this page" and "Show your
// words" (pressed while they show), only on a page holding them; "Next page with your words"
// while another page holds them. Pressed, it moves to that page; when that page has no next,
// the focus goes to its "Show your words". A page with none and no other shows nothing. The
// words are plain text, Devanagari marked lang="mr".

import { useEffect, useRef } from "react";
import { scriptParts } from "@/components/chat/answer";
import { getDocumentWords } from "@/lib/api";
import type { DocumentWords } from "@/lib/types";
import styles from "./PageViewer.module.css";

export const SHOW_WORDS = "Show your words";
export const NEXT_WORDS_PAGE = "Next page with your words";

export function wordsNoteText(words: readonly string[]): string {
  return `Highlighted: your words (${words.join(", ")}) on this page`;
}

/** The first page after `current` holding the words, else the first before it; null when no
 * other page does. `pages` ascending, as the server sends them. O(p). */
export function nextWordsPage(pages: readonly number[], current: number): number | null {
  return pages.find((page) => page > current) ?? pages.find((page) => page !== current) ?? null;
}

// The last request only (one document, words and reload): asked again for the same, it is
// that one, never a new one. Never more than one kept; a failed one is forgotten.
let last: { key: string; loading: Promise<DocumentWords> } | null = null;

export function loadDocumentWords(documentId: string, words: readonly string[], version: number): Promise<DocumentWords> {
  const key = `${documentId}:${version}:${words.join(" ")}`;
  if (last?.key === key) return last.loading;
  const asked = { key, loading: getDocumentWords(documentId, words) };
  last = asked;
  asked.loading.catch(() => {
    if (last === asked) last = null;
  });
  return asked.loading;
}

export function YourWords({
  words,
  found,
  pageNo,
  on,
  onToggle,
  onPage,
}: {
  words: readonly string[];
  /** The document's lines holding the words; null until they come (or when they could not be loaded). */
  found: DocumentWords | null;
  pageNo: number;
  on: boolean;
  onToggle: () => void;
  onPage: (pageNo: number) => void;
}) {
  const here = !!found && found.boxes.some((region) => region.kind === "box" && region.page === pageNo);
  const next = found ? nextWordsPage(found.pages, pageNo) : null;
  const toggle = useRef<HTMLButtonElement>(null);
  const moved = useRef(false); // "Next page with your words" was pressed
  useEffect(() => {
    // The page reached has no next: its button is gone, so the focus goes to the toggle.
    if (moved.current && next === null) toggle.current?.focus();
    moved.current = false;
  }, [pageNo, next]);
  if (words.length === 0 || !found || (!here && next === null)) return null;
  return (
    <div className={styles.yourWords}>
      {here && on ? (
        <p className={`muted ${styles.note} ${styles.wordsNote}`}>
          {scriptParts(wordsNoteText(words)).map((part, index) =>
            part.lang ? (
              <span key={index} lang={part.lang}>
                {part.text}
              </span>
            ) : (
              part.text
            ),
          )}
        </p>
      ) : null}
      <div className={styles.wordChips}>
        {here ? (
          <button ref={toggle} type="button" className={styles.wordChip} aria-pressed={on} onClick={onToggle}>
            <span className={styles.wordSwatch} aria-hidden="true" />
            {SHOW_WORDS}
          </button>
        ) : null}
        {next !== null ? (
          <button
            type="button"
            className={styles.wordChip}
            onClick={() => {
              moved.current = true;
              onPage(next);
            }}
          >
            {NEXT_WORDS_PAGE}
          </button>
        ) : null}
      </div>
    </div>
  );
}
