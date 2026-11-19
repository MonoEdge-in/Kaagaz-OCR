"use client";

// A person's Access page (PRD 7 PR 11a, #604; /admin/people/{id}): Management, and three lists, each
// with Give access and one Remove a row: their departments (with the Head tick), the private
// folders they are named on, and the files given to them. Read from GET /admin/users/{id}/access,
// with the users list for their email and for who added what. Every change asks first where it
// takes something away, goes through the password step when it gives the admin themselves access,
// then reads the page again. A turned-off person can be given nothing (409 disabled): the Give
// buttons, Management and the Head ticks go. Locked files wait for PR 12. Admin sessions only.

import Link from "next/link";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { usePasswordStep } from "@/components/access/SelfPasswordDialog";
import { asksFirst } from "@/components/access/passwordStep";
import {
  ACCESS_PAGE,
  ACCESS_REMOVED,
  DISABLED_BANNER,
  REMOVE_AS_HEAD,
  MANAGEMENT_WHAT,
  accessErrorText,
  accessTitle,
  byLine,
  canNowSeeToast,
  filesHeading,
  headToast,
  makeManagementQuestion,
  managementLine,
  managementToast,
  noDepartmentsText,
  noFilesText,
  noFoldersText,
  quoted,
  removeDepartmentQuestion,
  removeFileQuestion,
  removeFolderQuestion,
  removeHeadQuestion,
  sharedToast,
  stopManagementQuestion,
} from "@/components/access/words";
import access from "@/components/access/Access.module.css";
import { ActionDialog } from "@/components/documents/ActionDialog";
import { buildTree, pathLabel } from "@/components/documents/folderTree";
import type { FolderTree } from "@/components/documents/folderTree";
import { roleName } from "@/components/shell/nav";
import { EmptyState } from "@/components/ui/EmptyState";
import { Icon } from "@/components/ui/Icon";
import {
  ApiError,
  addNamed,
  getPersonAccess,
  giveFile,
  listAdminDepartments,
  listDocuments,
  listFolders,
  listUsers,
  putMembership,
  removeMembership,
  removeNamed,
  setManagement,
  takeFile,
} from "@/lib/api";
import { errorWords } from "@/lib/errorWords";
import { formatDate } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { AdminUser, PersonAccess } from "@/lib/types";
import { AdminGate } from "./AdminView";
import { Toast } from "./Toast";
import type { Notice } from "./Toast";
import styles from "./Admin.module.css";

const USERS_TAB = "/admin?tab=users";

export interface Loaded {
  access: PersonAccess;
  users: AdminUser[];
}

export function AccessPage({ userId }: { userId: string }) {
  return (
    <AdminGate>
      <AccessLoader userId={userId} />
    </AdminGate>
  );
}

function AccessLoader({ userId }: { userId: string }) {
  const { me } = useSession();
  const [attempt, setAttempt] = useState(0);
  const [data, setData] = useState<Loaded | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const reload = useCallback(() => setAttempt((n) => n + 1), []);

  useEffect(() => {
    let live = true;
    // The users list only adds emails and who gave what: without it the page still works.
    Promise.all([getPersonAccess(userId), listUsers().catch(() => ({ items: [] }))]).then(
      ([found, users]) => {
        if (!live) return;
        setData({ access: found, users: users.items });
        setFailed(null);
      },
      (caught: unknown) => {
        if (!live) return;
        setFailed(accessLoadFailure(caught));
      },
    );
    return () => {
      live = false;
    };
  }, [userId, attempt]);

  return <AccessPageView data={data} failed={failed} myId={me.user.id} onRetry={reload} onChanged={reload} />;
}

/** "missing" for a person who isn't there (404) or an id that can't be one (422), as one text;
 * else the words of a failed load. */
export function accessLoadFailure(caught: unknown): string {
  return caught instanceof ApiError && (caught.status === 404 || caught.status === 422) ? "missing" : errorWords(caught);
}

