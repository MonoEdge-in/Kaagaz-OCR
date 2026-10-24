"use client";

// Answer quality (Tushar, 24 Oct 2026: "give it its own icon, High, Extra high, Ultra, not in the
// chat, and an Off"): in the Ask bar, beside New, a quiet button naming the quality ("Off",
// "High") that opens a small panel (ComposerMenu.tsx; it opens below, there being no room
// above). Off is the organisation's usual model and sends nothing; High, Extra high and Ultra
// are its other models in order (qualityChoices), never by name. Locked while a question is out.

import { useId } from "react";
import { Icon } from "@/components/ui/Icon";
import type { ChatOptions } from "@/lib/types";
import { ComposerMenu } from "./ComposerMenu";
import { chooseQuality, qualityChoices, qualityOf } from "./logic";
import type { AnswerPick } from "./logic";
import styles from "./Chat.module.css";

export function QualityPicker({
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
  const current = qualityOf(options, pick);
  return (
    <ComposerMenu
      label={
        <span className={styles.quality}>
          <Icon name="check-seal" size={16} />
          {current}
        </span>
      }
      name="Answer quality"
      title="Answer quality"
      end
    >
      <fieldset className={styles.menuGroup} disabled={locked}>
        <legend>Answer quality</legend>
        {qualityChoices(options).map((choice) => (
          <label key={choice.id ?? "off"} className={styles.menuOption}>
            <input
              type="radio"
              name={`${id}-quality`}
              checked={choice.name === current}
              aria-describedby={`${id}-${choice.id ?? "off"}`}
              onChange={() => onPick(chooseQuality(options, pick, choice.id))}
            />
            <span className={styles.qualityWords}>
              {choice.name}
              <span id={`${id}-${choice.id ?? "off"}`} className={styles.menuHint}>
                {choice.hint}
              </span>
            </span>
          </label>
        ))}
      </fieldset>
    </ComposerMenu>
  );
}
