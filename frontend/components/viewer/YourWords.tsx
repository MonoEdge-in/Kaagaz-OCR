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
// 24 Oct 2026 (Tushar: "which keyword, and from where"): each word is lit in its own
// highlighter colour (wordColour, the server's hits), and a legend under the page lists each
// word with its colour and where it is: "2 lines on this page", "on pages 3 and 5" with a jump
// to its next page, or plainly "not in this document". The lines sweep in like a marker.

import { useEffect, useRef } from "react";
import { scriptParts } from "@/components/chat/answer";
import { getDocumentWords } from "@/lib/api";
import type { DocumentWords, WordFound } from "@/lib/types";
import styles from "./PageViewer.module.css";

export const SHOW_WORDS = "Show your words";
export const NEXT_WORDS_PAGE = "Next page with your words";
export const WORDS_HEADING = "Your words in this document";
/** The highlighter colours, cycled after the fourth word (PageViewer.module.css). */
export const WORD_COLOURS = 4;

/** A line's colour from the word it holds (data-word); the first colour when not known. */
export function wordColour(hit: number | undefined): string {
  return String((hit ?? 0) % WORD_COLOURS);
}

/** "page 3", "pages 3 and 5", "pages 2, 5, 7 and 9", "pages 2, 5, 7 and 4 more". */
export function pagesText(pages: readonly number[]): string {
  if (pages.length === 1) return `page ${pages[0]}`;
  const shown = pages.length > 4 ? pages.slice(0, 3) : pages.slice(0, -1);
  const last = pages.length > 4 ? `${pages.length - 3} more` : String(pages[pages.length - 1]);
  return `pages ${shown.join(", ")} and ${last}`;
}

/** Where one word is, from this page: "2 lines on this page", "on pages 3 and 5", or plainly
 * "not in this document" (the asker sees which of their words the document holds). */
export function wordWhereText(found: WordFound, here: number): string {
  if (found.lines === 0) return "not in this document";
  if (here > 0) return `${here} ${here === 1 ? "line" : "lines"} on this page`;
  return `on ${pagesText(found.pages)}`;
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
  const here = !!found && found.boxes.some((region) => region.page === pageNo); // a line, or a sheet's cell
  const legend = found?.found ?? [];
  // How many of this page's lines each word lit (the first word a line holds counts it).
  const hereOf = legend.map((_, word) =>
    found ? found.boxes.filter((region, index) => region.page === pageNo && (found.hits?.[index] ?? 0) === word).length : 0,
  );
  const next = found ? nextWordsPage(found.pages, pageNo) : null;
  const toggle = useRef<HTMLButtonElement>(null);
  const moved = useRef(false); // "Next page with your words" was pressed
  useEffect(() => {
    // The page reached has no next: its button is gone, so the focus goes to the toggle.
    if (moved.current && next === null) toggle.current?.focus();
    moved.current = false;
  }, [pageNo, next]);
  if (words.length === 0 || !found || (!here && next === null && legend.length === 0)) return null;
  return (
    <div className={styles.yourWords}>
      {on && legend.length > 0 ? (
        <>
          <p className={styles.wordsHeading}>{WORDS_HEADING}</p>
          <ul className={styles.wordLegend}>
            {legend.map((item, word) => {
              const own = hereOf[word] === 0 ? nextWordsPage(item.pages, pageNo) : null;
              const jump = own === next ? null : own; // never a second button to the same page
              return (
                <li key={item.word} className={styles.legendRow} data-missing={item.lines === 0 ? "" : undefined}>
                  <span className={styles.wordSwatch} data-word={wordColour(word)} aria-hidden="true" />
                  <span className={styles.legendWord}>
                    <Script text={item.word} />
                  </span>
                  <span className={styles.legendWhere}>{wordWhereText(item, hereOf[word] ?? 0)}</span>
                  {jump !== null ? (
                    <button type="button" className={styles.legendJump} onClick={() => onPage(jump)}>
                      {`Page ${jump}`}
                      <span className="visually-hidden">{`, where ${item.word} is`}</span>
                    </button>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </>
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

/** Plain text, its Devanagari runs marked lang="mr". */
function Script({ text }: { text: string }) {
  return (
    <>
      {scriptParts(text).map((part, index) =>
        part.lang ? (
          <span key={index} lang={part.lang}>
            {part.text}
          </span>
        ) : (
          part.text
        ),
      )}
    </>
  );
}
