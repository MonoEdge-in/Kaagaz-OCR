// #521: the words after a choice made on the compare screen, carried to the page it goes back to
// and shown there once as its toast (with Undo after Upload as a new version). This tab only
// (sessionStorage), for a minute (going back to a list leaves it unread); a blocked or missing
// store shows nothing, and the choice still stands.

const KEY = "kaagaz.version-choice";
const KEEP_MS = 60_000;

export interface Flash {
  message: string;
  undoLinkOf?: string; // the document linked as a new version, for Undo (V7)
}

export function saveFlash(flash: Flash): void {
  try {
    sessionStorage.setItem(KEY, JSON.stringify({ ...flash, at: Date.now() }));
  } catch {
    // no store: the page goes back without the words
  }
}

/** The words saved for this page, once; null when there are none. */
export function takeFlash(): Flash | null {
  try {
    const saved = sessionStorage.getItem(KEY);
    sessionStorage.removeItem(KEY);
    if (saved === null) return null;
    const flash = JSON.parse(saved) as Partial<Flash> & { at?: number };
    if (typeof flash.message !== "string" || typeof flash.at !== "number" || Date.now() - flash.at > KEEP_MS) return null;
    return typeof flash.undoLinkOf === "string"
      ? { message: flash.message, undoLinkOf: flash.undoLinkOf }
      : { message: flash.message };
  } catch {
    return null;
  }
}
