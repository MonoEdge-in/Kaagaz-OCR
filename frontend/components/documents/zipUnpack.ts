// A zip upload the server unpacks into a folder (#522), as the upload tray shows it: the server's
// one line ("Unpacking: 30 of 120 files into folder "X"…", then "Unpacked: 120 files into folder
// "X", 3 skipped (...)"), the folder to open, and the files it skipped, each with why. Kept pure
// so it is unit tested in node (tests/zip-unpack.test.ts).

import type { DocumentSummary, ZipSkippedFile } from "@/lib/types";

export interface ZipNote {
  line: string; // the server's status_message: already the progress or the result line
  folderId: string | null; // the folder it was unpacked into; null once that folder is deleted
  done: boolean; // nothing is waiting to be unpacked any more
  skipped: ZipSkippedFile[]; // the first ones the server sends, in the zip's order
  more: number; // skipped files not listed
}

/** The tray's note for a zip document once the server has started unpacking it; null for any
 * other document, and for a zip still waiting for its virus check (its status message says so). */
export function zipNote(document: Pick<DocumentSummary, "detected_type" | "status_message" | "unpack">): ZipNote | null {
  const unpack = document.unpack;
  if (document.detected_type !== "zip" || !unpack) return null;
  return {
    line: document.status_message,
    folderId: unpack.folder_id,
    done: unpack.waiting === 0,
    skipped: unpack.skipped_files,
    more: Math.max(0, unpack.skipped - unpack.skipped_files.length),
  };
}

/** "and 3 more" under the skipped files listed; "" when every one is listed. */
export function moreSkippedText(more: number): string {
  return more > 0 ? `and ${more} more skipped` : "";
}
