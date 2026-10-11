// The model an upload reads its scans with ("Read with", PRD 4 #17; model
// choice MS4, council 11 Oct 2026), kept out of UploadArea.tsx so it is unit
// tested in node (tests/upload-readers.test.ts). Every model, its words and
// whether this organisation may use it come from the server (GET
// /upload-limits, readers: the models MonoEdge allowed it): nothing here names
// one. The server lists the organisation's default first, so the first is
// picked at first and is never sent; the server reads with it anyway.

import type { UploadReader } from "@/lib/types";

/** One <option> of the select (not lib/types ReaderOption, which is what the server sends). */
export interface ReaderChoice {
  id: string;
  label: string;
  disabled: boolean;
}

/** Shown on a duplicate's row when a model was chosen: the earlier copy is not read again. */
export const DUPLICATE_READER_NOTE = "Already uploaded; it keeps the reader it was read with.";

/**
 * The select's options, in the server's order: the first is recommended; one
 * this server can't use is disabled, saying why.
 */
export function readerOptions(readers: readonly UploadReader[]): ReaderChoice[] {
  return readers.map((reader, index) => {
    let label = reader.title;
    if (!reader.available) label = `${reader.title} (${reader.reason ?? "not available"})`;
    else if (index === 0) label = `${reader.title} (recommended)`;
    return { id: reader.id, label, disabled: !reader.available };
  });
}

/** Only a real choice is shown: at least two models this organisation can use. */
export function offersChoice(readers: readonly UploadReader[]): boolean {
  return readers.filter((reader) => reader.available).length >= 2;
}

/** The model in use: the one picked while it is offered and usable, else the first (the default). */
export function pickedReader(readers: readonly UploadReader[], picked: string | null): UploadReader | null {
  return readers.find((reader) => reader.id === picked && reader.available) ?? readers[0] ?? null;
}

/** What an upload sends as read_model: the picked model, or undefined for the default. */
export function readerToSend(readers: readonly UploadReader[], picked: string | null): string | undefined {
  if (!offersChoice(readers)) return undefined;
  const reader = pickedReader(readers, picked);
  return reader !== null && reader !== readers[0] ? reader.id : undefined;
}

/**
 * The whole document's "Read again" (MS4): the model it is read with, sent
 * even when it is the default, so it is the one the warning names; undefined
 * when there is no choice (the server keeps its own).
 */
export function readAgainModel(readers: readonly UploadReader[], picked: string | null): string | undefined {
  return offersChoice(readers) ? pickedReader(readers, picked)?.id : undefined;
}

/** Said under that select: every page is read, and paid for, again. pages null: not counted yet. */
export function readAgainWarning(pages: number | null, model: string): string {
  if (pages === 1) return `The page will be read again by ${model}.`;
  return pages === null
    ? `All pages will be read again by ${model}.`
    : `All ${pages} pages will be read again by ${model}.`;
}
