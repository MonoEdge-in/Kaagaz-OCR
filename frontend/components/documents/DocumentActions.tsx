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
import type { FormEvent, KeyboardEvent, ReactNode, RefCallback } from "react";
import { createPortal } from "react-dom";
import { Icon } from "@/components/ui/Icon";
import { hiddenMovedText, moveEffectText } from "@/components/access/words";
import { createFolder, createTag, documentMovePreview, isHiddenMove, moveDocument, setDocumentTags, updateDocument } from "@/lib/api";
import type { DocumentSummary, MovePreview, Tag } from "@/lib/types";
import { ActionDialog } from "./ActionDialog";
import { BusyLabel } from "./BusyLabel";
import { NameChips } from "./FolderActions";
import { pathLabel } from "./folderTree";
import { FolderPicker } from "./FolderPicker";
import type { FolderTree } from "./folderTree";
import {
  ACTION_HELP,
  documentNameProblem,
  findTagByName,
  folderNameProblem,
  libraryErrorText,
  menuEntries,
  menuItemAfterKey,
  menuPlace,
  moveSuggestion,
  movedMessage,
  tagNameProblem,
} from "./library";
import type { EditAction, MenuAccess, MenuEntry, RowAction } from "./library";
import { documentsCount, runEach } from "./trash";
import styles from "./Library.module.css";

export interface RowMenuProps {
  /** A document; or a folder in Trash (TrashFolderRow), named "folder <name>", whose Restore and
   * Delete forever are a trashed document's (B16). */
  item: Pick<DocumentSummary, "id" | "display_name" | "status" | "deleted_at" | "can_delete">;
  canForever: boolean; // Delete forever (canDeleteForever, trash.ts)
  onPick(action: RowAction): void;
  /** The row keeps hold of the menu button, to hand focus back to it. */
  toggleRef: RefCallback<HTMLButtonElement>;
  /** The document screen's menu (DD2): Read again first when `readAgain`. */
  place?: "list" | "document";
  readAgain?: boolean;
  /** The document screen's other actions (24 Oct 2026: Ensemble, Export as Data, Download original),
   * after the menu's own and before Move to Trash, each with one plain line; disabled ones say why. */
  more?: readonly MoreItem[];
  /** PRD 7 (#604): Give access and Who can see this, for who may use them. */
  access?: MenuAccess;
}

export interface MoreItem {
  label: string;
  hint: string;
  onSelect(): void;
  disabledReason?: string | null;
}

const isDanger = (entry: MenuEntry) => entry.kind === "action" && entry.danger;

export function RowMenu({
  item,
  canForever,
  onPick,
  toggleRef,
  place = "list",
  readAgain = false,
  more = [],
  access,
}: RowMenuProps) {
  const [open, setOpen] = useState(false);
  const [at, setAt] = useState<{ top: number; left: number } | null>(null);
  const button = useRef<HTMLButtonElement | null>(null);
  const list = useRef<HTMLUListElement>(null);
  const menuId = useId();
  const entries = menuEntries(item, canForever, place, readAgain, access);

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

  if (entries.length === 0 && more.length === 0) return null;
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
            {entries.filter((entry) => !isDanger(entry)).map(entryItem)}
            {more.map((extra, index) => (
              <li key={extra.label} role="none">
                <button
                  role="menuitem"
                  tabIndex={-1}
                  type="button"
                  className={styles.menuItem}
                  aria-disabled={extra.disabledReason ? true : undefined}
                  aria-describedby={`${menuId}-more-${index}`}
                  onClick={() => {
                    if (extra.disabledReason) return;
                    close(true);
                    extra.onSelect();
                  }}
                >
                  <span className={styles.menuWords}>
                    {extra.label}
                    <span id={`${menuId}-more-${index}`} className={styles.menuHint}>
                      {extra.disabledReason ?? extra.hint}
                    </span>
                  </span>
                </button>
              </li>
            ))}
            {/* Vrushit UAT (25 Oct 2026): Move to Trash, the destructive one, always last. */}
            {entries.filter(isDanger).map(entryItem)}
          </ul>,
          window.document.body,
        )}
    </>
  );

  function entryItem(entry: MenuEntry) {
    return (
      <li key={entry.kind === "download" ? "download" : entry.action} role="none">
        {entry.kind === "download" ? (
          <a
            role="menuitem"
            tabIndex={-1}
            className={styles.menuItem}
            href={entry.href}
            download
            aria-label="Download original"
            aria-describedby={`${menuId}-download`}
            onClick={() => close(true)}
          >
            <span className={styles.menuWords}>
              Download original
              <span id={`${menuId}-download`} className={styles.menuHint}>
                {ACTION_HELP.download}
              </span>
            </span>
          </a>
        ) : (
          <button
            role="menuitem"
            tabIndex={-1}
            type="button"
            className={entry.danger ? `${styles.menuItem} ${styles.danger}` : styles.menuItem}
            aria-label={entry.words}
            aria-describedby={`${menuId}-${entry.action}`}
            onClick={() => pick(entry.action)}
          >
            {entry.danger && <Icon name="trash" />}
            {/* The words, then one plain line saying what it does (plain words, 25 Oct 2026). */}
            <span className={styles.menuWords}>
              {entry.words}
              <span id={`${menuId}-${entry.action}`} className={styles.menuHint}>
                {ACTION_HELP[entry.action]}
              </span>
            </span>
          </button>
        )}
      </li>
    );
  }
}

