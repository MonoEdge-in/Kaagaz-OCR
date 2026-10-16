"use client";

// One document's actions, behind its menu button (menuEntries, library.ts). In a
// folder or All documents: Rename, Move to folder, Tags, Upload new version,
// Download original and, when the server says can_delete, Move to Trash. In
// Trash: Restore (can_delete), Download original and, in an admin session, Delete
// forever (PRD 1, F5). On the document screen: Read again first (DD2).
// UI audit D5 (16 Oct 2026): the button is an icon ("More actions for <name>"); the
// menu opens in a portal on <body>, fixed beside the button with a 1px edge, flipped
// above it near the window's bottom, so neither <main> nor the table cuts it. It is a
// menu: the first item takes focus, Up/Down/Home/End move, Esc or Tab closes it (Esc
// back to the button), and a press outside closes it and only that. Scrolling or a
// resize closes it, so it never floats away from its row. Rename, Move and Tags each
// open a small dialog with the error next to its field; Move to Trash and Delete
// forever keep their own "are you sure" step. Any user of the customer may rename,
// move and tag (PRD 1, F2 and F3); the server checks again.

import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import type { FormEvent, KeyboardEvent, RefCallback } from "react";
import { createPortal } from "react-dom";
import { Icon } from "@/components/ui/Icon";
import { createTag, setDocumentTags, updateDocument } from "@/lib/api";
import type { DocumentSummary, Tag } from "@/lib/types";
import { ActionDialog } from "./ActionDialog";
import { BusyLabel } from "./BusyLabel";
import { inTreeOrder, pathLabel } from "./folderTree";
import type { FolderTree } from "./folderTree";
import {
  documentNameProblem,
  findTagByName,
  libraryErrorText,
  menuEntries,
  menuItemAfterKey,
  menuPlace,
  tagNameProblem,
} from "./library";
import type { EditAction, RowAction } from "./library";
import styles from "./Library.module.css";

export interface RowMenuProps {
  item: DocumentSummary;
  canForever: boolean; // Delete forever (canDeleteForever, trash.ts)
  onPick(action: RowAction): void;
  /** The row keeps hold of the menu button, to hand focus back to it. */
  toggleRef: RefCallback<HTMLButtonElement>;
  /** The document screen's menu (DD2): Read again first when `readAgain`. */
  place?: "list" | "document";
  readAgain?: boolean;
}

