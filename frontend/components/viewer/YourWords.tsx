"use client";

// Your words on the page (package H, 24 Oct 2026): a link from an Ask answer or a search
// result carries the asker's key words (?words=, linkWords), and the page lights every line
// holding one, drawn under the cited spot in their own colour (PageViewer's .word). The lines
// of every page come in one request a document (GET /documents/{id}/words, loadDocumentWords),
// kept while the pages change.
// 24 Oct 2026 (Tushar: "which keyword, and from where"): each word is lit in its own
// highlighter colour (wordColour, the server's hits). The lines sweep in like a marker.
// Vrushit UAT 2 (27 Oct 2026, #412): no "Your words in this document" panel under the page;
// only the lit lines stay.

import { getDocumentWords } from "@/lib/api";
import type { DocumentWords } from "@/lib/types";

/** The highlighter colours, cycled after the fourth word (PageViewer.module.css). */
export const WORD_COLOURS = 4;

/** A line's colour from the word it holds (data-word); the first colour when not known. */
export function wordColour(hit: number | undefined): string {
  return String((hit ?? 0) % WORD_COLOURS);
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
