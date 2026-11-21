"use client";

// Link as versions (#648, V13 to V15): three steps, plain words, large targets.
// 1, Pick the files: find them by name, each with its folder, date and pages (no page picture: each
// would be logged as a view, review of #659); the list
// holds only files the person can see, out of Trash, and a locked file is never offered. A file of
// a family already chosen is left out (it comes with its family).
// 2, Put them in order: oldest first, by the document's own date, else the upload; drag a file, or
// use its arrows (the keyboard's and a phone's way), and "This is the latest" picks the one Ask
// answers from (PRD 9 F-07: it starts on the newest upload, then moves with the last row after a
// reorder, until the person picks one).
// 3, Check and link: the server's preview says which file Ask will answer from, where the files
// will be kept and who would lose access (V15). An Admin whose link opens a file to themselves
// signs in again with the company account first (403 keycloak_reauth_required), as a folder move asks.
// Linked, the caller says so in its toast, with Undo (V16).

import { useEffect, useId, useMemo, useState } from "react";
import type { DragEvent, FormEvent, ReactNode } from "react";
import { ActionDialog } from "@/components/documents/ActionDialog";
import library from "@/components/documents/Library.module.css";
import { buildTree, pathLabel } from "@/components/documents/folderTree";
import type { FolderTree } from "@/components/documents/folderTree";
import { Icon as KIcon } from "@/components/ui/Icon/Icon";
import { withProof } from "@/components/access/proofStep";
import { listDocuments, listFolders, linkVersions, previewLinkVersions } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { DocumentSummary, LinkPreview } from "@/lib/types";
import {
  LINK_TEXT,
  MAX_LINK,
  MIN_LINK,
  byDate,
  confirmLines,
  fromSummary,
  linkBody,
  linkErrorText,
  moved,
  movedTo,
  newestUpload,
  undoLink,
} from "./link";
import type { LinkFile } from "./link";
import styles from "./Link.module.css";

/** What a link did: the toast's words and its Undo. */
export interface Linked {
  message: string;
  undo: () => Promise<unknown>;
}

type Step = 1 | 2 | 3;

export function LinkDialog({
  files,
  start,
  fixed,
  latest: startLatest,
  onClose,
  onLinked,
}: {
  /** The files chosen to start; for "order", already in their order. */
  files: readonly LinkFile[];
  start: "pick" | "order";
  /** A file that can't be taken off the list (the Versions tab's own document). */
  fixed?: string;
  /** The latest to start with; else the newest. */
  latest?: string | null;
  onClose(): void;
  onLinked(linked: Linked): void;
}) {
  const [step, setStep] = useState<Step>(start === "pick" ? 1 : 2);
  const [chosen, setChosen] = useState<LinkFile[]>(() => [...files]);
  const [order, setOrder] = useState<string[]>(() => files.map((file) => file.id));
  const [picked, setPicked] = useState<string | null>(startLatest ?? null);
  const [reordered, setReordered] = useState(false);
  const latest = picked ?? (reordered ? order.at(-1) : newestUpload(chosen)?.id) ?? null;
  const [preview, setPreview] = useState<LinkPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const body = linkBody(order, latest);

  function toOrder() {
    setOrder(byDate(chosen).map((file) => file.id));
    setPicked(null);
    setReordered(false);
    setError(null);
    setStep(2);
  }

  async function toConfirm() {
    if (!body) return;
    setBusy(true);
    setError(null);
    try {
      setPreview(await previewLinkVersions(body));
      setStep(3);
    } catch (caught) {
      setError(linkErrorText(caught));
    } finally {
      setBusy(false);
    }
  }

  async function link() {
    if (!body || !preview) return;
    setBusy(true);
    setError(null);
    try {
      const done = await withProof(() => linkVersions(body));
      if (done === null) return; // the company sign-in was started: nothing was linked
      const current = body.current_id;
      onLinked({
        message: LINK_TEXT.linked(body.document_ids.length, preview.latest.name),
        undo: () => undoLink(current),
      });
    } catch (caught) {
      setError(linkErrorText(caught));
      setBusy(false);
    }
  }

  /** Steps 2 and 3 are a form: Enter goes on, as the primary button does. */
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (step === 2) void toConfirm();
    else if (step === 3) void link();
  }

  const back = (to: Step) => {
    setError(null);
    setStep(to);
  };

  return (
    <ActionDialog title={LINK_TEXT.title} onClose={onClose} canClose={() => !busy} wide={step !== 3}>
      <Body form={step !== 1} onSubmit={submit}>
        {/* Each step's line takes the focus as the step changes (MoveTwoStepDialog's way). */}
        <p key={step} ref={focusOnMount} tabIndex={-1} className={styles.stepLine} aria-live="polite">
          <span className="muted">{LINK_TEXT.step(step)}</span> <strong>{LINK_TEXT.steps[step - 1]}</strong>
        </p>
        {step === 1 && <PickStep chosen={chosen} fixed={fixed} onChosen={setChosen} />}
        {step === 2 && (
          <OrderStep
            files={chosen}
            order={order}
            latest={latest}
            onOrder={(next) => {
              setOrder(next);
              setReordered(true);
            }}
            onLatest={setPicked}
          />
        )}
        {step === 3 && preview && (
          <ConfirmStep preview={preview} files={order.length} />
        )}
        {error && (
          <p className="error-text" role="alert">
            {error}
          </p>
        )}
        <div className={library.formButtons}>
          {step === 1 || (step === 2 && start === "order") ? (
            <button type="button" className="btn" onClick={onClose} disabled={busy}>
              {LINK_TEXT.cancel}
            </button>
          ) : (
            <button type="button" className="btn" onClick={() => back(step === 3 ? 2 : 1)} disabled={busy}>
              {LINK_TEXT.back}
            </button>
          )}
          {step === 1 && (
            <button
              type="button"
              className="btn btn-primary"
              onClick={toOrder}
              disabled={chosen.length < MIN_LINK || chosen.length > MAX_LINK}
            >
              {LINK_TEXT.next}
            </button>
          )}
          {step === 2 && (
            <button type="submit" className="btn btn-primary" disabled={busy || !body}>
              {busy ? LINK_TEXT.checking : LINK_TEXT.next}
            </button>
          )}
          {step === 3 && (
            <button type="submit" className="btn btn-primary" disabled={busy}>
              {busy ? LINK_TEXT.linking : LINK_TEXT.link}
            </button>
          )}
        </div>
      </Body>
    </ActionDialog>
  );
}

