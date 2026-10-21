// "Move 3 files to Trash?" asked in the page, next to what it acts on, never in
// a browser popup (Components.html, 11). The question in bold, what follows
// from it, then the action and Cancel. The action button says what happens.

import type { ReactNode } from "react";
import { Button, ButtonGroup } from "../Button/Button";
import { Icon } from "../Icon/Icon";
import type { KIconName } from "../Icon/Icon";
import styles from "./InlineConfirm.module.css";

export interface InlineConfirmProps {
  /** Names the confirmation for screen readers: "Confirm move to Trash". */
  label: string;
  question: ReactNode;
  /** What follows: "You can restore them for 30 days." */
  detail?: ReactNode;
  /** The action's words: "Move to Trash". */
  confirmLabel: string;
  /** While the action runs: "Moving…". */
  busyLabel?: string;
  busy?: boolean;
  icon?: KIconName;
  onConfirm(): void;
  onCancel(): void;
}

export function InlineConfirm({
  label,
  question,
  detail,
  confirmLabel,
  busyLabel,
  busy = false,
  icon = "trash",
  onConfirm,
  onCancel,
}: InlineConfirmProps) {
  return (
    <div className={styles.confirm} role="alertdialog" aria-label={label}>
      <Icon name={icon} />
      <span className={styles.message}>
        <strong>{question}</strong>
        {detail ? <> {detail}</> : null}
      </span>
      <ButtonGroup>
        <Button variant="danger" size="sm" loading={busy} onClick={onConfirm}>
          {busy && busyLabel ? busyLabel : confirmLabel}
        </Button>
        <Button size="sm" onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
      </ButtonGroup>
    </div>
  );
}
