"use client";

// The admin Departments tab (File management F1): the departments in their order, each
// with Rename and Move up / Move down, and a form to add one. A department is a top-level
// folder of kind department; anyone can still make their own folders anywhere. Every name
// is React text. The server refuses the change to anyone but an admin (403).
// Issue 376: one error at a time, in the word "department". An add that failed says why under
// its own form, on a line of its own, so the Add button never moves; typing clears it.

import { useEffect, useId, useState } from "react";
import type { FormEvent } from "react";
import { departmentNameProblem, libraryErrorText } from "@/components/documents/library";
import { departmentsOf, nextSortOrder, reorder } from "@/components/files/departments";
import fileStyles from "@/components/files/Files.module.css";
import { FILES_TEXT } from "@/components/files/words";
import { Icon } from "@/components/ui/Icon";
import { IconButton } from "@/components/ui/IconButton";
import { ApiError, createFolder, listFolders, updateFolder } from "@/lib/api";
import { errorWords } from "@/lib/errorWords";
import type { Folder } from "@/lib/types";
import styles from "./Admin.module.css";

export function DepartmentsPanel({ initial }: { initial?: Folder[] }) {
  const [folders, setFolders] = useState<Folder[] | null>(initial ?? null);
  const [error, setError] = useState<string | null>(null);
  const [addError, setAddError] = useState<string | null>(null); // said under the add form
  const [said, setSaid] = useState("");
  const [busy, setBusy] = useState(false);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (initial) return;
    let cancelled = false;
    listFolders().then(
      (reply) => {
        if (!cancelled) setFolders(reply.items);
      },
      (caught: unknown) => {
        if (!cancelled) setError(caught instanceof ApiError ? errorWords(caught) : "The departments did not load.");
      },
    );
    return () => {
      cancelled = true;
    };
  }, [attempt, initial]);

  const departments = departmentsOf(folders ?? []);

  async function run(work: () => Promise<unknown>, done: string, fail: (text: string) => void = setError) {
    setBusy(true);
    setError(null);
    setAddError(null);
    try {
      await work();
      setSaid(done);
      setAttempt((n) => n + 1);
    } catch (caught) {
      fail(libraryErrorText(caught, "department"));
    } finally {
      setBusy(false);
    }
  }

  function move(id: string, step: -1 | 1) {
    const changes = reorder(departments, id, step);
    if (changes.length === 0) return;
    void run(async () => {
      for (const change of changes) await updateFolder(change.id, { sort_order: change.sort_order });
    }, "Order saved.");
  }

  return (
    <section className={styles.section} aria-labelledby="departments-heading">
      <h2 id="departments-heading" className={styles.sectionHeading}>
        {FILES_TEXT.adminTab}
      </h2>
      <p className="muted">{FILES_TEXT.adminIntro}</p>
      <p className="visually-hidden" aria-live="polite">
        {said}
      </p>
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {folders === null && error === null && (
        <p className="muted" role="status">
          Loading the departments…
        </p>
      )}
      {folders !== null && departments.length === 0 && <p className="muted">{FILES_TEXT.noDepartments}</p>}
      {departments.length > 0 && (
        <ol className={fileStyles.departments}>
          {departments.map((department, index) => (
            <li key={department.id} className={fileStyles.department}>
              {renaming === department.id ? (
                <NameForm
                  start={department.name}
                  submitLabel={FILES_TEXT.save}
                  busy={busy}
                  onCancel={() => setRenaming(null)}
                  onSubmit={(name) => {
                    setRenaming(null);
                    void run(() => updateFolder(department.id, { name }), `Renamed to ${name}.`);
                  }}
                />
              ) : (
                <>
                  <span className={fileStyles.departmentName}>{department.name}</span>
                  <button type="button" className="btn btn-quiet" onClick={() => setRenaming(department.id)} disabled={busy}>
                    {FILES_TEXT.rename}
                    <span className="visually-hidden">{` ${department.name}`}</span>
                  </button>
                  <IconButton
                    icon="chevron-left"
                    className={styles.upButton}
                    label={`${FILES_TEXT.moveUp}: ${department.name}`}
                    disabled={busy || index === 0}
                    onClick={() => move(department.id, -1)}
                  />
                  <IconButton
                    icon="chevron-right"
                    className={styles.downButton}
                    label={`${FILES_TEXT.moveDown}: ${department.name}`}
                    disabled={busy || index === departments.length - 1}
                    onClick={() => move(department.id, 1)}
                  />
                </>
              )}
            </li>
          ))}
        </ol>
      )}
      {folders !== null && (
        <NameForm
          start=""
          label={FILES_TEXT.newDepartment}
          submitLabel={FILES_TEXT.addDepartment}
          busy={busy}
          error={addError}
          onEdit={() => setAddError(null)}
          onSubmit={(name) =>
            void run(
              () => createFolder(name, null, { sort_order: nextSortOrder(departments) }),
              `${name} added.`,
              setAddError,
            )
          }
        />
      )}
      {error && folders === null && (
        <button type="button" className="btn" onClick={() => setAttempt((n) => n + 1)}>
          <Icon name="refresh" />
          Try again
        </button>
      )}
    </section>
  );
}

/** A department's name, to add or rename one. `error`: the server's refusal of the last add,
 * shown in the same one place as a name's own problem, which takes its place. */
export function NameForm({
  start,
  label = FILES_TEXT.nameLabel,
  submitLabel,
  busy,
  error = null,
  onEdit,
  onSubmit,
  onCancel,
}: {
  start: string;
  label?: string;
  submitLabel: string;
  busy: boolean;
  error?: string | null;
  onEdit?(): void;
  onSubmit(name: string): void;
  onCancel?(): void;
}) {
  const id = useId();
  const [name, setName] = useState(start);
  const [problem, setProblem] = useState<string | null>(null);
  const shown = problem ?? error;
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const found = departmentNameProblem(name);
    setProblem(found);
    onEdit?.(); // the last refusal goes: one error at a time
    if (found) return;
    onSubmit(name.trim());
    if (!onCancel) setName("");
  }
  return (
    <form className={fileStyles.departmentForm} onSubmit={submit} noValidate>
      <div className="field">
        <label htmlFor={id}>{label}</label>
        <input
          id={id}
          className="input"
          value={name}
          maxLength={100}
          autoComplete="off"
          aria-invalid={shown ? true : undefined}
          aria-describedby={shown ? `${id}-error` : undefined}
          onChange={(event) => {
            setName(event.target.value);
            setProblem(null);
            onEdit?.();
          }}
        />
      </div>
      {onCancel && (
        <button type="button" className="btn" onClick={onCancel}>
          {FILES_TEXT.cancel}
        </button>
      )}
      <button type="submit" className="btn" disabled={busy}>
        {submitLabel}
      </button>
      {/* A line of its own under the field and buttons, so the button stays where it is. */}
      {shown && (
        <p id={`${id}-error`} className={`error-text ${fileStyles.departmentError}`} role="alert">
          {shown}
        </p>
      )}
    </form>
  );
}