export function RowMenu({ item, canForever, onPick, toggleRef, place = "list", readAgain = false }: RowMenuProps) {
  const [open, setOpen] = useState(false);
  const [at, setAt] = useState<{ top: number; left: number } | null>(null);
  const button = useRef<HTMLButtonElement | null>(null);
  const list = useRef<HTMLUListElement>(null);
  const menuId = useId();
  const entries = menuEntries(item, canForever, place, readAgain);

  const setButton = useCallback(
    (element: HTMLButtonElement | null) => {
      button.current = element;
      toggleRef(element);
    },
    [toggleRef],
  );

  function close(refocus: boolean) {
    setOpen(false);
    setAt(null);
    if (refocus) button.current?.focus();
  }

  // Placed once its size is known, before it paints (the re-render this asks for comes first
  // too); its first item takes focus.
  useLayoutEffect(() => {
    const menu = list.current;
    const opener = button.current;
    if (!open || !menu || !opener) return;
    const spot = menuPlace(opener.getBoundingClientRect(), menu.getBoundingClientRect(), {
      width: window.innerWidth,
      height: window.innerHeight,
    });
    setAt({ top: spot.top, left: spot.left });
    menu.querySelector<HTMLElement>("[role=menuitem]")?.focus();
  }, [open]);

  // A press anywhere outside closes the menu, and only that: its click is swallowed, so it
  // never lands on a link or button that lay under the open menu (MonoEdge misclick rule).
  // A scroll (of <main>, or anything but the menu) or a resize closes it too.
  useEffect(() => {
    if (!open) return;
    const shut = () => {
      setOpen(false);
      setAt(null);
    };
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target;
      if (target instanceof Node && (list.current?.contains(target) || button.current?.contains(target))) return;
      shut();
      const swallow = (click: MouseEvent) => {
        click.preventDefault();
        click.stopPropagation();
      };
      window.addEventListener("click", swallow, { capture: true, once: true });
      // A press that turns into a scroll has no click: then the next click is a real one.
      window.addEventListener("pointercancel", () => window.removeEventListener("click", swallow, true), { once: true });
    };
    const onScroll = (event: Event) => {
      if (!(event.target instanceof Node && list.current?.contains(event.target))) shut();
    };
    window.document.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("scroll", onScroll, { capture: true, passive: true });
    window.addEventListener("resize", shut, { passive: true });
    return () => {
      window.document.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", shut);
    };
  }, [open]);

  function onMenuKey(event: KeyboardEvent<HTMLUListElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      close(true);
      return;
    }
    if (event.key === "Tab") {
      close(true); // back on the button, so Tab goes on from there
      return;
    }
    const items = [...(list.current?.querySelectorAll<HTMLElement>("[role=menuitem]") ?? [])];
    const next = menuItemAfterKey(items.indexOf(event.target as HTMLElement), event.key, items.length);
    if (next === null) return;
    event.preventDefault();
    items[next]?.focus();
  }

  function pick(action: RowAction) {
    close(false);
    onPick(action);
  }

  if (entries.length === 0) return null;
  const name = `More actions for ${item.display_name}`;
  return (
    <>
      <button
        ref={setButton}
        type="button"
        className={`btn ${styles.menuButton}`}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        aria-label={name}
        title={name}
        onClick={() => (open ? close(false) : setOpen(true))}
        onKeyDown={(event) => {
          if (event.key !== "ArrowDown" || open) return;
          event.preventDefault();
          setOpen(true);
        }}
      >
        {/* Three square dots, drawn here: the icon set has no "more" icon yet. */}
        <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
          <rect x="2" y="7" width="2.5" height="2.5" fill="currentColor" />
          <rect x="6.75" y="7" width="2.5" height="2.5" fill="currentColor" />
          <rect x="11.5" y="7" width="2.5" height="2.5" fill="currentColor" />
        </svg>
      </button>
      {open &&
        createPortal(
          <ul
            ref={list}
            id={menuId}
            role="menu"
            className={`motion-pop ${styles.menuList}`}
            aria-label={`Actions for ${item.display_name}`}
            style={at ? { top: at.top, left: at.left } : { top: 0, left: 0 }}
            onKeyDown={onMenuKey}
          >
            {entries.map((entry) => (
              <li key={entry.kind === "download" ? "download" : entry.action} role="none">
                {entry.kind === "download" ? (
                  <a
                    role="menuitem"
                    tabIndex={-1}
                    className={styles.menuItem}
                    href={entry.href}
                    download
                    onClick={() => close(true)}
                  >
                    Download original
                  </a>
                ) : (
                  <button
                    role="menuitem"
                    tabIndex={-1}
                    type="button"
                    className={entry.danger ? `${styles.menuItem} ${styles.danger}` : styles.menuItem}
                    onClick={() => pick(entry.action)}
                  >
                    {entry.danger && <Icon name="trash" />}
                    {entry.words}
                  </button>
                )}
              </li>
            ))}
          </ul>,
          window.document.body,
        )}
    </>
  );
}

export interface EditDialogProps {
  action: EditAction;
  item: DocumentSummary;
  tree: FolderTree;
  tags: Tag[] | null;
  onClose(): void;
  /** Called once the dialog is shut and the page can take focus again. */
  onClosed(): void;
  /** Saved. moved: the document may have left the list being viewed. */
  onSaved(message: string, moved?: boolean): void;
  onTagCreated(): void;
}

