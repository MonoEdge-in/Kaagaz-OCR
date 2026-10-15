// The model an upload reads its scans with ("Read with", PRD 4 #17; model
// choice MS4, council 11 Oct 2026), kept out of UploadArea.tsx so it is unit
// tested in node (tests/upload-readers.test.ts). Every model, its words and
// whether this organisation may use it come from the server (GET
// /upload-limits, readers: the models MonoEdge allowed it): nothing here names
// one. The server offers at most two tiers, "Standard" first and "Careful",
// never a model's name, a price or cost words (UI quality council, 15 Oct
// 2026). Standard is the organisation's default, marked (default), picked at
// first and never sent, since the server reads with it anyway. When the default
// can't be used now, no reader is marked, and the one shown is sent (MS4, R14).

import type { UploadReader } from "@/lib/types";

/** One <option> of the select (not lib/types ReaderOption, which is what the server sends). */
export interface ReaderChoice {
  id: string;
  label: string;
  disabled: boolean;
}

/** Shown on a duplicate's row when a model was chosen: the earlier copy is not read again. */
export const DUPLICATE_READER_NOTE = "Already uploaded, so it was not read again.";

/**
 * The organisation's default: the reader the server marks default. A reader
 * without the mark (Auto, PRD 5 RT3) is the default when it comes first.
 */
function isDefault(reader: UploadReader, index: number): boolean {
  return reader.default ?? index === 0;
}

/**
 * The select's options, in the server's order, by their titles; one this
 * server can't use is disabled, saying why.
 */
export function readerOptions(readers: readonly UploadReader[]): ReaderChoice[] {
  return readers.map((reader) => ({
    id: reader.id,
    label: reader.available ? reader.title : `${reader.title} (${reader.reason ?? "not available"})`,
    disabled: !reader.available,
  }));
}

/** Only a real choice is shown: at least two models this organisation can use. */
export function offersChoice(readers: readonly UploadReader[]): boolean {
  return readers.filter((reader) => reader.available).length >= 2;
}

/** The model in use: the one picked while it is offered and usable, else the first (the default). */
export function pickedReader(readers: readonly UploadReader[], picked: string | null): UploadReader | null {
  return readers.find((reader) => reader.id === picked && reader.available) ?? readers[0] ?? null;
}

/**
 * What an upload sends as read_model: the model shown, or undefined for the
 * organisation's default (the server reads with it anyway).
 */
export function readerToSend(readers: readonly UploadReader[], picked: string | null): string | undefined {
  if (!offersChoice(readers)) return undefined;
  const reader = pickedReader(readers, picked);
  return reader !== null && !isDefault(reader, readers.indexOf(reader)) ? reader.id : undefined;
}

/**
 * The whole document's "Read again" (MS4): the model the user picked in the
 * select, or undefined when they changed nothing, so the document keeps its own
 * model (read_model) and never moves to the default unasked; undefined too when
 * there is no choice.
 */
export function readAgainModel(readers: readonly UploadReader[], picked: string | null): string | undefined {
  if (picked === null || !offersChoice(readers)) return undefined;
  return readers.find((reader) => reader.id === picked && reader.available)?.id;
}

/**
 * Said under that select: every page is read again. No cost words: org admins and
 * members never see cost (Tushar, 15 Oct 2026). pages null: not counted yet.
 */
export function readAgainWarning(pages: number | null): string {
  if (pages === 1) return "The page will be read again.";
  if (pages === null) return "Every page will be read again.";
  return `All ${pages} pages will be read again.`;
}