interface Confirming {
  text: string;
  button: string;
  danger: boolean;
  go(): void;
}

type Giving = "department" | "folder" | "file";

/** The page, for the render tests: `data` null while it loads; `failed` "missing" for a person
 * who isn't there (404), else the words of a failed load. */
export function AccessPageView({
  data,
  failed,
  myId,
  onRetry,
  onChanged,
}: {
  data: Loaded | null;
  failed: string | null;
  myId: string;
  onRetry(): void;
  onChanged(): void;
}) {
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [confirming, setConfirming] = useState<Confirming | null>(null);
  const [giving, setGiving] = useState<Giving | null>(null);
  const closeNotice = useCallback(() => setNotice(null), []);
  const passwordStep = usePasswordStep();

  if (failed === "missing") {
    return (
      <div className="page">
        <EmptyState
          icon="admin"
          title={ACCESS_PAGE.notFound}
          headingLevel={1}
          action={
            <Link href={USERS_TAB} className="btn">
              {ACCESS_PAGE.back}
            </Link>
          }
        />
      </div>
    );
  }
  if (!data) {
    return (
      <div className="page">
        <BackLink />
        {failed ? (
          <LoadFailed failed={failed} onRetry={onRetry} />
        ) : (
          <>
            <p className="visually-hidden" role="status">
              Loading this person&apos;s access…
            </p>
            <div className={`delayed ${styles.skeletons}`} aria-hidden="true">
              {[0, 1, 2].map((row) => (
                <span key={row} className={`skeleton ${styles.skeletonRow}`} />
              ))}
            </div>
          </>
        )}
      </div>
    );
  }

  const { user, departments, private_folders: folders, files } = data.access;
  const person = data.users.find((each) => each.id === user.id);
  const nameOf = (id: string | null) => (id ? data.users.find((each) => each.id === id)?.display_name : undefined);
  const isSelf = user.id === myId;
  const open = !user.disabled; // a turned-off person can be given nothing

  /** Sends one change and reads the page again, whatever happened (another admin may have changed
   * it). Null (the password step cancelled) changes nothing; a reply with `changed: false` (it was
   * so already) says nothing. */
  async function change(send: () => Promise<unknown>, done: string) {
    setBusy(true);
    try {
      const reply = await send();
      if (reply === null) return;
      if (!(typeof reply === "object" && reply !== null && "changed" in reply && reply.changed === false)) setNotice({ text: done });
      onChanged();
    } catch (caught) {
      setNotice({ problem: true, text: accessErrorText(caught) });
      onChanged();
    } finally {
      setBusy(false);
    }
  }
  const first = (on?: boolean) => asksFirst({ myId, targetId: user.id, on });
  const setHead = (department: { id: string; name: string }, on: boolean) =>
    change(
      () => passwordStep.run(department.name, (password) => putMembership(user.id, department.id, { head: on, password }), first(on)),
      headToast(user.name, department.name, on),
    );

  const ask = (text: string, button: string, go: () => void, danger = true) => setConfirming({ text, button, danger, go });
  const management = (on: boolean) =>
    change(
      () => passwordStep.run(MANAGEMENT_WHAT, (password) => setManagement(user.id, on, password), first(on)),
      managementToast(user.name, on),
    );

  return (
    <div className="page">
      <BackLink />
      <header className="page-header">
        <div>
          <h1>{accessTitle(user.name)}</h1>
          <p>
            {roleName(user.role)}
            {person && ` · ${person.email}`}
          </p>
        </div>
      </header>
      {failed && <LoadFailed failed={failed} onRetry={onRetry} />}
      {!open && (
        <div className={styles.errorBanner} role="note">
          <Icon name="alert" className={styles.errorIcon} />
          <div className={styles.errorBody}>
            <p>{DISABLED_BANNER}</p>
          </div>
        </div>
      )}
      {isSelf && open && <p className="hint">{ACCESS_PAGE.selfHint}</p>}

      <p className={access.line}>
        <span>{managementLine(user.management)}</span>
        {open && (
          <button
            type="button"
            className="btn"
            disabled={busy}
            onClick={() =>
              user.management
                ? ask(stopManagementQuestion(user.name), "Stop Management", () => void management(false))
                : ask(makeManagementQuestion(user.name), "Make Management", () => void management(true), false)
            }
          >
            {user.management ? "Stop Management" : "Make Management"}
          </button>
        )}
      </p>

      <Section title={ACCESS_PAGE.departments} empty={departments.length === 0 ? noDepartmentsText(user.name) : null} onGive={open ? () => setGiving("department") : null} busy={busy}>
        {departments.map((department) => (
          <li key={department.id} className={access.row}>
            <span className={access.rowText}>
              <span>{department.name}</span>
              <span className={access.rowLine}>{byLine("Added", formatDate(department.added_at), nameOf(department.added_by))}</span>
            </span>
            <span className={access.rowControls}>
              <label className={access.check}>
                <input
                  type="checkbox"
                  checked={department.head}
                  disabled={busy || !open}
                  onChange={(event) => {
                    if (event.target.checked) void setHead(department, true);
                    else ask(removeHeadQuestion(user.name, department.name), REMOVE_AS_HEAD, () => void setHead(department, false));
                  }}
                />
                Head<span className="visually-hidden">{` of ${department.name}`}</span>
              </label>
              <RemoveButton
                what={department.name}
                busy={busy}
                onClick={() =>
                  ask(removeDepartmentQuestion(user.name, department.name), `Remove from ${department.name}`, () =>
                    void change(() => removeMembership(user.id, department.id), ACCESS_REMOVED),
                  )
                }
              />
            </span>
          </li>
        ))}
      </Section>

      <Section title={ACCESS_PAGE.privateFolders} empty={folders.length === 0 ? noFoldersText(user.name) : null} onGive={open ? () => setGiving("folder") : null} busy={busy}>
        {folders.map((folder) => (
          <li key={folder.id} className={access.row}>
            <span className={access.rowText}>{folder.path ?? folder.name}</span>
            <span className={access.rowControls}>
              <RemoveButton
                what={folder.name}
                busy={busy}
                onClick={() =>
                  ask(removeFolderQuestion(user.name, folder.name), "Take off folder", () =>
                    void change(() => removeNamed(folder.id, user.id), ACCESS_REMOVED),
                  )
                }
              />
            </span>
          </li>
        ))}
      </Section>

      <Section title={filesHeading(user.name)} empty={files.length === 0 ? noFilesText(user.name) : null} onGive={open ? () => setGiving("file") : null} busy={busy}>
        {files.map((file) => (
          <li key={file.id} className={access.row}>
            {/* Plain text, not a link: the admin may not be able to open it. */}
            <span className={access.rowText}>
              <span>{file.name}</span>
              {file.path && <span className={access.rowLine}>{file.path}</span>}
              <span className={access.rowLine}>{byLine("Given", formatDate(file.given_at), nameOf(file.given_by))}</span>
            </span>
            <span className={access.rowControls}>
              <RemoveButton
                what={file.name}
                busy={busy}
                onClick={() =>
                  ask(removeFileQuestion(user.name, file.name), "Remove access", () =>
                    void change(() => takeFile(file.id, user.id), ACCESS_REMOVED),
                  )
                }
              />
            </span>
          </li>
        ))}
      </Section>

      {confirming && (
        <ActionDialog title={user.name} onClose={() => setConfirming(null)}>
          <p>{confirming.text}</p>
          {/* [Cancel] [Action]: Cancel has focus, so Enter never acts by accident. */}
          <div className="actions">
            <button type="button" className="btn" data-autofocus onClick={() => setConfirming(null)}>
              Cancel
            </button>
            <button
              type="button"
              className={`btn ${confirming.danger ? "btn-danger" : "btn-primary"}`}
              onClick={() => {
                setConfirming(null);
                confirming.go();
              }}
            >
              {confirming.button}
            </button>
          </div>
        </ActionDialog>
      )}
      {giving && (
        <GiveDialog
          giving={giving}
          data={data}
          onClose={() => setGiving(null)}
          onGive={(place) => {
            setGiving(null);
            const self = (what: string, send: (password?: string) => Promise<unknown>) => passwordStep.run(what, send, first());
            if (place.kind === "department")
              void change(() => self(place.name, (password) => putMembership(user.id, place.id, { password })), canNowSeeToast(user.name, place.name));
            if (place.kind === "folder")
              void change(() => self(quoted(place.name), (password) => addNamed(place.id, user.id, password)), canNowSeeToast(user.name, place.name));
            if (place.kind === "file")
              void change(
                () => self(quoted(place.name), (password) => giveFile(place.id, user.id, password)),
                sharedToast(1, [{ id: user.id, name: user.name }]),
              );
          }}
        />
      )}
      {passwordStep.dialog}
      <Toast notice={notice} onClose={closeNotice} />
    </div>
  );
}

