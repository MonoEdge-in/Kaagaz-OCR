// "From your stories" (S3b): up to three of the user's own documents whose story
// the question tells again, as a labelled region with a list of cards. It sits
// beside the answer or the results, never inside them, and looks unlike a
// source card (dashed edge, its own note). The band is always in words. Every
// text is React text, so a name with markup shows as plain characters. The
// screen announces the cards itself (storyStatusText); this box adds nothing
// to a live region. The logic is in lib/storyMatches.ts. "Search my stories"
// gives its own heading and band words; cards left behind by a failed answer
// say which question they belong to.

import Link from "next/link";
import { useId } from "react";
import { STORY_HEADING, storyCards } from "@/lib/storyMatches";
import type { StoryBand, StoryMatch } from "@/lib/types";
import styles from "./StoryMatches.module.css";

export interface StoryMatchesProps {
  matches: readonly StoryMatch[];
  note?: string;
  headingLevel: 2 | 3;
  heading?: string;
  bands?: Record<StoryBand, string>;
  question?: string; // shown as "For your question: ..." when no answer follows it
}

export function StoryMatches({
  matches,
  note,
  headingLevel,
  heading = STORY_HEADING,
  bands,
  question,
}: StoryMatchesProps) {
  const headingId = useId();
  if (matches.length === 0) return null;
  const Heading = headingLevel === 2 ? "h2" : "h3";
  return (
    <section className={styles.box} aria-labelledby={headingId} data-stories="">
      <Heading id={headingId} className={styles.heading}>
        {heading}
      </Heading>
      {question !== undefined ? <p className={styles.note}>For your question: {question}</p> : null}
      {note ? <p className={styles.note}>{note}</p> : null}
      <ul className={styles.cards}>
        {storyCards(matches, bands).map((card) => (
          <li key={card.id} className={styles.card} data-lead={card.lead ? "" : undefined}>
            <div className={styles.text}>
              <p className={styles.name}>{card.name}</p>
              <p className={styles.meta}>
                <span>{card.type}</span>
                <span className={styles.band}>{card.band}</span>
              </p>
            </div>
            <Link
              href={card.href}
              className={card.lead ? `btn btn-primary ${styles.open}` : `btn ${styles.open}`}
            >
              {card.openText}
              <span className="visually-hidden">: {card.name}</span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
