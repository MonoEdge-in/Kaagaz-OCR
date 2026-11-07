// #535 (Tushar, 7 Nov 2026), PR 1: what the streamed answer shows while it is on its way
// (StreamingAnswer.tsx), as plain functions. Real stages only, the real sources, and only
// sentences that passed verification: nothing here makes up a word. The reveal is one
// pattern: each verified sentence shows word by word, about 20ms a word but never more than
// 600ms a sentence, one sentence after another, its citation numbers landing at its end.
// When `done` comes, its reply is the truth: its sentences finish the reveal when the
// preview so far is the start of them; when it differs (a sentence dropped), the card
// settles at once and the difference fades (AnswerView's `settle`).

import type { Stage, StreamEvent, StreamSentence, StreamSources } from "@/lib/askStream";
import type { ChatResponse } from "@/lib/types";

export const STAGE_TEXT: Record<Stage, string> = {
  understanding: "Understanding your question…",
  searching: "Searching your documents…",
  writing: "Writing the answer…",
  checking: "Checking the answer…",
};

/** Document chips shown at most; the rest are "+N more". */
export const MAX_CHIPS = 6;
export const WORD_MS = 20;
export const SENTENCE_CAP_MS = 600;
/** After the last word, a beat for its citation number to land before the card settles. */
export const SETTLE_PAUSE_MS = 240;

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/** "Found 12 passages in 3 documents"; null when nothing was found. */
export function foundText(sources: StreamSources | null): string | null {
  if (!sources || sources.documents.length === 0) return null;
  return `Found ${plural(sources.passages, "passage", "passages")} in ${plural(sources.documents.length, "document", "documents")}`;
}

/** "+4 more" past the first MAX_CHIPS documents; null when they all fit. */
export function moreChipsText(sources: StreamSources): string | null {
  const more = sources.documents.length - MAX_CHIPS;
  return more > 0 ? `+${more} more` : null;
}

export interface StreamProgress {
  /** The stage running now; null before the first one. */
  stage: Stage | null;
  /** Every stage started so far, in order. */
  reached: Stage[];
  sources: StreamSources | null;
  /** The verified sentences, in order. */
  sentences: StreamSentence[];
}

export const NO_PROGRESS: StreamProgress = { stage: null, reached: [], sources: null, sentences: [] };

/** The progress after one event. A sentence out of order (or again) is dropped: `done` has it if it belongs. */
export function progressAfter(progress: StreamProgress, event: StreamEvent): StreamProgress {
  if (event.type === "stage") {
    const reached = progress.reached.includes(event.stage) ? progress.reached : [...progress.reached, event.stage];
    return { ...progress, stage: event.stage, reached };
  }
  if (event.type === "sources") return { ...progress, sources: event.sources };
  const last = progress.sentences.at(-1);
  if (last && event.sentence.index <= last.index) return progress;
  return { ...progress, sentences: [...progress.sentences, event.sentence] };
}

/** The stages to list, each done or the one running. Before the first event the server is
 * understanding the question (always its first stage); once the reply is in, all are done. */
export function stageList(progress: StreamProgress, finished: boolean): { stage: Stage; state: "done" | "active" }[] {
  const reached = progress.reached.length > 0 ? progress.reached : (["understanding"] as Stage[]);
  const active = progress.stage ?? "understanding";
  return reached.map((stage) => ({ stage, state: !finished && stage === active ? "active" : "done" }));
}

/** A sentence's words, each with the spaces after it, so the words joined are the sentence. */
export const wordsOf = (text: string): string[] => text.trim().match(/\S+\s*/g) ?? [];

const same = (a: StreamSentence, b: StreamSentence) => a.text.trim() === b.text.trim();

/** The final answer, when its card shows its sentences (an answered answer, not small talk). */
function written(final: ChatResponse) {
  const answer = final.answer.answer;
  return answer && answer.status === "answered" && answer.route !== "small_talk" ? answer : null;
}

/** The final card's opening line (contract v2, code-written), shown over the sentences it reveals
 * so the card settles with nothing moving; null without one. */
export const previewOpening = (final: ChatResponse | null): string | null =>
  (final && written(final)?.version === 2 && written(final)?.opening) || null;

/** The final answer's sentences, when its card shows them as sentences. */
function finalSentences(final: ChatResponse): StreamSentence[] {
  const answer = written(final);
  if (!answer) return [];
  return answer.sentences
    .filter((sentence) => sentence.text.trim() !== "")
    .map((sentence, index) => ({ index, text: sentence.text, citations: sentence.citations }));
}

/**
 * The sentences to reveal, and whether the final answer differs from the preview. Before
 * `done`: the streamed ones. After: the final answer's, when the preview is the start of
 * them (a fixed calculation streams none, so all of its sentences reveal now); otherwise the
 * preview stays as it is and `changed` says the card settles at once, the difference faded.
 */
export function previewSentences(
  progress: StreamProgress,
  final: ChatResponse | null,
): { sentences: StreamSentence[]; changed: boolean } {
  if (!final) return { sentences: progress.sentences, changed: false };
  const truth = finalSentences(final);
  const prefix = progress.sentences.length <= truth.length && progress.sentences.every((s, i) => same(s, truth[i]!));
  return prefix ? { sentences: truth, changed: false } : { sentences: progress.sentences, changed: true };
}

/** How far the reveal has got: whole sentences, then words of the next; since when that sentence shows. */
export interface Reveal {
  sentence: number;
  words: number;
  since: number | null;
}

export const REVEAL_START: Reveal = { sentence: 0, words: 0, since: null };

/** Milliseconds a word for a sentence of `count` words: 20, less for a long one (600ms at most). */
export const wordMs = (count: number) => Math.min(WORD_MS, SENTENCE_CAP_MS / Math.max(1, count));

/**
 * The reveal at time `now` (a frame's time), for sentences of `counts` words. One sentence
 * at a time: the next starts where the last ended, or when it arrives if the reveal was
 * waiting for it (since null). Its first word shows at once.
 */
export function advance(reveal: Reveal, counts: readonly number[], now: number): Reveal {
  let { sentence, since } = reveal;
  while (sentence < counts.length) {
    const count = Math.max(1, counts[sentence]!);
    if (since === null) since = now;
    const per = wordMs(count);
    const words = Math.min(count, Math.max(0, Math.floor((now - since) / per) + 1));
    if (words < count) return { sentence, words, since };
    since += count * per;
    sentence += 1;
  }
  return { sentence, words: 0, since: null };
}

/** Everything shown at once (reduced motion). */
export const revealAll = (counts: readonly number[]): Reveal => ({ sentence: counts.length, words: 0, since: null });