export function EditDialog({
  action,
  item,
  tree,
  tags,
  onClose,
  onClosed,
  onSaved,
  onTagCreated,
}: EditDialogProps) {
  const titles: Record<EditAction, string> = {
    rename: `Rename “${item.display_name}”`,
    move: `Move “${item.display_name}”`,
    tags: `Tags on “${item.display_name}”`,
  };
  return (
    <ActionDialog title={titles[action]} onClose={onClose} onClosed={onClosed}>
      {action === "rename" && <RenameForm item={item} onSaved={onSaved} onCancel={onClose} />}
      {action === "move" && <MoveForm item={item} tree={tree} onSaved={onSaved} onCancel={onClose} />}
      {action === "tags" && (
        <TagsForm
          item={item}
          tags={tags}
          onSaved={onSaved}
          onCancel={onClose}
          onTagCreated={onTagCreated}
        />
      )}
    </ActionDialog>
  );
}

function FormButtons({
  busy,
  words,
  disabled = false,
  onCancel,
}: {
  busy: boolean;
  words: string;
  disabled?: boolean;
  onCancel(): void;
}) {
  // [Cancel] [Primary]: the primary last (Library.module.css .formButtons).
  return (
    <div className={styles.formButtons}>
      <button type="button" className="btn" onClick={onCancel} disabled={busy}>
        Cancel
      </button>
      <button type="submit" className="btn btn-primary" disabled={busy || disabled}>
        <BusyLabel busy={busy} idle={words} working={words} />
      </button>
    </div>
  );
}

interface FormProps {
  item: DocumentSummary;
  onSaved(message: string, moved?: boolean): void;
  onCancel(): void;
}

function RenameForm({ item, onSaved, onCancel }: FormProps) {
  const [name, setName] = useState(item.display_name);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const id = useId();

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const problem = documentNameProblem(name);
    if (problem) {
      setError(problem);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const saved = await updateDocument(item.id, { display_name: name.trim() });
      onSaved(`Renamed to ${saved.display_name}.`);
    } catch (caught) {
      setError(libraryErrorText(caught, "document"));
      setBusy(false);
    }
  }

  return (
    <form className={styles.dialogForm} onSubmit={submit} noValidate>
      <div className="field">
        <label htmlFor={`${id}-name`}>Name</label>
        <input
          id={`${id}-name`}
          data-autofocus
          className="input"
          value={name}
          maxLength={255}
          autoComplete="off"
          onChange={(event) => setName(event.target.value)}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? `${id}-error` : undefined}
        />
        {error && (
          <p id={`${id}-error`} className="error-text" role="alert">
            {error}
          </p>
        )}
      </div>
      <FormButtons
        busy={busy}
        words="Save name"
        disabled={name.trim() === item.display_name}
        onCancel={onCancel}
      />
    </form>
  );
}

function MoveForm({ item, tree, onSaved, onCancel }: FormProps & { tree: FolderTree }) {
  const [target, setTarget] = useState(item.folder_id ?? ""); // "" is the top level
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const id = useId();
  const folders = inTreeOrder(tree);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await updateDocument(item.id, { folder_id: target || null });
      const where = target ? pathLabel(tree, target) : "the top level";
      onSaved(`Moved ${item.display_name} to ${where}.`, true);
    } catch (caught) {
      setError(libraryErrorText(caught, "document"));
      setBusy(false);
    }
  }

  return (
    <form className={styles.dialogForm} onSubmit={submit} noValidate>
      <div className="field">
        <label htmlFor={`${id}-folder`}>Folder</label>
        <select
          id={`${id}-folder`}
          data-autofocus
          className="input"
          value={target}
          onChange={(event) => setTarget(event.target.value)}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? `${id}-error` : undefined}
        >
          <option value="">Top level (no folder)</option>
          {folders.map((node) => (
            <option key={node.folder.id} value={node.folder.id}>
              {pathLabel(tree, node.folder.id)}
            </option>
          ))}
        </select>
        {folders.length === 0 && (
          <p className="hint">No folders yet. Make one with New folder, above the list.</p>
        )}
        {error && (
          <p id={`${id}-error`} className="error-text" role="alert">
            {error}
          </p>
        )}
      </div>
      <FormButtons
        busy={busy}
        words="Move"
        disabled={target === (item.folder_id ?? "")}
        onCancel={onCancel}
      />
    </form>
  );
}

const tagOrder = new Intl.Collator("en-IN", { sensitivity: "base", numeric: true });

