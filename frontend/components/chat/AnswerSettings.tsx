"use client";

// Answer settings (features 25-27), at the right of the composer's toolbar beside
// Send: a quiet button naming the pick ("Gemini 3.8 Flash · High") that opens a
// small panel (ComposerMenu.tsx) with which model answers, by name, and how hard
// it thinks (High, Medium, Low), from what the organisation allows. Hidden when
// there is only one choice. With Ensemble allowed, up to three models are ticked
// and answer together; High then works with one model only. No price or cost here.

import { useId } from "react";
import type { ChatOptions, ThinkingLevel } from "@/lib/types";
import { modelName } from "./answer";
import { ComposerMenu } from "./ComposerMenu";
import { allowedLevels, chooseAgent, LEVEL_NAMES, pickSummary, toggleAgent } from "./logic";
import type { AnswerPick } from "./logic";
import styles from "./Chat.module.css";

/** Strongest first, as the segments read left to right. */
const LEVEL_ORDER: ThinkingLevel[] = ["high", "medium", "low"];

const LEVEL_HINTS: Record<ThinkingLevel, string> = {
  high: "Thinks longest, best for tricky questions.",
  medium: "Balanced.",
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
  const levels = allowedLevels(options, pick);
  const full = pick.agents.length >= options.max_agents;
  const highDropped = levels.length < options.levels.length;
  const shown = LEVEL_ORDER.filter((level) => levels.includes(level));

  return (
    <ComposerMenu label={pickSummary(options, pick)} name="Answer settings" title="Answer settings" end>
      <fieldset className={styles.menuGroup} disabled={locked}>
        <legend className="visually-hidden">Answer settings</legend>
        {options.agents.length > 1 ? (
          <fieldset
            className={styles.menuGroup}
            aria-describedby={options.ensemble ? `${id}-most` : undefined}
          >
            <legend>Answered by</legend>
            {options.agents.map((agent) => {
              const ticked = pick.agents.includes(agent.id);
              return (
                <label key={agent.id} className={styles.menuOption}>
                  {options.ensemble ? (
                    <input
                      type="checkbox"
                      checked={ticked}
                      disabled={!ticked && full}
                      aria-describedby={!ticked && full ? `${id}-most` : undefined}
                      onChange={() => onPick(toggleAgent(options, pick, agent.id))}
                    />
                  ) : (
                    <input
                      type="radio"
                      name={`${id}-model`}
                      checked={ticked}
                      onChange={() => onPick(chooseAgent(options, pick, agent.id))}
                    />
                  )}
                  {modelName(agent.name)}
                </label>
              );
            })}
            {options.ensemble ? (
              <p id={`${id}-most`} className={styles.menuHint}>
                Up to {options.max_agents} answer together; the answer says if they agree.
              </p>
            ) : null}
          </fieldset>
        ) : null}
        {options.levels.length > 1 ? (
          <fieldset className={styles.menuGroup}>
            <legend>Thinking</legend>
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
            {highDropped ? <p className={styles.menuHint}>High works only when one answers alone.</p> : null}
          </fieldset>
        ) : null}
      </fieldset>
    </ComposerMenu>
  );
}
