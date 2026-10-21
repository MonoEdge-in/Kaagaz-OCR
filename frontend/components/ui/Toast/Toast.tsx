// A short note after an action (Components.html, 11), with Undo where the
// action can be undone. This is the note itself; where it sits (bottom left),
// how long it stays (6 s, held while the pointer or focus is on it) and the
// live region it is read out from belong to the app shell, which places it.

import type { ReactNode } from "react";
import { Button } from "../Button/Button";
import { Icon } from "../Icon/Icon";
import type { KIconName } from "../Icon/Icon";
import styles from "./Toast.module.css";

export interface ToastProps {
  icon?: KIconName;
  children: ReactNode;
  /** One action, usually Undo. */
  action?: { label: string; onClick(): void };
}

export function Toast({ icon = "check", children, action }: ToastProps) {
  return (
    <div className={styles.toast}>
      <Icon name={icon} />
      <span className={styles.message}>{children}</span>
      {action ? (
        <Button variant="quiet" size="sm" onClick={action.onClick}>
          {action.label}
        </Button>
      ) : null}
    </div>
  );
}
