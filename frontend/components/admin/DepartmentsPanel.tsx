"use client";

// The admin Departments tab (File management F1): the departments in their order, each
// with Rename and Move up / Move down, and a form to add one. A department is a top-level
// folder of kind department; anyone can still make their own folders anywhere. Every name
// is React text. The server refuses the change to anyone but an admin (403).
// Issue 376: one error at a time, in the word "department". An add that failed says why under
// its own form, on a line of its own, so the Add button never moves; typing clears it.
// PRD 7 (#604): each department's access (GET /admin/departments): its head, how many people,
// Department only or open, its people (DepartmentPeople) and the switch (DepartmentOnlyDialog);
// a new department asks who heads it (NewDepartmentHead). What changed shows in a toast.

import { useCallback, useEffect, useId, useState } from "react";
import type { FormEvent } from "react";
import { usePasswordStep } from "@/components/access/SelfPasswordDialog";
import {
  ADD_PEOPLE,
  DEPARTMENT_ONLY,
  KEEP_TO_PEOPLE,
  OPEN_TO_EVERYONE,
  accessErrorText,
  departmentOnlyToast,
  headLine,
  newDepartmentToast,
  noMembersWarning,
  peopleCount,
} from "@/components/access/words";
import access from "@/components/access/Access.module.css";
import { departmentNameProblem, libraryErrorText } from "@/components/documents/library";
import { departmentsOf, nextSortOrder, reorder } from "@/components/files/departments";
import fileStyles from "@/components/files/Files.module.css";
import { FILES_TEXT } from "@/components/files/words";
import { Icon } from "@/components/ui/Icon";
import { IconButton } from "@/components/ui/IconButton";
import { createFolder, listAdminDepartments, listFolders, setDepartmentOnly, updateFolder } from "@/lib/api";
import { errorWords } from "@/lib/errorWords";
import type { AdminDepartment, Folder, Person } from "@/lib/types";
import { DepartmentOnlyDialog } from "./DepartmentOnlyDialog";
import { DepartmentPeople } from "./DepartmentPeople";
import { NewDepartmentHead } from "./NewDepartmentHead";
import { Toast } from "./Toast";
import type { Notice } from "./Toast";
import styles from "./Admin.module.css";

const NOBODY: Person = { id: "", name: "" };

/** `me`: the signed-in admin (the head step's Me, and the password step). `initial` and
 * `initialAccess`: what to show without asking (the render tests). */