/** Shared with My access (#707), which says `title` in its own words. */
export function LoadFailed({ failed, onRetry, title = ACCESS_PAGE.loadFailed }: { failed: string; onRetry(): void; title?: string }) {
  return (
    <div className={styles.errorBanner} role="alert">
      <Icon name="alert" className={styles.errorIcon} />
      <div className={styles.errorBody}>
        <p className={styles.errorTitle}>{title}</p>
        <p>{failed}</p>
      </div>
      <button type="button" className="btn" onClick={onRetry}>
        <Icon name="refresh" />
        Try again
      </button>
    </div>
  );
}

function BackLink() {
  return (
    <Link href={USERS_TAB} className={access.back}>
      <Icon name="chevron-left" />
      {ACCESS_PAGE.back}
    </Link>
  );
}

/** Shared with My access (#707), read only there (`onGive` null). */
export function Section({
  title,
  empty,
  onGive,
  busy,
  children,
}: {
  title: string;
  empty: string | null;
  onGive: (() => void) | null; // null: nothing can be given (a turned-off account)
  busy: boolean;
  children: ReactNode;
}) {
  const id = useId();
  return (
    <section className={styles.section} aria-labelledby={id}>
      <div className={styles.sectionHead}>
        <h2 id={id} className={styles.sectionHeading}>
          {title}
        </h2>
        {onGive && (
          <button type="button" className="btn" aria-haspopup="dialog" disabled={busy} onClick={onGive}>
            <Icon name="plus" />
            {ACCESS_PAGE.give}
            <span className="visually-hidden">{`: ${title}`}</span>
          </button>
        )}
      </div>
      {empty ? <p className={styles.emptyLine}>{empty}</p> : <ul className={access.list}>{children}</ul>}
    </section>
  );
}

