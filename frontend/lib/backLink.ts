// The way back from a document to the screen that opened it (#436, Tushar, 28 Oct 2026):
// "If I come to any document from Ask, I must have a back to chat." A screen that opens
// documents wraps itself in <BackFrom value={its own address}>, and its links carry
// ?from=<that address> (withFrom). The document page reads it back (backFrom): only a
// path on this site, to one of the places below, counts, and the words come from this
// list, never from the address. Anything else is ignored and the back link is the usual
// one. The address holds ids and the search's words only, never a question.

import { createContext, useContext } from "react";

export const FROM_PARAM = "from";

/** A screen's own address, for the document links inside it; null adds nothing. */
export const BackFrom = createContext<string | null>(null);
export const useBackFrom = () => useContext(BackFrom);

// The places a document can be opened from, by path, and how the back link names them.
const PLACES: readonly (readonly [RegExp, string])[] = [
  [/^\/chat$/, "chat"],
  [/^\/review$/, "Review"],
  [/^\/search$/, "search results"],
  [/^\/dashboard$/, "Home"],
  [/^\/words$/, "My words"],
  [/^\/documents\/[\w-]+$/, "previous document"],
];

// Parsed against this made-up origin, so a path that would leave the site shows up as one.
const HERE = "https://kaagaz.invalid";

/**
 * Where ?from= leads back to and its name ("chat", "Review"), or null: not a path on this
 * site (`//evil.com`, `https://…`, `/\evil`, `javascript:`, a space, tab or newline)
 * or not one of the places.
 */
export function backFrom(from: string | null): { href: string; name: string } | null {
  if (from === null || !from.startsWith("/") || from.startsWith("//")) return null;
  if (/[\\\s]/.test(from)) return null; // a backslash, or a tab or newline the URL parser drops
  const url = new URL(from, HERE);
  if (url.origin !== HERE) return null;
  const place = PLACES.find(([path]) => path.test(url.pathname));
  return place ? { href: `${url.pathname}${url.search}${url.hash}`, name: place[1] } : null;
}

/** `href` with ?from=<from> added before any #part; unchanged when `from` is not a place backFrom knows. */
export function withFrom(href: string, from: string | null): string {
  if (from === null || backFrom(from) === null) return href;
  const cut = href.indexOf("#");
  const path = cut === -1 ? href : href.slice(0, cut);
  const hash = cut === -1 ? "" : href.slice(cut);
  return `${path}${path.includes("?") ? "&" : "?"}${FROM_PARAM}=${encodeURIComponent(from)}${hash}`;
}