export function DepartmentsPanel({
  initial,
  initialAccess,
  me = NOBODY,
}: {
  initial?: Folder[];
  initialAccess?: AdminDepartment[];
  me?: Person;
}) {
  const [folders, setFolders] = useState<Folder[] | null>(initial ?? null);
  const [accessOf, setAccessOf] = useState<AdminDepartment[]>(initialAccess ?? []);
  const [error, setError] = useState<string | null>(null);
  const [addError, setAddError] = useState<string | null>(null); // said under the add form
  const [notice, setNotice] = useState<Notice | null>(null);
  const [busy, setBusy] = useState(false);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [people, setPeople] = useState<{ id: string; adding: boolean } | null>(null); // the open people list
  const [switching, setSwitching] = useState<AdminDepartment | null>(null); // Department only, asked
  const [heading, setHeading] = useState<Folder | null>(null); // a new department: who heads it?
  const closeNotice = useCallback(() => setNotice(null), []);
  const step = usePasswordStep();

  useEffect(() => {
    if (initial) return;
    let cancelled = false;
    Promise.all([listFolders(), listAdminDepartments()]).then(
      ([reply, departmentAccess]) => {
        if (cancelled) return;
        setFolders(reply.items);
        setAccessOf(departmentAccess.items);
      },
      (caught: unknown) => {
        if (!cancelled) setError(`The departments did not load. ${errorWords(caught)}`);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [attempt, initial]);

  const departments = departmentsOf(folders ?? []);

  /** `done`: the toast, or null to say nothing (a new department: its head step says it). */
  async function run(work: () => Promise<unknown>, done: string | null, fail: (text: string) => void = setError) {
    setBusy(true);
    setError(null);
    setAddError(null);
    try {
      if ((await work()) !== null && done) setNotice({ text: done });
      setAttempt((n) => n + 1);
    } catch (caught) {
      fail(accessErrorText(caught, () => libraryErrorText(caught, "department")));
    } finally {
      setBusy(false);
    }
  }

  function switchDepartmentOnly(department: AdminDepartment, passwordFirst: boolean) {
    const on = !department.department_only;
    setSwitching(null);
    void run(
      () => step.run(department.name, (password) => setDepartmentOnly(department.id, on, password), passwordFirst),
      departmentOnlyToast(department.name, on),
    );
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
              <DepartmentAccess
                department={accessOf.find((each) => each.id === department.id)}
                busy={busy}
                peopleOpen={people?.id === department.id}
                onPeople={(adding) => setPeople(people?.id === department.id && !adding ? null : { id: department.id, adding })}
                onSwitch={setSwitching}
              />
              {people?.id === department.id && (
                <DepartmentPeople
                  key={`${department.id}-${people.adding}`}
                  department={department}
                  me={me}
                  adding={people.adding}
                  run={step.run}
                  onChanged={() => setAttempt((n) => n + 1)}
                  onNotice={setNotice}
                />
              )}
            </li>
          ))}
        </ol>
      )}
      {heading && (
        <NewDepartmentHead
          department={heading}
          me={me}
          run={step.run}
          onDone={(head) => {
            setNotice({ text: newDepartmentToast(heading.name, head?.name ?? null) });
            setHeading(null);
            setAttempt((n) => n + 1);
          }}
        />
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
              async () => setHeading(await createFolder(name, null, { sort_order: nextSortOrder(departments) })),
              null,
              setAddError,
            )
          }
        />
      )}
      {switching && (
        <DepartmentOnlyDialog
          department={switching}
          myId={me.id}
          onClose={() => setSwitching(null)}
          onAddPeople={() => {
            setSwitching(null);
            setPeople({ id: switching.id, adding: true });
          }}
          onConfirm={(passwordFirst) => switchDepartmentOnly(switching, passwordFirst)}
        />
      )}
      {step.dialog}
      <Toast notice={notice} onClose={closeNotice} />
      {error && folders === null && (
        <button type="button" className="btn" onClick={() => setAttempt((n) => n + 1)}>
          <Icon name="refresh" />
          Try again
        </button>
      )}
    </section>
  );
}

/** A department's access under its name (PRD 7, #604): its head, how many people, Department only
 * or open, People (n) and the switch; with Department only on and nobody in it, a warning with
 * Add people. Nothing while GET /admin/departments has not answered. */
export function DepartmentAccess({
  department,
  busy,
  peopleOpen,
  onPeople,
  onSwitch,
}: {
  department: AdminDepartment | undefined;
  busy: boolean;
  peopleOpen: boolean;
  onPeople(adding: boolean): void;
  onSwitch(department: AdminDepartment): void;
}) {
  if (!department) return null;
  return (
    <>
      <span className={access.facts}>
        <span>{headLine(department.heads)}</span>
        <span>{peopleCount(department.members)}</span>
        {department.department_only ? (
          <span className="status-chip" data-tone="working">
            {DEPARTMENT_ONLY}
          </span>
        ) : (
          <span>{OPEN_TO_EVERYONE}</span>
        )}
        <button type="button" className="btn btn-quiet" aria-expanded={peopleOpen} disabled={busy} onClick={() => onPeople(false)}>
          {`People (${department.members})`}
          <span className="visually-hidden">{` in ${department.name}`}</span>
        </button>
        <button type="button" className="btn" aria-haspopup="dialog" disabled={busy} onClick={() => onSwitch(department)}>
          {department.department_only ? OPEN_TO_EVERYONE : KEEP_TO_PEOPLE}
          <span className="visually-hidden">{`: ${department.name}`}</span>
        </button>
      </span>
      {department.warnings.includes("no_members") && (
        <p className={access.warning} role="note">
          <span>{noMembersWarning(department.name)}</span>
          <button type="button" className="btn" disabled={busy} onClick={() => onPeople(true)}>
            {ADD_PEOPLE}
            <span className="visually-hidden">{` to ${department.name}`}</span>
          </button>
        </p>
      )}
    </>
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
