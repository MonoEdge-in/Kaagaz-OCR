// Unsent work, kept in this tab only (issue 468): a session that ends sends the
// tab to sign in, a full page load, and what was typed used to be lost. A draft
// lives in sessionStorage (this tab, gone when it closes), under the user id and
// the screen, and only that user ever gets it back: a draft of anyone else is
// dropped unread. Passwords, codes, tokens and files are never kept: any field
// whose name says so is left out, and only plain values are stored.
//
// Screens save on change with saveDraftSoon (it waits a moment, so typing is not
// a write per key), read back on mount with readDraft, and clearDraft once the
// work is sent. flushDrafts writes the waiting ones now, just before a session
// end leaves the page. clearDrafts runs on signing out and when someone else
// signs in. Storage the browser refuses means no draft, never an error.

export type DraftValue = string | number | boolean | null | DraftValue[] | { [field: string]: DraftValue };
export type Draft = { [field: string]: DraftValue };

/** The muted line under a form that got a draft back (Ask, Add a user), until it is edited or sent. */
export const DRAFT_KEPT = "We kept what you were typing. It hasn't been sent.";

const PREFIX = "kaagaz-draft:";
const WAIT_MS = 500;
const SECRET_WORDS = new Set(["password", "passcode", "code", "codes", "token", "secret", "otp", "totp", "file", "files"]);

const keyOf = (userId: string, screen: string) => `${PREFIX}${userId}:${screen}`;

/** The words of a field name: tempPassword, temp_password and temp-password are all "temp", "password". */
const wordsOf = (field: string) =>
  field
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .toLowerCase()
    .split(/[^a-z0-9]+/);

/** A field never kept: its name has a secret word in it. */
export const isSecretField = (field: string): boolean => wordsOf(field).some((word) => SECRET_WORDS.has(word));

/** Only plain values, without secret fields; anything else (a file, say) is left out. */
function plain(value: unknown): DraftValue | undefined {
  if (value === null || typeof value === "string" || typeof value === "boolean") return value;
  if (typeof value === "number") return Number.isFinite(value) ? value : undefined;
  if (Array.isArray(value)) return value.map(plain).filter((each): each is DraftValue => each !== undefined);
  if (typeof value === "object" && Object.getPrototypeOf(value) === Object.prototype) {
    const kept: Draft = {};
    for (const [field, inner] of Object.entries(value)) {
      if (isSecretField(field)) continue;
      const clean = plain(inner);
      if (clean !== undefined) kept[field] = clean;
    }
    return kept;
  }
  return undefined;
}

/** Every draft key in this tab, or none when storage is refused. */
function draftKeys(): string[] {
  try {
    const keys: string[] = [];
    for (let i = 0; i < sessionStorage.length; i++) {
      const key = sessionStorage.key(i);
      if (key?.startsWith(PREFIX)) keys.push(key);
    }
    return keys;
  } catch {
    return [];
  }
}

function remove(key: string): void {
  try {
    sessionStorage.removeItem(key);
  } catch {
    // Storage refused: nothing was kept.
  }
}

const waiting = new Map<string, { timer: ReturnType<typeof setTimeout>; write: () => void }>();

function cancel(key: string): void {
  const pending = waiting.get(key);
  if (pending) clearTimeout(pending.timer);
  waiting.delete(key);
}

/** Keep this screen's draft now. Null (or nothing left once secrets are out) removes it. */
export function saveDraft(userId: string, screen: string, fields: Draft | null): void {
  const key = keyOf(userId, screen);
  cancel(key);
  const kept = fields === null ? null : (plain(fields) as Draft);
  if (kept === null || Object.keys(kept).length === 0) {
    remove(key);
    return;
  }
  try {
    sessionStorage.setItem(key, JSON.stringify({ userId, fields: kept }));
  } catch {
    // Storage refused or full: no draft.
  }
}

/** Keep this screen's draft in a moment; a newer call replaces a waiting one. */
export function saveDraftSoon(userId: string, screen: string, fields: Draft | null): void {
  const key = keyOf(userId, screen);
  cancel(key);
  const write = () => saveDraft(userId, screen, fields);
  waiting.set(key, { timer: setTimeout(write, WAIT_MS), write });
}

/** Write every waiting draft now: the page is about to go. */
export function flushDrafts(): void {
  for (const { write } of [...waiting.values()]) write();
}

/** This user's draft for this screen, or null: only ever read under this user's own key. A save
 * of it still waiting is written first, so a quick remount gets the last keystrokes back. */
export function readDraft(userId: string, screen: string): Draft | null {
  const key = keyOf(userId, screen);
  waiting.get(key)?.write();
  try {
    const raw = sessionStorage.getItem(key);
    if (raw === null) return null;
    const stored: unknown = JSON.parse(raw);
    const fields = typeof stored === "object" && stored !== null && "fields" in stored ? stored.fields : null;
    return typeof fields === "object" && fields !== null && !Array.isArray(fields) ? (fields as Draft) : null;
  } catch {
    return null;
  }
}

/** Forget this screen's draft (the work was sent), a waiting save included. */
export function clearDraft(userId: string, screen: string): void {
  const key = keyOf(userId, screen);
  cancel(key);
  remove(key);
}

/** Drop the drafts of everyone but this user. */
export function keepDraftsOf(userId: string): void {
  for (const key of [...waiting.keys()]) if (!key.startsWith(keyOf(userId, ""))) cancel(key);
  for (const key of draftKeys()) if (!key.startsWith(keyOf(userId, ""))) remove(key);
}

/** Forget every draft in this tab: signed out, or someone else signed in. */
export function clearDrafts(): void {
  for (const key of [...waiting.keys()]) cancel(key);
  for (const key of draftKeys()) remove(key);
}
