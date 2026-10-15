"use client";

// Answer settings (features 25-27), in the scope chip's row: which model answers, by
// name, and how hard it thinks (High, Medium, Low), from what the organisation allows.
// Hidden when there is only one choice. With Ensemble allowed, up to three models are
// ticked and answer together; High then works with one model only. A native <details>:
// its summary is the chip, and it keeps focus when it closes. No price or cost here.

import { useId } from "react";
import libraryStyles from "@/components/documents/Library.module.css";
import type { ChatOptions } from "@/lib/types";
import { modelName } from "./answer";
import { allowedLevels, chooseAgent, LEVEL_NAMES, pickSummary, toggleAgent } from "./logic";
import type { AnswerPick } from "./logic";
import styles from "./Chat.module.css";

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

  return (
    <details className={styles.answerSettings}>
      <summary className={`btn btn-quiet ${styles.answerSummary}`}>
        <span className="muted">Answer with:</span> <strong>{pickSummary(options, pick)}</strong>
      </summary>
      <fieldset className={`${libraryStyles.fieldset} ${styles.answerChoices}`} disabled={locked}>
        <legend>Answer settings</legend>
        {options.ensemble ? (
          <fieldset className={libraryStyles.fieldset} aria-describedby={`${id}-most`}>
            <legend>Answered by</legend>
            {options.agents.map((agent) => {
              const ticked = pick.agents.includes(agent.id);
              return (
                <label key={agent.id} className={libraryStyles.check}>
                  <input
                    type="checkbox"
                    checked={ticked}
                    disabled={!ticked && full}
                    aria-describedby={!ticked && full ? `${id}-most` : undefined}
                    onChange={() => onPick(toggleAgent(options, pick, agent.id))}
                  />
                  {modelName(agent.name)}
                </label>
              );
            })}
            <p id={`${id}-most`} className="hint">
              Up to {options.max_agents} at once. They answer together, and the answer says if they agree.
            </p>
          </fieldset>
        ) : options.agents.length > 1 ? (
          <div className="field">
            <label htmlFor={`${id}-model`}>Answered by</label>
            <select
              id={`${id}-model`}
              className="input"
              value={pick.agents[0] ?? ""}
              onChange={(event) => onPick(chooseAgent(options, pick, event.target.value))}
            >
              {pick.agents.length === 0 ? <option value="">Usual choice</option> : null}
              {options.agents.map((agent) => (
                <option key={agent.id} value={agent.id}>
                  {modelName(agent.name)}
                </option>
              ))}
            </select>
          </div>
        ) : null}
        {options.levels.length > 1 ? (
          <div className="field">
            <label htmlFor={`${id}-level`}>Thinking</label>
            <select
              id={`${id}-level`}
              className="input"
              value={pick.level}
              aria-describedby={highDropped ? `${id}-high` : undefined}
              onChange={(event) =>
                onPick({ ...pick, level: levels.find((level) => level === event.target.value) ?? pick.level })
              }
            >
              {levels.map((level) => (
                <option key={level} value={level}>
                  {LEVEL_NAMES[level]}
                </option>
              ))}
            </select>
            {highDropped ? (
              <p id={`${id}-high`} className="hint">
                High works only when one answers alone.
              </p>
            ) : null}
          </div>
        ) : null}
      </fieldset>
    </details>
  );
}
