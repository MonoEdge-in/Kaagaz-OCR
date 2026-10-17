"use client";

// This person's past conversations, grouped Today / Yesterday / Earlier by the
// India day each was started, and a button to start a new one. The same list
// sits in the side pane on wide screens and in the drawer below 1024 px.
// UI audit A1 (16 Oct 2026): a search over the titles, and each conversation's
// menu: Rename (in place; Enter saves, Esc cancels) and Delete (asked first).

import { useCallback, useId, useRef, useState } from "react";
import type { FormEvent } from "react";
import { RowMenu } from "@/components/admin/RowMenu";
import { ActionDialog } from "@/components/documents/ActionDialog";
import { Icon } from "@/components/ui/Icon";
import { IconButton } from "@/components/ui/IconButton";
import type { ConversationSummary } from "@/lib/types";
import { useMinuteNow } from "./hooks";
import { conversationTime, filterConversations, groupConversations } from "./logic";
import styles from "./Chat.module.css";

const TITLE_MAX = 200;

export function ConversationList({
  items,
  failed,
  activeId,
  disabled,
  onOpen,
  onNew,
  onRetry,
  onRename,
  onDelete,
}: {
  items: ConversationSummary[] | null;
  failed: boolean;
  activeId: string | null;
  disabled: boolean;
  onOpen: (id: string) => void;
  onNew: () => void;
  onRetry: () => void;
  /** Each says whether it worked; the screen says what happened. */
  onRename: (id: string, title: string) => Promise<boolean>;
  onDelete: (id: string) => Promise<boolean>;
}) {
  const now = useMinuteNow();
  const prefix = useId();
  const [search, setSearch] = useState("");
  const searchBox = useRef<HTMLInputElement>(null);
  const [renaming, setRenaming] = useState<{ id: string; title: string } | null>(null);
  const [deleting, setDeleting] = useState<ConversationSummary | null>(null);
  const [busy, setBusy] = useState(false);
  const shown = items ? filterConversations(items, search) : [];
  const groups = groupConversations(shown, now);
  // The name box takes focus when Rename opens it, with its words selected.
  const focusName = useCallback((input: HTMLInputElement | null) => input?.select(), []);

  const saveTitle = async (event: FormEvent) => {
    event.preventDefault();
    if (!renaming || busy) return;
    const title = renaming.title.trim();
    if (!title) return;
    setBusy(true);
    if (await onRename(renaming.id, title)) setRenaming(null);
    setBusy(false);
  };

  return (
    <nav className={styles.conversations} aria-label="Conversations">
      {/* Secondary: Send in the question box is this screen's one primary. The
          list can go stale (another tab, another device), and there is no
          pull to refresh, so Refresh is always there (aria-label and tooltip). */}
      <div className={styles.listActions}>
        <button type="button" className="btn" disabled={disabled} onClick={onNew}>
          <Icon name="plus" />
          New conversation
        </button>
        <IconButton icon="refresh" label="Refresh conversations" className="btn-quiet" onClick={onRetry} />
      </div>

      {items !== null && items.length > 0 ? (
        <div className={styles.listSearch}>
          <label htmlFor={`${prefix}-search`}>Search conversations</label>
          {/* The Documents search's pattern: a lens inside the box, an x to clear it. */}
          <div className={styles.searchField}>
            <Icon name="search" size={16} className={styles.searchIcon} />
            <input
              id={`${prefix}-search`}
              ref={searchBox}
              type="search"
              className={`input ${styles.searchInput}`}
              autoComplete="off"
              placeholder="Search conversations"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
            {search !== "" && (
              <IconButton icon="close" label="Clear search" className={styles.searchClear} onClick={() => {
                  setSearch("");
                  searchBox.current?.focus();
                }}
              />
            )}
          </div>
        </div>
      ) : null}

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
      {items !== null && items.length > 0 && shown.length === 0 ? (
        <p className={styles.listNote} role="status">
          No conversation has these words in its name.
        </p>
      ) : null}

      {groups.map((group) => {
        const headingId = `${prefix}-${group.key}`;
        return (
          <section key={group.key} className={styles.group} aria-labelledby={headingId}>
            <h2 id={headingId} className={styles.groupTitle}>
              {group.title}
            </h2>
            <ul className={styles.items}>
              {group.items.map((item) =>
                renaming?.id === item.id ? (
                  <li key={item.id}>
                    <form className={styles.renameForm} onSubmit={(event) => void saveTitle(event)}>
                      <label htmlFor={`${prefix}-rename`} className="visually-hidden">
                        New name for {item.title}
                      </label>
                      <input
                        ref={focusName}
                        id={`${prefix}-rename`}
                        className="input"
                        autoComplete="off"
                        maxLength={TITLE_MAX}
                        value={renaming.title}
                        onChange={(event) => setRenaming({ id: item.id, title: event.target.value })}
                        onKeyDown={(event) => {
                          if (event.key === "Escape") setRenaming(null);
                        }}
                      />
                      <div className={styles.renameButtons}>
                        <button type="button" className="btn" onClick={() => setRenaming(null)}>
                          Cancel
                        </button>
                        <button
                          type="submit"
                          className="btn btn-primary"
                          disabled={busy || renaming.title.trim() === ""}
                          aria-busy={busy || undefined}
                        >
                          Save
                        </button>
                      </div>
                    </form>
                  </li>
                ) : (
                  <li key={item.id} className={styles.itemRow}>
                    <button
                      type="button"
                      className={styles.item}
                      aria-current={item.id === activeId ? "true" : undefined}
                      disabled={disabled}
                      onClick={() => onOpen(item.id)}
                    >
                      <span className={styles.itemTitle} title={item.title}>
                        {item.title}
                      </span>
                      <time className={styles.itemTime} dateTime={item.created_at}>
                        {conversationTime(item.created_at, group.key)}
                      </time>
                    </button>
                    <RowMenu
                      compact
                      name={item.title}
                      items={[
                        { label: "Rename", onSelect: () => setRenaming({ id: item.id, title: item.title }) },
                        { label: "Delete", onSelect: () => setDeleting(item), destructive: true },
                      ]}
                    />
                  </li>
                ),
              )}
            </ul>
          </section>
        );
      })}

      {deleting ? (
        <ActionDialog title="Delete this conversation?" onClose={() => setDeleting(null)} canClose={() => !busy}>
          <p>
            &ldquo;{deleting.title}&rdquo; and its questions and answers are removed for good. Your documents stay as
            they are.
          </p>
          {/* [Cancel] [Delete]: the action last; Cancel has focus, so Enter never deletes by accident. */}
          <div className="actions">
            <button type="button" className="btn" data-autofocus disabled={busy} onClick={() => setDeleting(null)}>
              Cancel
            </button>
            <button
              type="button"
              className="btn btn-danger"
              disabled={busy}
              aria-busy={busy || undefined}
              onClick={async () => {
                setBusy(true);
                if (await onDelete(deleting.id)) setDeleting(null);
                setBusy(false);
              }}
            >
              Delete
            </button>
          </div>
        </ActionDialog>
      ) : null}
    </nav>
  );
}
