"use client";

// Answer settings (features 25-27), at the right of the composer's toolbar beside
// Send: a quiet button naming the pick ("Precision") that opens a small panel
// (ComposerMenu.tsx) with which model answers and its AI engine (Swift is Medium,
// Precision is High; Low is not offered here), from what the organisation allows, capped by the effective Ask level (AI
// settings, spec 17 Oct 2026). Hidden when there is only one choice. One model answers
// (Tushar, 16 Oct 2026, one model in Ask): "Answered by" is one choice of radio buttons.
// Changed on purpose (spec 17 Oct 2026): org users never see a model's name, so the
// models are "Standard" and "Careful" (agentTier). No price or cost here.

import { useId } from "react";
import type { ChatOptions, ThinkingLevel } from "@/lib/types";
import { ComposerMenu } from "./ComposerMenu";
import { agentTier, askLevels, chooseAgent, LEVEL_NAMES, pickSummary } from "./logic";
import type { AnswerPick } from "./logic";
import styles from "./Chat.module.css";

/** Swift first, then Precision (SoW 19 Oct 2026; Low is not offered in Ask). */
const LEVEL_ORDER: ThinkingLevel[] = ["medium", "high"];

const LEVEL_HINTS: Record<ThinkingLevel, string> = {
  high: "Thinks longest, best for tricky questions.",
  medium: "Quick and balanced, for most questions.",
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
        {options.agents.length > 1 ? (
          <fieldset className={styles.menuGroup}>
            <legend>Answered by</legend>
            {options.agents.map((agent, index) => (
              <label key={agent.id} className={styles.menuOption}>
                <input
                  type="radio"
                  name={`${id}-model`}
                  checked={pick.agents[0] === agent.id}
                  onChange={() => onPick(chooseAgent(pick, agent.id))}
                />
                {agentTier(options, index)}
              </label>
            ))}
          </fieldset>
        ) : null}
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
            {/* Each level's hint describes its radio; the chosen one's is shown. */}
            {shown.map((level) => (
              <p key={level} id={`${id}-${level}`} className={styles.menuHint} hidden={pick.level !== level}>
                {LEVEL_HINTS[level]}
              </p>
            ))}
          </fieldset>
        ) : null}
      </fieldset>
    </ComposerMenu>
  );
}
