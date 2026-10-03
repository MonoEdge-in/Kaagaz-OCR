// "Your story about this document": the screen's small decisions, unit tested in
// node (tests/viewer-story.test.ts). The text check mirrors check_story in
// kaagaz/db/document_stories.py at a basic level, for instant feedback only: the
// server checks again and wins. A story is the user's own text: shown as text
// only, never logged.

import type { UploadState } from "@/components/documents/logic";
import { ApiError } from "@/lib/api";

// Characters (code points), after tidying. Only the tidied text is sent, so the
// server's 4,000 before tidying never applies to it.
export const STORY_MAX = 1000;
export const STORY_ANCHOR = "your-story";
export const STORY_HINT =
  "Write how you remember this document, e.g. 'Papa ne 2018 me jo Daregaon wali jamin ka 7/12 diya tha, bank loan ke liye'. Only you can see it.";

/** The viewer, opened at the story section. */
export const storyHref = (documentId: string) => `/documents/${documentId}#${STORY_ANCHOR}`;

/** The document a finished upload row offers a story for, or null (not finished, failed, in Trash). */
export function storyTarget(state: UploadState): string | null {
  if (state.kind === "done" || state.kind === "restored") return state.documentId;
  return state.kind === "duplicate" && !state.inTrash ? state.documentId : null;
}

/** Characters as the server counts them: code points, so an emoji is 1, not 2. */
export const codePoints = (value: string) => [...value].length;

// Python's str.isspace(), close enough: \s without U+FEFF (a format character), with \x1c-\x1f and \x85.
const SPACE = /[\t-\r\x1c-\x20\x85\xa0\u1680\u2000-\u200A\u2028\u2029\u202F\u205F\u3000]/u;
const SPACES = new RegExp(`${SPACE.source}+`, "u");
const REMOVED = /^[\p{Cc}\p{Cf}\p{Cs}\p{Co}\p{Cn}]$/u; // controls, format, lone surrogates, private use, unassigned
const INVISIBLE = /^[\u034F\u180B-\u180F\uFE00-\uFE0F\u{e0100}-\u{e01ef}]$/u; // joiner and variation selectors
const MARK = /^\p{M}$/u;
const JOINERS = new Set(["\u200C", "\u200D"]);
const VIRAMA = "\u094D";
const MAX_MARKS = 4;
const BLANK_LETTERS = /[\u115F\u1160\u3164\uFFA0]/gu; // Hangul fillers: letters that show nothing

/** The server's _clean: drop invisible and control characters (a joiner stays after a virama) and marks past 4. */
function clean(value: string): string {
  const kept: string[] = [];
  let marks = 0;
  for (const ch of value) {
    if (JOINERS.has(ch)) {
      if (kept.at(-1) === VIRAMA) kept.push(ch);
      continue;
    }
    if (INVISIBLE.test(ch) || (!SPACE.test(ch) && REMOVED.test(ch))) continue;
    if (MARK.test(ch)) {
      marks += 1;
      if (marks > MAX_MARKS) continue;
    } else {
      marks = 0;
    }
    kept.push(ch);
  }
  return kept.join("");
}

/** The story as the server will store it: cleaned, NFC, white space runs made one space, trimmed. */
export const tidyStory = (value: string) =>
  clean(value).normalize("NFC").split(SPACES).filter(Boolean).join(" ");

/** Why the server would refuse this text, in its words, or null when it looks fine. */
export function storyProblem(value: string): string | null {
  const story = tidyStory(value);
  if (!story) return "Write the story first.";
  if (codePoints(story) > STORY_MAX) return `A story can be at most ${STORY_MAX} characters.`;
  if (!/[\p{L}\p{N}]/u.test(story.replace(BLANK_LETTERS, ""))) {
    return "The story needs at least one letter or digit.";
  }
  return null;
}

/** The counter under the text box, of what will be stored. */
export function countText(value: string): string {
  const used = codePoints(tidyStory(value));
  const count = (n: number) => n.toLocaleString("en-IN");
  const text = `${count(used)} of ${count(STORY_MAX)} characters`;
  return used > STORY_MAX ? `${text}, ${count(used - STORY_MAX)} too many` : text;
}

export type StoryBody = { story: string; user_id: string };

/** PUT /documents/{id}/my-story: the tidied story and who this tab signed in (409 session_changed if someone else has since). */
export const storyBody = (value: string, userId: string): StoryBody => ({ story: tidyStory(value), user_id: userId });

export type StoryAction = "load" | "save" | "delete";

/**
 * What the screen shows for a failed request.
 * field: by the text box (400: the text); gone: the story or the document is not
 * there any more (404); alert: everything else, with the server's own words.
 */
export type StoryFailure = { kind: "field" | "gone" | "alert"; message: string };

const NOT = { load: "Your story could not be loaded.", save: "Not saved.", delete: "Not deleted." } as const;

export function storyFailure(caught: unknown, action: StoryAction): StoryFailure {
  const not = NOT[action];
  if (!(caught instanceof ApiError) || caught.status === 0) {
    return { kind: "alert", message: `${not} Check your internet connection, then try again.` };
  }
  const { status, code, message } = caught;
  if (status === 400 && action === "save") return { kind: "field", message };
  if (status === 404) {
    if (action === "delete") return { kind: "gone", message: "Your story isn't there any more." };
    return { kind: "gone", message: `${not} This document isn't there any more, or it is in Trash. Reload the page.` };
  }
  if (status === 409 && code === "session_changed") {
    return {
      kind: "alert",
      message: `${not} A different account has signed in on this browser since this page opened. Reload the page to see who is signed in now.`,
    };
  }
  if (status === 401) return { kind: "alert", message: `${not} Please sign in again.` };
  if (status >= 500 && status !== 503) return { kind: "alert", message: `${not} Something went wrong on our side. Try again.` };
  // 400 on a read or delete, 403 (CSRF, permission), other 409, 429, 503: the server's own words.
  return { kind: "alert", message: `${not} ${message}` };
}