const focusOnMount = (element: HTMLElement | null) => element?.focus();

/** The dialog's body: a form for steps 2 and 3; step 1 holds its own search form, so a div. */
function Body({
  form,
  onSubmit,
  children,
}: {
  form: boolean;
  onSubmit(event: FormEvent<HTMLFormElement>): void;
  children: ReactNode;
}) {
  return form ? (
    <form className={library.dialogForm} onSubmit={onSubmit}>
      {children}
    </form>
  ) : (
    <div className={library.dialogForm}>{children}</div>
  );
}

/** Step 1: find files by name and choose 2 to 20. */
function PickStep({
  chosen,
  fixed,
  onChosen,
}: {
  chosen: readonly LinkFile[];
  fixed?: string;
  onChosen(files: LinkFile[]): void;
}) {
  const id = useId();
  const [q, setQ] = useState("");
  const [found, setFound] = useState<DocumentSummary[] | null>(null);
  const [finding, setFinding] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tree, setTree] = useState<FolderTree | null>(null);

  useEffect(() => {
    let cancelled = false;
    listFolders().then(
      (reply) => {
        if (!cancelled) setTree(buildTree(reply.items));
      },
      () => undefined, // the folders are a hint only: the files still show
    );
    return () => {
      cancelled = true;
    };
  }, []);

  async function find(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFinding(true);
    setError(null);
    try {
      setFound((await listDocuments({ q: q.trim(), limit: 20 })).items);
    } catch (caught) {
      setError(linkErrorText(caught));
    } finally {
      setFinding(false);
    }
  }

  const families = new Set(chosen.map((file) => file.familyId ?? file.id));
  const offered = (found ?? []).filter((item) => item.locked !== true && !families.has(item.family_id));
  const full = chosen.length >= MAX_LINK;
  const facts = (file: LinkFile) =>
    [
      file.folderId && tree?.byId.has(file.folderId) ? pathLabel(tree, file.folderId) : null,
      formatDate(file.date),
      file.pages ? `${file.pages} ${file.pages === 1 ? "page" : "pages"}` : null,
    ]
      .filter(Boolean)
      .join(" · ");

  return (
    <>
      <p className="muted">{LINK_TEXT.pickHint}</p>
      <form className={styles.find} role="search" onSubmit={(event) => void find(event)}>
        <label htmlFor={id}>{LINK_TEXT.findLabel}</label>
        <div className={styles.findRow}>
          <input
            id={id}
            className="input"
            type="search"
            autoComplete="off"
            placeholder={LINK_TEXT.findPlaceholder}
            value={q}
            onChange={(event) => setQ(event.target.value)}
            data-autofocus
          />
          <button type="submit" className="btn" disabled={finding}>
            {finding ? LINK_TEXT.finding : LINK_TEXT.find}
          </button>
        </div>
      </form>
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {found !== null && offered.length === 0 && <p className="muted">{LINK_TEXT.noneFound}</p>}
      {offered.length > 0 && (
        <ul className={styles.fileList} aria-label="Files found">
          {offered.map((item) => {
            const file = fromSummary(item);
            return (
              <li key={item.id} className={styles.fileRow}>
                <span className={styles.fileWords}>
                  <span className={styles.fileName}>{file.name}</span>
                  <span className={styles.fileFacts}>{facts(file)}</span>
                </span>
                <button
                  type="button"
                  className="btn"
                  disabled={full}
                  onClick={() => onChosen([...chosen, file])}
                  aria-label={`${LINK_TEXT.add} ${file.name}`}
                >
                  {LINK_TEXT.add}
                </button>
              </li>
            );
          })}
        </ul>
      )}
      <h3 className={styles.subHeading}>{LINK_TEXT.chosen(chosen.length)}</h3>
      <ul className={styles.fileList} aria-label={LINK_TEXT.chosen(chosen.length)}>
        {chosen.map((file) => (
          <li key={file.id} className={styles.fileRow}>
            <span className={styles.fileWords}>
              <span className={styles.fileName}>{file.name}</span>
              <span className={styles.fileFacts}>{facts(file)}</span>
            </span>
            {file.id !== fixed && (
              <button
                type="button"
                className="btn btn-quiet"
                onClick={() => onChosen(chosen.filter((each) => each.id !== file.id))}
                aria-label={`${LINK_TEXT.remove} ${file.name}`}
              >
                {LINK_TEXT.remove}
              </button>
            )}
          </li>
        ))}
      </ul>
    </>
  );
}

