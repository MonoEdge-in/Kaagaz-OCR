"use client";

// This person's past conversations, grouped Today / Yesterday / Earlier by the
// India day each was started, and a button to start a new one. The same list
// sits in the side pane on wide screens and in the drawer below 1024 px.

import { useId } from "react";
import { Icon } from "@/components/ui/Icon";
import type { ConversationSummary } from "@/lib/types";
import { useMinuteNow } from "./hooks";
import { conversationTime, groupConversations } from "./logic";
import styles from "./Chat.module.css";

export function ConversationList({
  items,
  failed,
  activeId,
  disabled,
  onOpen,
  onNew,
  onRetry,
}: {
  items: ConversationSummary[] | null;
  failed: boolean;
  activeId: string | null;
  disabled: boolean;
  onOpen: (id: string) => void;
  onNew: () => void;
  onRetry: () => void;
}) {
  const now = useMinuteNow();
  const headingPrefix = useId();
  const groups = items ? groupConversations(items, now) : [];

  return (
    <nav className={styles.conversations} aria-label="Conversations">
      <button type="button" className="btn btn-primary btn-block" disabled={disabled} onClick={onNew}>
        <Icon name="plus" />
        New conversation
      </button>

      {failed ? (
        <div className={styles.listError}>
          <p className="error-text" role="alert">
            Could not load your conversations.
          </p>
          <button type="button" className="btn" onClick={onRetry}>
            <Icon name="refresh" size={16} />
            Try again
          </button>
        </div>
      ) : null}

      {items === null && !failed ? (
        <div className={`${styles.listSkeleton} delayed`} aria-hidden="true">
          <span className={`skeleton ${styles.listSkeletonItem}`} />
          <span className={`skeleton ${styles.listSkeletonItem}`} />
          <span className={`skeleton ${styles.listSkeletonItem}`} />
        </div>
      ) : null}

      {items !== null && items.length === 0 ? (
        <p className={styles.listNote}>Your questions will show here.</p>
      ) : null}

      {groups.map((group) => {
        const headingId = `${headingPrefix}-${group.key}`;
        return (
          <section key={group.key} className={styles.group} aria-labelledby={headingId}>
            <h2 id={headingId} className={styles.groupTitle}>
              {group.title}
            </h2>
            <ul className={styles.items}>
              {group.items.map((item) => (
                <li key={item.id}>
                  <button
                    type="button"
                    className={styles.item}
                    aria-current={item.id === activeId ? "true" : undefined}
                    disabled={disabled}
                    onClick={() => onOpen(item.id)}
                  >
                    <span className={styles.itemTitle}>{item.title}</span>
                    <time className={styles.itemTime} dateTime={item.created_at}>
                      {conversationTime(item.created_at, group.key)}
                    </time>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        );
      })}
    </nav>
  );
}
