// #521: the words after a choice made on the compare screen, carried to the document page it goes
// back to and shown there once as its toast (with Undo after Upload as a new version). They name
// that document: another document's page leaves them (review, 6 Nov 2026). This tab only
// (sessionStorage), for a minute; a blocked or missing store shows nothing, and the choice
// still stands.

const KEY = "kaagaz.version-choice";
const KEEP_MS = 60_000;
const DOCUMENT_PAGE = /^\/documents\/([\w-]+)$/;

export interface Flash {
  message: string;
  documentId: string; // the page that shows them
  undoLinkOf?: string; // the document linked as a new version, for Undo (V7)
}

/** The document page the compare screen goes back to (`from`, else this document); null: none. */
export function flashTarget(from: { href: string; name: string } | null, documentId: string): string | null {
  if (from === null) return documentId;
  return DOCUMENT_PAGE.exec(from.href)?.[1] ?? null;
}

export function saveFlash(flash: Flash): void {
  try {
    sessionStorage.setItem(KEY, JSON.stringify({ ...flash, at: Date.now() }));
  } catch {
    // no store: the page goes back without the words
  }
}

/** The words saved for this document's page, once; null when there are none for it. */
export function takeFlash(documentId: string): Flash | null {
  try {
    const saved = sessionStorage.getItem(KEY);
    if (saved === null) return null;
    const flash = JSON.parse(saved) as Partial<Flash> & { at?: number };
    const stale = typeof flash.at !== "number" || Date.now() - flash.at > KEEP_MS;
    if (stale || typeof flash.message !== "string" || typeof flash.documentId !== "string") {
      sessionStorage.removeItem(KEY);
      return null;
    }
    if (flash.documentId !== documentId) return null; // another page's: left for it
    sessionStorage.removeItem(KEY);
    const shown: Flash = { message: flash.message, documentId };
    return typeof flash.undoLinkOf === "string" ? { ...shown, undoLinkOf: flash.undoLinkOf } : shown;
  } catch {
    return null;
  }
}
