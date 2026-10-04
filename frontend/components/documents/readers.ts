// The scan reader an upload may choose ("Scan with", PRD 4 #17, PROVISIONAL),
// kept out of UploadArea.tsx so it is unit tested in node
// (tests/upload-readers.test.ts). Every reader, its words and whether this
// server can use it come from the server (GET /upload-limits, readers): nothing
// here names one. The server lists its default (Auto) first, so the first is
// picked at first and is never sent; the server reads with it anyway.

import type { UploadReader } from "@/lib/types";

export interface ReaderOption {
  id: string;
  label: string;
  disabled: boolean;
}

/** Shown on a duplicate's row when a reader was chosen: the earlier copy is not read again. */
export const DUPLICATE_READER_NOTE = "Already uploaded; it keeps the reader it was read with.";

/**
 * The select's options, in the server's order: the first is recommended; one
 * this server can't use is disabled, saying why.
 */
export function readerOptions(readers: readonly UploadReader[]): ReaderOption[] {
  return readers.map((reader, index) => {
    let label = reader.title;
    if (!reader.available) label = `${reader.title} (${reader.reason ?? "not available"})`;
    else if (index === 0) label = `${reader.title} (recommended)`;
    return { id: reader.id, label, disabled: !reader.available };
  });
}

/** Only a real choice is shown: at least two readers this server can use. */
export function offersChoice(readers: readonly UploadReader[]): boolean {
  return readers.filter((reader) => reader.available).length >= 2;
}

/** The reader in use: the one picked while it is offered and usable, else the first (the default). */
export function pickedReader(readers: readonly UploadReader[], picked: string | null): UploadReader | null {
  return readers.find((reader) => reader.id === picked && reader.available) ?? readers[0] ?? null;
}

/** What an upload sends as ocr_choice: the picked reader, or undefined for the default. */
export function readerToSend(readers: readonly UploadReader[], picked: string | null): string | undefined {
  if (!offersChoice(readers)) return undefined;
  const reader = pickedReader(readers, picked);
  return reader !== null && reader !== readers[0] ? reader.id : undefined;
}
