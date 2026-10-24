"use client";

// "What is this?" at the top of the document panel (document panel UX, 25 Oct 2026): the
// card's AI summary, moved here from Details, under its caution, at most about five lines
// until "Show the full summary"; then up to five of the card's own facts, each with its
// label (Verified or Ready to verify). Nothing is added or worked out. Devanagari runs carry
// lang="mr". No card yet: one quiet line while it is read; nothing when none will come.
// It rises in once, when it first appears (the CSS; never on a tab change: it sits above them).

import { useId, useState } from "react";
import type { CSSProperties } from "react";
import { scriptParts } from "@/components/chat/answer";
import { Icon } from "@/components/ui/Icon/Icon";
import { LabelBadge } from "@/components/viewer/LabelBadge";
import { cardSections } from "@/components/viewer/panels";
import type { Spot } from "@/components/viewer/panels";
import { beingRead } from "@/components/viewer/reprocess";
import { valueAt } from "@/components/viewer/scanned";
import { sameRegion } from "@/components/viewer/selection";
import type { DocumentDetail, Region } from "@/lib/types";
import styles from "./DocumentView.module.css";

export const SUMMARY_TEXT = {
  heading: "Summary",
  caution: "Written by AI. Check it against the pages.",
  waiting: "The summary appears when reading finishes.",
  more: "Show the full summary",
  less: "Show less",
};

export const MAX_FACTS = 5;
/** About five lines at the panel's width: a longer summary is cut there until opened. */
export const LONG_SUMMARY = 320;

/** Text with each Devanagari run marked lang="mr". Plain text only. */
export function ScriptText({ text }: { text: string }) {
  return (
    <>
      {scriptParts(text).map((part, index) =>
        part.lang ? (
          <span key={index} lang={part.lang}>
            {part.text}
          </span>
        ) : (
          part.text
        ),
      )}
    </>
  );
}

export function SummaryCard({
  detail,
  active,
  onShow,
}: {
  detail: DocumentDetail;
  active: Region | null;
  onShow: (spot: Spot) => void;
}) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const card = detail.card;
  if (card === null) {
    const coming = beingRead(detail.status) || detail.understanding === true;
    return coming ? <p className={styles.summaryWaiting}>{SUMMARY_TEXT.waiting}</p> : null;
  }
  const facts = cardSections(card, detail.values)
    .flatMap((section) => section.items)
    .slice(0, MAX_FACTS);
  if (!card.summary && facts.length === 0) return null;
  const long = (card.summary?.length ?? 0) > LONG_SUMMARY;

  return (
    <section className={styles.summaryCard} aria-labelledby={`${id}-heading`}>
      <h2 id={`${id}-heading`} className={styles.summaryHeading}>
        {SUMMARY_TEXT.heading}
      </h2>
      <p className={styles.caution}>{SUMMARY_TEXT.caution}</p>
      {card.summary && (
        <p id={`${id}-text`} className={styles.summaryText} data-clamped={long && !open ? "true" : undefined} data-open={open || undefined}>
          <ScriptText text={card.summary} />
        </p>
      )}
      {long && (
        <button
          type="button"
          className={`btn btn-quiet ${styles.expand}`}
          aria-expanded={open}
          aria-controls={`${id}-text`}
          onClick={() => setOpen(!open)}
        >
          {open ? SUMMARY_TEXT.less : SUMMARY_TEXT.more}
          <Icon name="chev-down" size="sm" className={styles.chevron} />
        </button>
      )}
      {facts.length > 0 && (
        <dl className={styles.keyFacts}>
          {facts.map((item, index) => {
            const pressed = active !== null && sameRegion(active, item.region);
            return (
              <div key={item.key} className={styles.keyFact} style={{ "--i": index } as CSSProperties}>
                <dt>{item.name}</dt>
                <dd>
                  <button type="button" className={styles.valueButton} aria-pressed={pressed} onClick={() => onShow(item)}>
                    <ScriptText text={item.text} />
                    <span className="visually-hidden">{`, show on page ${item.region.page}`}</span>
                  </button>
                  <LabelBadge label={valueAt(detail.values, item.region)?.label ?? "needs_review"} />
                </dd>
              </div>
            );
          })}
        </dl>
      )}
    </section>
  );
}
