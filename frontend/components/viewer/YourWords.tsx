"use client";

// Your words on the page (package H, 24 Oct 2026): a link from an Ask answer or a search
// result carries the asker's key words (?words=, linkWords), and the page lights every line
// holding one (GET /documents/{id}/pages/{n}/words), drawn under the cited spot in their own
// colour (PageViewer's .word). Under the page: "Highlighted: your words (krishna, maval) on this
// page" and "Show your words" (pressed while they show), only on a page holding them; "Next
// page with your words" while another page holds them. A page with none and no other shows
// nothing. The words are plain text, Devanagari marked lang="mr".

import { scriptParts } from "@/components/chat/answer";
import type { PageWords } from "@/lib/types";
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

export function YourWords({
  words,
  found,
  pageNo,
  on,
  onToggle,
  onPage,
}: {
  words: readonly string[];
  /** This page's answer; null until it comes (or when it could not be loaded). */
  found: PageWords | null;
  pageNo: number;
  on: boolean;
  onToggle: () => void;
  onPage: (pageNo: number) => void;
}) {
  if (words.length === 0 || !found) return null;
  const here = found.page_no === pageNo && found.boxes.some((region) => region.kind === "box");
  const next = nextWordsPage(found.pages, pageNo);
  if (!here && next === null) return null;
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
          <button type="button" className={styles.wordChip} aria-pressed={on} onClick={onToggle}>
            <span className={styles.wordSwatch} aria-hidden="true" />
            {SHOW_WORDS}
          </button>
        ) : null}
        {next !== null ? (
          <button type="button" className={styles.wordChip} onClick={() => onPage(next)}>
            {NEXT_WORDS_PAGE}
          </button>
        ) : null}
      </div>
    </div>
  );
}
