// My words: the screens' small decisions, unit tested in node (tests/my-words.test.ts).
// The text check mirrors kaagaz/answering/words_text.py for instant feedback only: the
// server checks again and wins (a word chat reads its own way is refused only there).
// Words are the user's text: shown as text only, never logged.

import { ApiError } from "@/lib/api";
import type { Answer, MyWord, WordSaved } from "@/lib/types";

export const WORD_MAX = 40;
export const MEANING_MAX = 80;
export const MAX_WORDS = 500; // and 3 meanings a word: the server says so (409 too_many_meanings)
const MAX_NOTES = 10;
const MAX_LINKED = 20;

/** NFC, every run of white space made one space, the ends trimmed (as the server's tidy). */
export const tidy = (value: string) => value.normalize("NFC").split(/\s+/u).filter(Boolean).join(" ");
/** The form duplicates are found by: tidied and case folded. */
export const fold = (value: string) => tidy(value).toLowerCase().normalize("NFC");

/** Why the server would refuse this text, in its words, or null when it looks fine. */
export function textProblem(value: string, what: "word" | "meaning"): string | null {
  const text = tidy(value);
  const limit = what === "word" ? WORD_MAX : MEANING_MAX;
  if (!text) return `Type the ${what}.`;
  if ([...text].length > limit) return `The ${what} can be at most ${limit} characters.`;
  const allowed = what === "word" ? /^[\p{L}\p{M}\p{Nd} -]+$/u : /^[\p{L}\p{M}\p{Nd} ,-]+$/u;
  if (!allowed.test(text)) {
    return `Use only letters, digits, spaces${what === "meaning" ? ", commas" : ""} and hyphens in the ${what}.`;
  }
  if (!/\p{L}/u.test(text)) return `The ${what} needs at least one letter.`;
  if (what === "word" && text.split(" ").length > 3) return "A word can have at most 3 parts.";
  return null;
}

export type WordDraft = { word: string; meaning: string };

/** What the duplicate question can add to a POST or PATCH body. */
type Answered = { confirm_another?: true; replace_id?: string };
export type NewWordBody = WordDraft & Answered & { user_id: string; document_ids?: string[] };
export type ChangeBody = Partial<WordDraft> & Answered & { user_id: string };

/** POST /me/words; documentIds links it to the files just uploaded. */
export function addBody(draft: WordDraft, userId: string, documentIds: string[] = []): NewWordBody {
  return {
    word: tidy(draft.word),
    meaning: tidy(draft.meaning),
    user_id: userId,
    // The server takes at most 20; the link is a note only, the word works for every question.
    ...(documentIds.length > 0 ? { document_ids: documentIds.slice(0, MAX_LINKED) } : {}),
  };
}

export type Choice = { kind: "add_too" } | { kind: "replace"; id: string } | { kind: "cancel" };

/** The duplicate dialog's choice as the POST or PATCH to send again; null for Cancel. */
export function retryBody<T extends Answered>(body: T, choice: Choice): T | null {
  if (choice.kind === "cancel") return null;
  return choice.kind === "add_too" ? { ...body, confirm_another: true } : { ...body, replace_id: choice.id };
}

/** PATCH /me/words/{id}: only what changed (else the server compares it to itself); null when nothing did. */
export function changeBody(item: WordDraft, draft: WordDraft, userId: string): ChangeBody | null {
  const word = tidy(draft.word) !== item.word ? tidy(draft.word) : undefined;
  const meaning = tidy(draft.meaning) !== item.meaning ? tidy(draft.meaning) : undefined;
  if (word === undefined && meaning === undefined) return null;
  return { ...(word === undefined ? {} : { word }), ...(meaning === undefined ? {} : { meaning }), user_id: userId };
}

/** Both fields look fine to the instant check. */
export const draftOk = (draft: WordDraft) => !textProblem(draft.word, "word") && !textProblem(draft.meaning, "meaning");

export type Existing = { id: string; meaning: string };

export type Saved = { kind: "saved"; word: MyWord; message: string };
export type SaveState = Saved | { kind: "ask"; existing: Existing[] } | { kind: "refused"; message: string };

const BUILT_IN = " Chat already knows this word its own way, so it may read it both ways.";

/** A saved reply (POST: 201 created, 200 already or replaced; PATCH: no outcome) as what the screen says. */
export function savedState(reply: Omit<WordSaved, "outcome"> & Partial<WordSaved>): Saved {
  const { word, meaning } = reply.word;
  const said = {
    created: `Saved '${word}' as '${meaning}'.`,
    already: `'${word}' as '${meaning}' is already in your words.`,
    replaced: `Saved '${word}' as '${meaning}' in place of the old meaning.`,
  }[reply.outcome ?? "created"];
  return { kind: "saved", word: reply.word, message: reply.warning === "built_in" ? said + BUILT_IN : said };
}

function existingOf(details: unknown): Existing[] | null {
  const existing = (details as { existing?: unknown } | null)?.existing;
  if (!Array.isArray(existing) || existing.length === 0) return null;
  const ok = existing.every((row) => typeof row?.id === "string" && typeof row?.meaning === "string");
  return ok ? (existing as Existing[]).map(({ id, meaning }) => ({ id, meaning })) : null;
}

