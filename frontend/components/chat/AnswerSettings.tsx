"use client";

// Answer settings (features 25-27), at the right of the composer's toolbar beside
// Send: a quiet button naming the pick ("Precision") that opens a small panel
// (ComposerMenu.tsx) with which model answers and its AI engine (Swift is Medium,
// Precision is High; Low is not offered here), from what the organisation allows, capped by the effective Ask level (AI
// settings, spec 17 Oct 2026). Hidden when there is only one choice. One model answers
// (Tushar, 16 Oct 2026, one model in Ask): "Answered by" is one choice of radio buttons.
// Changed on purpose (spec 17 Oct 2026): org users never see a model's name. Changed on purpose
// (Vrushit UAT, 25 Oct 2026: "give only one option, Swift and Precision, and make sure there is a
// difference"): this panel is the one choice; Precision also answers on the organisation's
// strongest model (logic.ts choiceBody). No price or cost here.

import { useId } from "react";
import type { ChatOptions, ThinkingLevel } from "@/lib/types";
import { ComposerMenu } from "./ComposerMenu";
import { askLevels, LEVEL_NAMES, pickSummary } from "./logic";
import type { AnswerPick } from "./logic";
import styles from "./Chat.module.css";

/** Swift first, then Precision (SoW 19 Oct 2026; Low is not offered in Ask). */
const LEVEL_ORDER: ThinkingLevel[] = ["medium", "high"];

// Plain words (25 Oct 2026): what each does for the person asking. Changed on purpose (Vrushit
// UAT, 25 Oct 2026): what each really does differently (chat.py _choice, service.py _input_cap).
const LEVEL_HINTS: Record<ThinkingLevel, string> = {
  high: "Precision: reads more of your documents and takes more care, on the strongest AI your organisation allows. Slower; for important questions.",
  medium: "Swift: quick answers from the most relevant parts of your documents, for everyday questions.",
  low: "Fastest.",
};

export function AnswerSettings({
  options,
  pick,
  locked,
  onPick,
}: {
  options: ChatOptions;
  pick: AnswerPick;
  locked: boolean; // while a question is out
  onPick: (pick: AnswerPick) => void;
}) {
  const id = useId();
  const shown = LEVEL_ORDER.filter((level) => askLevels(options).includes(level));

  return (
    <ComposerMenu label={pickSummary(options, pick)} name="Answer settings" title="Answer settings" end>
      <fieldset className={styles.menuGroup} disabled={locked}>
        <legend className="visually-hidden">Answer settings</legend>
        {shown.length > 1 ? (
          <fieldset className={styles.menuGroup}>
            <legend>AI engine</legend>
            <div className={styles.segments}>
              {shown.map((level) => (
                <label key={level} className={styles.segment}>
                  <input
                    type="radio"
                    name={`${id}-level`}
                    checked={pick.level === level}
                    aria-describedby={`${id}-${level}`}
                    onChange={() => onPick({ ...pick, level })}
                  />
                  {LEVEL_NAMES[level]}
                </label>
              ))}
            </div>
            {/* Each level's hint describes its radio; both are shown, so the choice is clear
                before it is made (plain words, 25 Oct 2026). */}
            {shown.map((level) => (
              <p key={level} id={`${id}-${level}`} className={styles.menuHint}>
                {LEVEL_HINTS[level]}
              </p>
            ))}
          </fieldset>
        ) : null}
      </fieldset>
    </ComposerMenu>
  );
}
