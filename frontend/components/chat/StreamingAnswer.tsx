"use client";

// #535 (Tushar, 7 Nov 2026), PR 1: the answer card while the answer streams, in place of
// the old spinner and grey skeleton lines. No fake words: only the real stages, the real
// sources and the sentences that passed verification (stream.ts). In the card's order:
// the verified sentences, revealed word by word with their citation numbers landing at the
// end, so they sit where the final card puts them and nothing moves when it settles; then
// the stages, a soft pulse on the one running and a small check on each one done; then
// "Found 12 passages in 3 documents" and the documents' names, which fade and rise in, one
// after another. Once `final` (done's reply) has finished revealing, onSettle hands over to
// the final answer card (AnswerView's `settle`). Reduced motion: everything at once.
// Screen readers hear a polite live region instead of the moving text: each stage, the
// sources, and each sentence once, never word by word.

import { useEffect, useEffectEvent, useLayoutEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { Icon } from "@/components/ui/Icon/Icon";
import type { ChatResponse } from "@/lib/types";
import { useMediaQuery } from "./hooks";
import { STILL_LOOKING_AFTER_MS } from "./logic";
import {
  advance,
  foundText,
  MAX_CHIPS,
  moreChipsText,
  previewOpening,
  previewSentences,
  REVEAL_START,
  revealAll,
  SETTLE_PAUSE_MS,
  STAGE_TEXT,
  stageList,
  wordsOf,
} from "./stream";
import type { Reveal, StreamProgress } from "./stream";
import styles from "./Chat.module.css";

export const STILL_LOOKING = "Still looking. Long registers take a little longer.";
const DEVANAGARI = /[ऀ-ॿ]/u;

/** The reveal so far: one requestAnimationFrame loop while words are left to show. */
function useReveal(counts: readonly number[], instant: boolean): Reveal {
  const [reveal, setReveal] = useState(REVEAL_START);
  const current = useRef(REVEAL_START);
  const key = counts.join(",");
  useEffect(() => {
    if (instant) return;
    const list = key === "" ? [] : key.split(",").map(Number);
    let frame = requestAnimationFrame(function tick(now: number) {
      const before = current.current;
      const next = advance(before, list, now);
      current.current = next;
      if (next.sentence !== before.sentence || next.words !== before.words) setReveal(next);
      if (next.sentence < list.length) frame = requestAnimationFrame(tick);
    });
    return () => cancelAnimationFrame(frame);
  }, [key, instant]);
  return instant ? revealAll(counts) : reveal;
}

export function StreamingAnswer({
  progress,
  final,
  onSettle,
  onGrow,
}: {
  progress: StreamProgress;
  /** done's reply, once it is in; null until then. */
  final: ChatResponse | null;
  /** The reveal is over: show the final card. `changed`: it differs from the preview. */
  onSettle?: (changed: boolean) => void;
  /** The card grew: the thread follows it while the person is at the bottom. */
  onGrow?: () => void;
}) {
  const reduced = useMediaQuery("(prefers-reduced-motion: reduce)");
  const { sentences, changed } = previewSentences(progress, final);
  const words = sentences.map((sentence) => wordsOf(sentence.text));
  const reveal = useReveal(
    words.map((list) => list.length),
    reduced,
  );
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    const timer = window.setTimeout(() => setSlow(true), STILL_LOOKING_AFTER_MS);
    return () => window.clearTimeout(timer);
  }, []);

  // A changed preview is not the answer: no need to finish revealing it.
  const revealed = final !== null && (changed || reveal.sentence >= sentences.length);
  const settle = useEffectEvent(() => onSettle?.(changed));
  // A beat for the last citation number to land; none with nothing revealed, a changed preview or reduced motion.
  const pause = reduced || changed || sentences.length === 0 ? 0 : SETTLE_PAUSE_MS;
  useEffect(() => {
    if (!revealed) return;
    const timer = window.setTimeout(() => settle(), pause);
    return () => window.clearTimeout(timer);
  }, [revealed, pause]);

  const grow = useEffectEvent(() => onGrow?.());
  const stages = stageList(progress, final !== null);
  const found = foundText(progress.sources);
  // Not on mount: the screen scrolls a new question's card into view by itself.
  const grew = useRef(false);
  useLayoutEffect(() => {
    if (grew.current) grow();
    grew.current = true;
  }, [reveal.sentence, reveal.words, stages.length, found]);

  const shown = sentences.slice(0, reveal.sentence + (reveal.words > 0 ? 1 : 0));
  const stageNow = STAGE_TEXT[progress.stage ?? "understanding"];
  const chips = progress.sources?.documents.slice(0, MAX_CHIPS) ?? [];
  const more = progress.sources ? moreChipsText(progress.sources) : null;
  const opening = changed ? null : previewOpening(final);

  return (
    // No aria-busy: it would hold back the live region's words until the answer is in.
    <div className={styles.answerCard}>
      {opening ? (
        <p className={`${styles.opening} ${styles.streamOpening}`} aria-hidden="true">
          {opening}
        </p>
      ) : null}
      {shown.length > 0 ? (
        <p className={styles.answerText} aria-hidden="true" data-preview="">
          {shown.map((sentence, index) => {
            const whole = index < reveal.sentence;
            const list = words[index] ?? [];
            return (
              <span key={sentence.index}>
                {index > 0 ? " " : null}
                {(whole ? list : list.slice(0, reveal.words)).map((word, at) => (
                  <span key={at} className={styles.word} lang={DEVANAGARI.test(word) ? "mr" : undefined}>
                    {word}
                  </span>
                ))}
                {whole
                  ? sentence.citations.map((n) => (
                      <span key={n} className={`${styles.numberMark} ${styles.streamCite}`}>
                        {n}
                      </span>
                    ))
                  : null}
              </span>
            );
          })}
        </p>
      ) : null}
      <ol className={styles.stages} aria-hidden="true">
        {stages.map(({ stage, state }) => (
          <li key={stage} className={styles.stage} data-state={state}>
            <span className={styles.stageMark}>{state === "done" ? <Icon name="check" size="sm" /> : null}</span>
            {STAGE_TEXT[stage]}
          </li>
        ))}
      </ol>
      {found ? (
        <div className={styles.found} aria-hidden="true">
          <p className={styles.understood}>{found}</p>
          <ul className={styles.foundChips}>
            {chips.map((doc, index) => (
              <li key={doc.id} className={styles.foundChip} style={{ "--i": index } as CSSProperties} title={doc.name}>
                <span className={styles.foundName}>{doc.name}</span>
              </li>
            ))}
            {more ? (
              <li className={`${styles.foundChip} ${styles.foundMore}`} style={{ "--i": chips.length } as CSSProperties}>
                {more}
              </li>
            ) : null}
          </ul>
        </div>
      ) : null}
      {slow && shown.length === 0 && final === null ? (
        <p className={styles.understood} aria-hidden="true">
          {STILL_LOOKING}
        </p>
      ) : null}
      <div className="visually-hidden" aria-live="polite">
        <p>{stageNow}</p>
        {found ? <p>{found}.</p> : null}
        {opening ? <p>{opening}</p> : null}
        {slow && sentences.length === 0 && final === null ? <p>{STILL_LOOKING}</p> : null}
        {sentences.map((sentence) => (
          <p key={sentence.index}>{sentence.text.trim()}</p>
        ))}
      </div>
    </div>
  );
}