const DRAG = "application/x-kaagaz-version";

/** Step 2: the order, oldest first, and the latest. Exported for the render test. */
export function OrderStep({
  files,
  order,
  latest,
  onOrder,
  onLatest,
}: {
  files: readonly LinkFile[];
  order: readonly string[];
  latest: string | null;
  onOrder(order: string[]): void;
  onLatest(id: string): void;
}) {
  const radio = useId();
  const byId = useMemo(() => new Map(files.map((file) => [file.id, file])), [files]);
  const [over, setOver] = useState<string | null>(null);

  function drop(event: DragEvent<HTMLLIElement>, onto: string) {
    event.preventDefault();
    setOver(null);
    onOrder(movedTo(order, event.dataTransfer.getData(DRAG), onto));
  }

  return (
    <>
      <p className="muted">
        {LINK_TEXT.orderHint} {LINK_TEXT.latestHint}
      </p>
      <ol className={styles.order} aria-label={LINK_TEXT.steps[1]}>
        {order.map((id, at) => {
          const file = byId.get(id);
          if (!file) return null;
          return (
            <li
              key={id}
              className={styles.orderRow}
              draggable
              data-over={over === id ? "true" : undefined}
              onDragStart={(event) => {
                event.dataTransfer.setData(DRAG, id);
                event.dataTransfer.effectAllowed = "move";
              }}
              onDragOver={(event) => {
                if (!event.dataTransfer.types.includes(DRAG)) return;
                event.preventDefault();
                setOver(id);
              }}
              onDragLeave={() => setOver(null)}
              onDrop={(event) => drop(event, id)}
            >
              <span className={styles.orderNo} aria-hidden="true">
                {at + 1}
              </span>
              <span className={styles.fileWords}>
                <span className={styles.fileName}>{file.name}</span>
                <span className={styles.fileFacts}>{formatDate(file.date)}</span>
              </span>
              <label className={styles.latest}>
                <input
                  type="radio"
                  name={radio}
                  value={id}
                  checked={latest === id}
                  onChange={() => onLatest(id)}
                  aria-label={`${LINK_TEXT.latest}: ${file.name}`}
                />
                <span aria-hidden="true">{LINK_TEXT.latest}</span>
              </label>
              <span className={styles.arrows}>
                <button
                  type="button"
                  className="btn btn-quiet"
                  aria-label={`${LINK_TEXT.moveUp}: ${file.name}`}
                  title={LINK_TEXT.moveUp}
                  disabled={at === 0}
                  onClick={() => onOrder(moved(order, id, -1))}
                >
                  <KIcon name="chev-down" size="sm" className={styles.up} />
                </button>
                <button
                  type="button"
                  className="btn btn-quiet"
                  aria-label={`${LINK_TEXT.moveDown}: ${file.name}`}
                  title={LINK_TEXT.moveDown}
                  disabled={at === order.length - 1}
                  onClick={() => onOrder(moved(order, id, 1))}
                >
                  <KIcon name="chev-down" size="sm" />
                </button>
              </span>
            </li>
          );
        })}
      </ol>
    </>
  );
}

/** Step 3: the plain summary. Exported for the render test. */
export function ConfirmStep({
  preview,
  files,
}: {
  preview: LinkPreview;
  files: number;
}) {
  return (
    <>
      <ul className={styles.summary}>
        {confirmLines(preview, files).map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
    </>
  );
}