/** A failed save as what the screen does next: ask (409 word_has_meaning), or say why. */
export function failedState(caught: unknown): SaveState {
  if (!(caught instanceof ApiError) || caught.status === 0) {
    return { kind: "refused", message: "Not saved. Check your internet connection, then try again." };
  }
  if (caught.status === 409 && caught.code === "word_has_meaning") {
    const existing = existingOf(caught.details);
    if (existing) return { kind: "ask", existing };
  }
  if (caught.status === 404) {
    return { kind: "refused", message: "That word or file isn't there any more. Reload the page." };
  }
  // 400 (the text), 409 (limits, session_changed), 429: the server's own words.
  return { kind: "refused", message: caught.message };
}

/** The duplicate dialog's question (the council's wording). */
export function duplicateQuestion(word: string, meaning: string, existing: Existing[]): string {
  const had = existing.map((row) => `'${row.meaning}'`).join(" and ");
  return `You already added '${word}' as ${had}. Add '${meaning}' as another meaning too? Two meanings for one word can confuse chat.`;
}

export type WordGroup = { key: string; word: string; meanings: MyWord[] };

/** The list for the screen: grouped by word (folded), in the server's order, filtered in the browser. */
export function wordGroups(items: MyWord[], filter: string): WordGroup[] {
  const q = fold(filter);
  const groups = new Map<string, WordGroup>();
  for (const item of items) {
    const key = fold(item.word);
    const group = groups.get(key) ?? { key, word: item.word, meanings: [] };
    group.meanings.push(item);
    groups.set(key, group);
  }
  return [...groups.values()].filter(
    (group) => !q || group.key.includes(q) || group.meanings.some((m) => fold(m.meaning).includes(q)),
  );
}

/** A note on the limits, or null well before them (the server still decides). */
export function limitNote(count: number): string | null {
  if (count >= MAX_WORDS) return `You have ${MAX_WORDS} words, the most you can keep. Delete some to add more.`;
  return count >= MAX_WORDS - 20 ? `You have ${count} of ${MAX_WORDS} words.` : null;
}

export const SOURCE_TEXT = { manual: "Added here", upload: "Added while uploading", viewer: "Added in the viewer" } as const;

/**
 * The chat note for each of my words an answer used: "Understood 'malak' as
 * 'owner' (your word)". Nothing when the answer has no understood_words (before
 * A3) or it is not a list of {word, meaning} strings. At most 10.
 */
export function understoodNotes(answer: Pick<Answer, "understood_words">): string[] {
  const words: unknown = answer.understood_words;
  if (!Array.isArray(words)) return [];
  return words
    .filter((w) => typeof w?.word === "string" && typeof w?.meaning === "string" && w.word && w.meaning)
    .slice(0, MAX_NOTES)
    .map((w: { word: string; meaning: string; origin?: string }) =>
      `Understood '${w.word}' as '${w.meaning}' (${w.origin === "organization" ? "organization word" : "your word"})`,
    );
}

// sent: the last request, to send again after the duplicate question.
export type UploadPair = WordDraft & { key: string; state: { kind: "draft" } | { kind: "saving" } | SaveState; sent?: NewWordBody };

/** The documents of uploads that finished (new, already there, or restored) and were not linked yet. */
export function uploadedIds(entries: UploadEntry[], linked: ReadonlySet<string>): string[] {
  const ids = entries.flatMap(({ state }) =>
    state.kind === "done" || state.kind === "duplicate" || state.kind === "restored" ? [state.documentId] : [],
  );
  return [...new Set(ids)].filter((id) => !linked.has(id));
}

/** The pairs the next finished upload saves: drafts with text that looks fine. */
export const pairsToSave = (pairs: UploadPair[]) =>
  pairs.filter((pair) => pair.state.kind === "draft" && draftOk(pair));

/** One line for the live region once the upload's words have been sent. */
export function pairsSummary(states: SaveState[]): string {
  const count = (kind: SaveState["kind"]) => states.filter((s) => s.kind === kind).length;
  const parts = [[count("saved"), "saved to My words"], [count("ask"), "already have a meaning: choose what to do"],
    [count("refused"), "not saved"]] as const;
  const said = parts.filter(([n]) => n > 0).map(([n, text]) => `${n} ${text}`);
  return said.length > 0 ? `Words in these files: ${said.join(", ")}.` : "";
}

// --- UI audit, 16 Oct 2026 (W1, W2) ------------------------------------------------

/** W1: Add is ready once both boxes have something in them; the full check runs on Add. */
export const draftFilled = (draft: WordDraft) => tidy(draft.word) !== "" && tidy(draft.meaning) !== "";

/** W2: the search box shows from this many words; fewer fit on one screen. */
export const SEARCH_FROM = 10;

/** One CSV cell: quoted when it holds a comma or a quote; a leading = + - @ made plain text for spreadsheets. */
function csvCell(text: string): string {
  const safe = /^[=+\-@]/.test(text) ? `'${text}` : text;
  return /[",\n]/.test(safe) ? `"${safe.replaceAll('"', '""')}"` : safe;
}

/** W2: my words as a CSV file's text (word, meaning), in the list's order. */
export function wordsCsv(items: readonly Pick<MyWord, "word" | "meaning">[]): string {
  return ["word,meaning", ...items.map((item) => `${csvCell(item.word)},${csvCell(item.meaning)}`)].join("\n") + "\n";
}