function TagsForm({
  item,
  tags,
  onSaved,
  onCancel,
  onTagCreated,
}: FormProps & { tags: Tag[] | null; onTagCreated(): void }) {
  // Every tag of the customer, plus any made here; the document's own are
  // added in case the list could not be loaded.
  const [known, setKnown] = useState<Tag[]>(() => {
    const all = [...(tags ?? [])];
    for (const tag of item.tags) if (!all.some((other) => other.id === tag.id)) all.push(tag);
    return all.sort((a, b) => tagOrder.compare(a.name, b.name));
  });
  const [chosen, setChosen] = useState<Set<string>>(() => new Set(item.tags.map((tag) => tag.id)));
  const [newName, setNewName] = useState("");
  const [newError, setNewError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const id = useId();

  function toggle(tagId: string) {
    setChosen((current) => {
      const next = new Set(current);
      if (next.has(tagId)) next.delete(tagId);
      else next.add(tagId);
      return next;
    });
  }

  function tick(tag: Tag) {
    setChosen((current) => new Set(current).add(tag.id));
    setNewName("");
    setNewError(null);
    setStatus(`${tag.name} is ticked.`);
  }

  async function addTag() {
    const problem = tagNameProblem(newName);
    if (problem) {
      setNewError(problem);
      return;
    }
    const existing = findTagByName(known, newName);
    if (existing) {
      tick(existing);
      return;
    }
    setAdding(true);
    setNewError(null);
    try {
      const tag = await createTag(newName.trim());
      setKnown((current) => [...current, tag].sort((a, b) => tagOrder.compare(a.name, b.name)));
      tick(tag);
      onTagCreated();
    } catch (caught) {
      setNewError(libraryErrorText(caught, "tag"));
    } finally {
      setAdding(false);
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    // A name typed but not added would be lost without a word: say so instead.
    if (newName.trim()) {
      setNewError("Press Add tag to add this tag, or clear the box.");
      document.getElementById(`${id}-new`)?.focus();
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await setDocumentTags(item.id, [...chosen]);
      onSaved(`Saved the tags on ${item.display_name}.`);
    } catch (caught) {
      setError(libraryErrorText(caught, "tag"));
      setBusy(false);
    }
  }

  return (
    <form className={styles.dialogForm} onSubmit={submit} noValidate>
      <fieldset className={styles.fieldset}>
        <legend>Tick the tags this document has</legend>
        {known.length === 0 ? (
          <p className="muted">No tags yet. Add the first one below.</p>
        ) : (
          <ul className={styles.choices}>
            {known.map((tag, index) => (
              <li key={tag.id}>
                <label className={styles.check}>
                  <input
                    type="checkbox"
                    data-autofocus={index === 0 ? true : undefined}
                    checked={chosen.has(tag.id)}
                    onChange={() => toggle(tag.id)}
                  />
                  {tag.name}
                </label>
              </li>
            ))}
          </ul>
        )}
      </fieldset>
      <div className="field">
        <label htmlFor={`${id}-new`}>New tag</label>
        <div className={styles.addTag}>
          <input
            id={`${id}-new`}
            data-autofocus={known.length === 0 ? true : undefined}
            className="input"
            value={newName}
            maxLength={40}
            autoComplete="off"
            onChange={(event) => setNewName(event.target.value)}
            onKeyDown={(event) => {
              // Enter here adds the tag; it does not save the form. While a Hindi or
              // Marathi keyboard is still forming the word, Enter is the keyboard's.
              if (event.key === "Enter" && !event.nativeEvent.isComposing) {
                event.preventDefault();
                void addTag();
              }
            }}
            aria-invalid={newError ? true : undefined}
            aria-describedby={newError ? `${id}-new-error` : undefined}
          />
          <button type="button" className="btn" onClick={() => void addTag()} disabled={adding}>
            <Icon name="plus" />
            Add tag
          </button>
        </div>
        {newError && (
          <p id={`${id}-new-error`} className="error-text" role="alert">
            {newError}
          </p>
        )}
        <p className="visually-hidden" aria-live="polite">
          {status}
        </p>
      </div>
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      <FormButtons busy={busy} words="Save tags" onCancel={onCancel} />
    </form>
  );
}