export interface EditDialogProps {
  action: EditAction;
  item: DocumentSummary;
  tree: FolderTree;
  tags: Tag[] | null;
  onClose(): void;
  /** Called once the dialog is shut and the page can take focus again. */
  onClosed(): void;
  /** Saved. moved: the document may have left the list being viewed; undo puts it back. */
  onSaved(message: string, moved?: boolean, undo?: () => Promise<void>): void;
  onTagCreated(): void;
  /** Names for a new folder in Move (folderNameSuggestions). */
  suggestions?: readonly string[];
  onFolderCreated?(): void;
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
  suggestions,
  onFolderCreated,
}: EditDialogProps) {
  const titles: Record<EditAction, string> = {
    rename: `Rename “${item.display_name}”`,
    move: `Move “${item.display_name}”`,
    tags: `Tags on “${item.display_name}”`,
  };
  return (
    <ActionDialog title={titles[action]} onClose={onClose} onClosed={onClosed}>
      {action === "rename" && <RenameForm item={item} onSaved={onSaved} onCancel={onClose} />}
      {action === "move" && (
        <MoveForm
          items={[item]}
          tree={tree}
          suggestions={suggestions}
          onSaved={onSaved}
          onFolderCreated={onFolderCreated}
          onCancel={onClose}
        />
      )}
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

// Move to folder (Tushar, 16 Oct 2026: files already here had no clear way into a folder):
// radio buttons, so arrows move between choices and a disabled one is skipped. All documents
// (no folder), then the folders as a tree that opens one folder at a time (FolderPicker, #484:
// thousands of folders, 100 levels), the way to where they are now open; where they are now is
// marked Current and can't be chosen. First, marked Suggested, the folder named for their kind of
// document, or a new folder of that name (moveSuggestion); last, New folder… with a name box and
// suggested names, which makes the folder at the top level and moves into it in one go. One
// document or many (the selection bar); one call each, the server checks each again.
const TOP = ""; // All documents: no folder
const NEW = "+new";
const SUGGESTED_NEW = "+suggested";

export interface MoveFormProps {
  items: readonly DocumentSummary[];
  tree: FolderTree;
  /** Names for a new folder (folderNameSuggestions). */
  suggestions?: readonly string[];
  /** Moved; undo puts each document back where it was (none when an admin moved one where they
   * can't see it: PR 10c's `{id, hidden: true}`). */
  onSaved(message: string, moved: boolean, undo?: () => Promise<void>): void;
  /** A folder was made here: the screen asks for the folders again. */
  onFolderCreated?(): void;
  onCancel(): void;
}

export function MoveForm({ items, tree, suggestions = [], onSaved, onFolderCreated, onCancel }: MoveFormProps) {
  const places = new Set(items.map((item) => item.folder_id ?? TOP));
  const current = places.size === 1 ? [...places][0] : null; // where they all are now
  const [suggested] = useState(() => moveSuggestion(items, tree)); // kept while the folders reload
  const suggestedId = suggested && "folderId" in suggested ? suggested.folderId : null;
  const suggestedName = suggested && "newName" in suggested ? suggested.newName : null;
  const [choice, setChoice] = useState<string | null>(
    suggestedId ?? (suggestedName !== null ? SUGGESTED_NEW : null),
  );
  const [newName, setNewName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const id = useId();
  // The suggested folder is tagged where it is in the tree and chosen at first (Tushar, 16 Oct
  // 2026: no second copy of it breaking the column); the way to it and to the current place starts open.
  const [openTo] = useState(() => [choice, current ?? null]);
  // Focus starts on the choice made, else on the first one that can be chosen.
  const focusValue = choice ?? (current === TOP ? (tree.roots[0]?.folder.id ?? NEW) : TOP);
  // PRD 7 PR 11b (#604, PR 10c): for one file, who would lose and gain it in the place chosen
  // (counts only for the person it is held for); nothing for a new folder, or when the person may
  // not make such a move (the move itself then says why).
  const [preview, setPreview] = useState<MovePreview | null>(null);
  const one = items.length === 1 ? (items[0]?.id ?? null) : null;
  const existing = choice !== null && choice !== NEW && choice !== SUGGESTED_NEW && choice !== current;
  useEffect(() => {
    if (one === null || !existing || choice === null) return;
    let live = true;
    documentMovePreview(one, choice || null).then(
      (reply) => live && setPreview(reply),
      () => live && setPreview(null),
    );
    return () => {
      live = false;
      setPreview(null);
    };
  }, [one, existing, choice]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (choice === null) {
      setError("Choose where to move to.");
      return;
    }
    const name = choice === SUGGESTED_NEW ? suggestedName : choice === NEW ? newName : null;
    const problem = name === null ? null : folderNameProblem(name);
    if (problem) {
      setError(problem);
      return;
    }
    setBusy(true);
    setError(null);
    let target = choice;
    let where = target === TOP ? "All documents" : pathLabel(tree, target);
    if (name !== null) {
      try {
        const created = await createFolder(name.trim(), null);
        onFolderCreated?.();
        target = created.id;
        where = created.name;
        setChoice(created.id); // trying again moves into it, and makes no second folder
      } catch (caught) {
        setError(libraryErrorText(caught, "folder"));
        setBusy(false);
        return;
      }
    }
    const before = new Map(items.map((item) => [item.id, item.folder_id]));
    const ids = items.filter((item) => (item.folder_id ?? TOP) !== target).map((item) => item.id);
    let hidden = 0; // PR 10c: an admin moved one where they can't see it
    const result = await runEach(ids, async (docId) => {
      if (isHiddenMove(await moveDocument(docId, target || null))) hidden += 1;
    });
    if (result.done.length === 0) {
      setError(libraryErrorText(result.firstError, "document"));
      setBusy(false);
      return;
    }
    const undo = async () => {
      const back = await runEach(result.done, (docId) => updateDocument(docId, { folder_id: before.get(docId) ?? null }));
      if (back.failed.length > 0) throw back.firstError;
    };
    const reason = result.failed.length > 0 ? libraryErrorText(result.firstError, "document") : "";
    const said = movedMessage(result.done.length, ids.length, where, reason);
    if (hidden > 0) onSaved(`${said} ${hiddenMovedText(hidden, result.done.length)}`, true);
    else onSaved(said, true, undo);
  }

  const option = (value: string, key: string, words: ReactNode) => {
    const here = value === current;
    return (
      <li key={key}>
        <label className={styles.check}>
          <input
            type="radio"
            name={`${id}-to`}
            value={value}
            checked={choice === value}
            disabled={here || busy}
            data-autofocus={value === focusValue ? true : undefined}
            onChange={() => {
              setChoice(value);
              setError(null);
            }}
          />
          {words}
          {here && <span className={styles.tag}>Current</span>}
        </label>
      </li>
    );
  };

  return (
    <form className={styles.dialogForm} onSubmit={submit} noValidate>
      <fieldset className={styles.fieldset} aria-describedby={error ? `${id}-error` : undefined}>
        <legend>Move to</legend>
        <ul className={styles.folderChoices}>
          {suggestedName !== null &&
            option(
              SUGGESTED_NEW,
              "suggested",
              <>
                New folder: {suggestedName} <span className={styles.tag}>Suggested</span>
              </>,
            )}
          {option(TOP, "top", "All documents (no folder)")}
          <FolderPicker
            tree={tree}
            name={`${id}-to`}
            choice={choice}
            onChoose={(value) => {
              setChoice(value);
              setError(null);
            }}
            disabled={(node) => node.folder.id === current}
            note={(node) =>
              node.folder.id === current ? (
                <span className={styles.tag}>Current</span>
              ) : node.folder.id === suggestedId ? (
                <>
                  {" "}
                  <span className={styles.tag}>Suggested</span>
                </>
              ) : null
            }
            openTo={openTo}
            busy={busy}
            focusId={focusValue}
          />
          {option(NEW, "new", "New folder…")}
        </ul>
      </fieldset>
      <p className="hint" role="status">
        {preview ? moveEffectText(preview) : ""}
      </p>
      {choice === NEW && (
        <div className="field">
          <label htmlFor={`${id}-name`}>Name of the new folder</label>
          <input
            id={`${id}-name`}
            className="input"
            value={newName}
            maxLength={100}
            autoComplete="off"
            placeholder="e.g. Invoices 2026"
            onChange={(event) => setNewName(event.target.value)}
          />
          <NameChips names={suggestions} picked={newName} onPick={setNewName} />
        </div>
      )}
      {error && (
        <p id={`${id}-error`} className="error-text" role="alert">
          {error}
        </p>
      )}
      <FormButtons busy={busy} words="Move" disabled={choice === null} onCancel={onCancel} />
    </form>
  );
}

export interface MoveDialogProps extends Omit<MoveFormProps, "onCancel"> {
  onClose(): void;
  onClosed?(): void;
}

/** The Move dialog for the documents ticked in the list. */
export function MoveDialog({ onClose, onClosed, ...form }: MoveDialogProps) {
  const title = form.items.length === 1 ? `Move “${form.items[0]?.display_name}”` : `Move ${documentsCount(form.items.length)}`;
  return (
    <ActionDialog title={title} onClose={onClose} onClosed={onClosed}>
      <MoveForm {...form} onCancel={onClose} />
    </ActionDialog>
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
