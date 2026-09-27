"use client";

// This person's past conversations, newest first, and a button to start a new one.

import { formatDateTime } from "@/lib/format";
import type { ConversationSummary } from "@/lib/types";
import styles from "./Chat.module.css";

export function ConversationList({
  items,
  error,
  activeId,
  disabled,
  onOpen,
  onNew,
}: {
  items: ConversationSummary[] | null;
  error: string | null;
  activeId: string | null;
  disabled: boolean;
  onOpen: (id: string) => void;
  onNew: () => void;
}) {
  return (
    <nav className={`card stack ${styles.conversations}`} aria-label="Conversations">
      <button type="button" className="btn btn-primary" disabled={disabled} onClick={onNew}>
        New conversation
      </button>
      {error ? <p className="error-text">{error}</p> : null}
      {items === null && !error ? <p className="muted">Loading…</p> : null}
      {items !== null && items.length === 0 ? <p className="muted">No conversations yet.</p> : null}
      {items !== null && items.length > 0 ? (
        <ul className={styles.conversationItems}>
          {items.map((item) => {
            const active = item.id === activeId;
            return (
              <li key={item.id}>
                <button
                  type="button"
                  className={active ? `${styles.conversation} ${styles.conversationActive}` : styles.conversation}
                  aria-current={active ? "true" : undefined}
                  disabled={disabled}
                  onClick={() => onOpen(item.id)}
                >
                  <span className={styles.conversationTitle}>{item.title}</span>
                  <span className="muted">
                    {formatDateTime(item.created_at)}
                    {active ? " (open)" : ""}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      ) : null}
    </nav>
  );
}