function RemoveButton({ what, busy, onClick }: { what: string; busy: boolean; onClick(): void }) {
  return (
    <button type="button" className="btn btn-quiet" disabled={busy} onClick={onClick}>
      Remove
      <span className="visually-hidden">{` ${what}`}</span>
    </button>
  );
}

interface Place {
  kind: Giving;
  id: string;
  name: string;
  label: string; // a private folder's path, else its name
  path?: string; // a file's folder
}

const GIVE_TITLE: Record<Giving, string> = {
  department: "Give access to a department",
  folder: "Give access to a private folder",
  file: "Give access to a file",
};

/** The small form behind each Give access: a department, a private folder (by its path) or a
 * file (found by name), not one they have already; its one primary button gives it. */
function GiveDialog({
  giving,
  data,
  onClose,
  onGive,
}: {
  giving: Giving;
  data: Loaded;
  onClose(): void;
  onGive(place: Place): void;
}) {
  const id = useId();
  const [options, setOptions] = useState<Place[] | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [find, setFind] = useState("");
  const [chosen, setChosen] = useState("");
  const tree = useRef<Promise<FolderTree> | null>(null); // the folders, read once, for a file's path
  const { departments, private_folders: folders, files } = data.access;

  useEffect(() => {
    let live = true;
    const has = (list: readonly { id: string }[]) => new Set(list.map((each) => each.id));
    const load = async (): Promise<Place[]> => {
      if (giving === "department") {
        const taken = has(departments);
        const reply = await listAdminDepartments();
        return reply.items
          .filter((each) => !taken.has(each.id))
          .map((each) => ({ kind: giving, id: each.id, name: each.name, label: each.name }));
      }
      if (giving === "folder") {
        const taken = has(folders);
        const reply = await listFolders();
        const tree = buildTree(reply.items);
        return reply.items
          .filter((each) => each.private && !taken.has(each.id))
          .map((each) => ({ kind: giving, id: each.id, name: each.name, label: pathLabel(tree, each.id) || each.name }));
      }
      const taken = has(files);
      tree.current ??= listFolders().then(
        (reply) => buildTree(reply.items),
        (caught: unknown) => {
          tree.current = null; // asked again with the next search
          throw caught;
        },
      );
      const [reply, folderTree] = await Promise.all([listDocuments({ q: find.trim() || undefined, limit: 10 }), tree.current]);
      return reply.items
        .filter((each) => !taken.has(each.id))
        .map((each) => ({
          kind: giving,
          id: each.id,
          name: each.display_name,
          label: each.display_name,
          path: each.folder_id ? pathLabel(folderTree, each.folder_id) : undefined,
        }));
    };
    const timer = window.setTimeout(
      () =>
        load().then(
          (found) => {
            if (live) setOptions(found);
          },
          (caught: unknown) => {
            if (live) setFailed(errorWords(caught));
          },
        ),
      giving === "file" ? 300 : 0, // a file: once typing pauses
    );
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [giving, find, departments, folders, files]);

  const place = options?.find((each) => each.id === chosen) ?? null;
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (place) onGive(place);
  }

  return (
    <ActionDialog title={GIVE_TITLE[giving]} onClose={onClose}>
      <form className="stack" onSubmit={submit} noValidate>
        {giving === "file" && (
          <div className="field">
            <label htmlFor={`${id}-find`}>{ACCESS_PAGE.fileSearch}</label>
            <input id={`${id}-find`} className="input" type="search" autoComplete="off" value={find}
              onChange={(event) => {
                setFind(event.target.value);
                setFailed(null); // a new search: the last failure goes
              }}
            />
          </div>
        )}
        {failed && <p className="error-text">{failed}</p>}
        {!failed && options === null && (
          <p className="muted" role="status">
            Loading…
          </p>
        )}
        {options && options.length === 0 && (
          <p className="muted">{giving === "folder" ? ACCESS_PAGE.noPrivateFolders : giving === "file" ? ACCESS_PAGE.noFiles : ACCESS_PAGE.noDepartments}</p>
        )}
        {options && options.length > 0 && (
          <fieldset className={access.picker}>
            <legend className="visually-hidden">{GIVE_TITLE[giving]}</legend>
            <ul className={access.pickerList}>
              {options.map((option) => (
                <li key={option.id}>
                  <label className={access.pickerRow}>
                    <input type="radio" name={id} value={option.id} checked={chosen === option.id} onChange={() => setChosen(option.id)} />
                    <span className={access.rowText}>
                      <span>{option.label}</span>
                      {option.path && <span className={access.rowLine}>{option.path}</span>}
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          </fieldset>
        )}
        <div className="actions">
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={!place}>
            {ACCESS_PAGE.give}
          </button>
        </div>
      </form>
    </ActionDialog>
  );
}
