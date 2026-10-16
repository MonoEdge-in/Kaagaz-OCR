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
// Features 26 and 27 (15 Oct 2026): the server may put "Dynamic" first, the default
// (it picks the right reading for each page), and offer "More than one reader…"
// (ensemble): the models by name, 2 or 3 ticked, the main reader tagged. What is
// sent is a Reading: read_mode, read_model, read_agents.

import type { EnsembleOffer, UploadReader, UploadReading as Reading } from "@/lib/types";

export const DYNAMIC = "dynamic"; // the server's id for Dynamic in readers
export const ENSEMBLE = "ensemble"; // the select's own option for more than one reader
export const ENSEMBLE_LABEL = "More than one reader…";
export const ENSEMBLE_HINT = "Each value that needs a check is decided by what most readers read.";
export const READERS_CAP = "Up to 3 readers.";

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
export function readerOptions(readers: readonly UploadReader[], ensemble?: EnsembleOffer | null): ReaderChoice[] {
  const options = readers.map((reader) => ({
    id: reader.id,
    label: reader.available ? reader.title : `${reader.title} (${reader.reason ?? "not available"})`,
    disabled: !reader.available,
  }));
  return ensemble ? [...options, { id: ENSEMBLE, label: ENSEMBLE_LABEL, disabled: false }] : options;
}

/** Only a real choice is shown: at least two readings this organisation can use. */
export function offersChoice(readers: readonly UploadReader[], ensemble?: EnsembleOffer | null): boolean {
  return readers.filter((reader) => reader.available).length + (ensemble ? 1 : 0) >= 2;
}

/** The readers ticked at first: the main reader alone (one more is needed). */
export function startAgents(ensemble: EnsembleOffer | null | undefined): string[] {
  return ensemble ? [ensemble.main] : [];
}

/** Tick or untick one reader, keeping the server's order; never more than ensemble.max. */
export function toggleAgent(ensemble: EnsembleOffer, ticked: readonly string[], id: string): string[] {
  const next = ticked.includes(id) ? ticked.filter((one) => one !== id) : [...ticked, id];
  if (next.length > ensemble.max) return [...ticked];
  return ensemble.readers.map((reader) => reader.id).filter((one) => next.includes(one));
}

/** A reader that can't be ticked now: the most are ticked already (its reason: READERS_CAP). */
export function agentDisabled(ensemble: EnsembleOffer, ticked: readonly string[], id: string): boolean {
  return !ticked.includes(id) && ticked.length >= ensemble.max;
}

/** The main reader, as the server picks it: the organisation's when ticked, else the first ticked. */
export function mainReader(ensemble: EnsembleOffer, ticked: readonly string[]): string | null {
  return ticked.includes(ensemble.main) ? ensemble.main : (ticked[0] ?? null);
}

/** Said under the readers: how many to tick. */
export function agentsHint(ensemble: EnsembleOffer, ticked: readonly string[]): string {
  if (ticked.length < 2) return `Tick 2 to ${ensemble.max} readers.`;
  return `${ticked.length} readers will read each page.`;
}

/**
 * What an upload sends: Ensemble's readers (only once 2 are ticked; until then
 * the organisation's default reading), Dynamic, or the model shown (readerToSend);
 * undefined for the organisation's default.
 */
export function readingToSend(
  readers: readonly UploadReader[],
  ensemble: EnsembleOffer | null | undefined,
  picked: string | null,
  agents: readonly string[],
): Reading | undefined {
  if (picked === ENSEMBLE && ensemble) return agents.length >= 2 ? { read_mode: ENSEMBLE, read_agents: [...agents] } : undefined;
  return asReading(readerToSend(readers, picked));
}

/** The whole document's Read again: as readingToSend, but only what the user picked (readAgainModel). */
export function readAgainReading(
  readers: readonly UploadReader[],
  ensemble: EnsembleOffer | null | undefined,
  picked: string | null,
  agents: readonly string[],
): Reading | undefined {
  if (picked === ENSEMBLE && ensemble) return agents.length >= 2 ? { read_mode: ENSEMBLE, read_agents: [...agents] } : undefined;
  return asReading(readAgainModel(readers, picked));
}

function asReading(id: string | undefined): Reading | undefined {
  if (id === undefined) return undefined;
  return id === DYNAMIC ? { read_mode: DYNAMIC } : { read_model: id };
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
