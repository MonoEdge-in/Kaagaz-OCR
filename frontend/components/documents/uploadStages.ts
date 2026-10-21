// The upload row's bar tells the truth (PRD 5 U10), kept pure so it is unit tested
// in node (tests/upload-stages.test.ts). Times are performance.now() milliseconds.
//
// - Three stages: Sending (bytes sent), Saving (from the last byte to the server's
//   reply: the server copies, hashes, stores and queues the file) and Reading.
// - While sending or saving, the percentage is the upload's share of the whole job,
//   each stage weighted by its expected time: sending by the upload speed measured so
//   far, saving by the file's size, reading by the page estimate. So the last byte is
//   never near 100%.
// - Once the server has the document, it is the document's own fraction, the same as
//   its table row and a one-file batch's bar (lib/progress.ts documentFraction).
// - It never moves backwards, never more than MAX_STEP_PERCENT in one tick (the
//   browser reports bytes in coarse steps: 20%, then 100%), and stays at most
//   CAP_PERCENT (lib/progress.ts) until the document is ready; then the bar is gone.

import { CAP_PERCENT, documentFraction } from "@/lib/progress";
import { uploadStateText } from "./logic";
import type { UploadState } from "./logic";

export const MAX_STEP_PERCENT = 15;
// ponytail: fixed guesses until a speed is measured; the server's own saving time is not reported.
export const ASSUMED_BYTES_PER_SECOND = 500_000;
const SAVE_BYTES_PER_SECOND = 20_000_000;
const MIN_SAVE_SECONDS = 2;
const SAVING_AT_MOST = 0.9; // saving never fills its share before the reply
const MEASURED_FROM = 0.05; // the speed is trusted once 5% has gone

export type Stage = "sending" | "saving" | "reading";

export interface StageTimes {
  startedAt: number | null; // sending started
  sentAt: number | null; // the last byte went (Saving starts)
  repliedAt: number | null; // the server replied (Reading starts)
}

export const NO_TIMES: StageTimes = { startedAt: null, sentAt: null, repliedAt: null };

/** What the bar shows: the highest whole percent so far, and the tick it last moved at. */
export interface Shown {
  percent: number;
  at: number;
}

/** The stage a row is in; null when it has no bar (waiting, done, refused, failed). */
export function stageOf(state: UploadState): Stage | null {
  return state.kind === "sending" || state.kind === "saving" || state.kind === "reading" ? state.kind : null;
}

const seconds = (from: number | null, to: number | null) =>
  from !== null && to !== null ? Math.max(0, (to - from) / 1000) : null;

/**
 * The row's fraction at ``now``. Sending or saving: (sending + saving done) over the three
 * expected times, ``readSeconds`` the reading estimate (estimateIngestSeconds). Reading:
 * the document's own fraction, the one its table row shows (documentFraction).
 */
export function jobFraction(
  state: UploadState,
  sizeBytes: number,
  readSeconds: number,
  times: StageTimes,
  now: number,
): number {
  const stage = stageOf(state);
  if (stage === null) return 0;
  if (state.kind === "reading") {
    // Progress fix, 16 Oct 2026: not weighed with the send and save seconds and a 7 s a
    // page guess any more, which made the row disagree with the table.
    const { progress, time_left: value } = state.document;
    return documentFraction(progress?.step, value ?? null, now) ?? 0;
  }
  const size = Math.max(0, sizeBytes);
  let send = seconds(times.startedAt, times.sentAt);
  if (send === null) {
    const elapsed = seconds(times.startedAt, now) ?? 0;
    const fraction = state.kind === "sending" ? state.fraction : 0;
    send = fraction >= MEASURED_FROM && elapsed > 0 ? elapsed / fraction : size / ASSUMED_BYTES_PER_SECOND;
  }
  const save = seconds(times.sentAt, times.repliedAt) ?? Math.max(MIN_SAVE_SECONDS, size / SAVE_BYTES_PER_SECOND);
  const read = Math.max(1, readSeconds);
  const total = send + save + read;
  if (state.kind === "sending") return (send * Math.min(1, Math.max(0, state.fraction))) / total;
  if (state.kind === "saving") {
    const saving = seconds(times.sentAt, now) ?? 0;
    return (send + save * Math.min(SAVING_AT_MOST, saving / save)) / total;
  }
  return 0;
}

/** The next thing to show at tick ``now``: one step a tick at most, never back, at most CAP_PERCENT. */
export function nextShown(previous: Shown | null, fraction: number, now: number): Shown {
  const shown = previous ?? { percent: 0, at: Number.NEGATIVE_INFINITY };
  if (now === shown.at || !Number.isFinite(fraction)) return shown;
  const target = Math.min(CAP_PERCENT, Math.floor(fraction * 100 + 1e-9));
  if (target <= shown.percent) return shown;
  return { percent: Math.min(target, shown.percent + MAX_STEP_PERCENT), at: now };
}

/**
 * The <progress>'s aria-valuetext: the row's badge words and its percent. Saving (indeterminate)
 * and a document waiting for the reader (U7, QA 17 Oct) have no percent.
 */
export function valueText(state: UploadState, percent: number): string {
  const words = uploadStateText(state);
  return state.kind === "saving" || words === "Waiting" ? words : `${words}, ${percent}%`;
}
