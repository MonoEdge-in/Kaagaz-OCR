"use client";

// Users of this customer: the list (one User column, the name with the e-mail
// under it, so neither is squeezed) with each account's status, changing a
// role, and each row's other actions in one menu (UI audit AD2, 16 Oct 2026):
// disabling or enabling an account, unlocking it, resetting its password or its
// two-step sign-in, each that takes something away asked first in a dialog. Each menu item
// has one plain line under it saying what it does (USER_MENU_HELP). "Add user" opens
// the form in a side panel (AD3), with a password field across it and Generate.
// What happened shows in a toast in the corner. An admin cannot disable themselves, drop
// their own admin role or reset their own two-step sign-in; the controls for that are not
// shown, and the server refuses it anyway. An admin may allow a member or
// reviewer to delete forever (asked first; needs the admin's own two-step
// sign-in), and stop it again.
//
// Connected accounts (#457 phase 1): "Connections to approve" above the list while any wait
// (Approve, Decline), a "Signs in with" column, and "Remove connected accounts" in a row's
// menu, asked first. None of it shows while no provider is switched on (the server's 404).
//
// A reset password comes back once, in the reply. It is shown in a card above
// the list, with Copy, until Done or until the tab is left; it is never stored.

import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { ActionDialog } from "@/components/documents/ActionDialog";
import { useMinuteNow } from "@/components/chat/hooks";
import { roleName } from "@/components/shell/nav";
import { closeAllowed, isBackdropClick } from "@/components/ui/drawerLogic";
import { Icon } from "@/components/ui/Icon";
import { IconButton } from "@/components/ui/IconButton";
import { PasswordField } from "@/components/ui/PasswordField";
import {
  ApiError,
  approveConnection,
  createUser,
  declineConnection,
  listUsers,
  pendingConnections,
  removeConnections,
  resetUser2fa,
  resetUserPassword,
  setDeleteForever,
  unlockUser,
  updateUser,
} from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { AddedUser, AdminUser, PendingConnection, Role } from "@/lib/types";
import table from "@/components/documents/Table.module.css";
import { CopyButton } from "./CopyButton";
import { MoreRows, useRowCap } from "./RowCap";
import { RowMenu } from "./RowMenu";
import type { RowMenuItem } from "./RowMenu";
import { Toast } from "./Toast";
import type { Notice } from "./Toast";
import {
  MIN_PASSWORD_LENGTH,
  NEW_USER_FIELD_IDS,
  REMOVE_CONNECTIONS_CONFIRM,
  addedNotice,
  ROLES,
  allowForeverQuestion,
  firstInvalidField,
  foreverControl,
  generatePassword,
  pendingLine,
  signsInWith,
  twoStepNote,
  userActions,
  userStatus,
  validateNewUser,
} from "./logic";
import type { NewUserErrors, NewUserInput } from "./logic";
import styles from "./Admin.module.css";

const SKELETON_ROWS = [0, 1, 2];

/** A temporary password just made for one user: shown once, then gone. */
interface Revealed {
  name: string;
  password: string;
}

export function UsersPanel({
  currentUserId,
  adminTwoStep,
}: {
  currentUserId: string;
  adminTwoStep: boolean;
}) {
  const [attempt, setAttempt] = useState(0);
  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [error, setError] = useState<{ attempt: number; message: string } | null>(null);
  const [revealed, setRevealed] = useState<Revealed | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [adding, setAdding] = useState(false);
  const closeNotice = useCallback(() => setNotice(null), []);
  // Rounded to the minute, so "Locked until" and Unlock clear on their own.
  const now = useMinuteNow();
  const cap = useRowCap();

  useEffect(() => {
    let cancelled = false;
    listUsers().then(
      (result) => {
        if (!cancelled) setUsers(result.items);
      },
      (caught: unknown) => {
        if (!cancelled) {
          setError({
            attempt,
            message:
              caught instanceof ApiError
                ? caught.message
                : "Check your internet connection, then try again.",
          });
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  const failed = error !== null && error.attempt === attempt ? error.message : null;

  function replace(user: AdminUser) {
    // A reply without signs_in_with (#457) changed no connection: the row keeps what it had.
    setUsers((current) =>
      current ? current.map((existing) => (existing.id === user.id ? { ...existing, ...user } : existing)) : current,
    );
  }

  return (
    <div className={styles.panelStack}>
      <ConnectionsToApprove onApproved={() => setAttempt((n) => n + 1)} onNotice={setNotice} />
      <section className={styles.section} aria-labelledby="users-heading">
        <div className={styles.sectionHead}>
          <h2 id="users-heading" className={styles.sectionHeading}>
            Users
          </h2>
          <button
            type="button"
            className="btn btn-primary"
            aria-haspopup="dialog"
            aria-expanded={adding}
            onClick={() => setAdding(true)}
          >
            <Icon name="plus" />
            Add user
          </button>
        </div>
        {revealed && <TemporaryPassword revealed={revealed} onDone={() => setRevealed(null)} />}
        {failed && !users && (
          <div className={styles.errorBanner} role="alert">
            <Icon name="alert" className={styles.errorIcon} />
            <div className={styles.errorBody}>
              <p className={styles.errorTitle}>Could not load the users.</p>
              <p>{failed}</p>
            </div>
            <button type="button" className="btn" onClick={() => setAttempt((n) => n + 1)}>
              <Icon name="refresh" />
              Try again
            </button>
          </div>
        )}
        {!users && !failed && (
          <>
            <p className="visually-hidden" role="status">
              Loading the users…
            </p>
            <div className={`delayed ${styles.skeletons}`} aria-hidden="true">
              {SKELETON_ROWS.map((row) => (
                <span key={row} className={`skeleton ${styles.skeletonRow}`} />
              ))}
            </div>
          </>
        )}
        {users && (
          <div className={`${table.reflowWrap} ${styles.tableScroll}`}>
            <table className={`table ${table.reflow}`} role="table">
              <caption className="visually-hidden">Users of this customer</caption>
              <thead role="rowgroup">
                <tr role="row">
                  <th scope="col" role="columnheader">
                    User
                  </th>
                  <th scope="col" role="columnheader" className={styles.roleColumn}>
                    Role
                  </th>
                  <th scope="col" role="columnheader" className={styles.statusColumn}>
                    Status
                  </th>
                  <th scope="col" role="columnheader">
                    Signs in with
                  </th>
                  <th scope="col" role="columnheader" className={styles.addedColumn}>
                    Added
                  </th>
                  <th scope="col" role="columnheader" className={styles.actionsColumn}>
                    <span className="visually-hidden">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody role="rowgroup">
                {users.slice(0, cap.limit).map((user) => (
                  <UserRow
                    key={user.id}
                    user={user}
                    isSelf={user.id === currentUserId}
                    adminTwoStep={adminTwoStep}
                    now={now}
                    onChanged={replace}
                    onPasswordReset={setRevealed}
                    onNotice={setNotice}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
        {users && <MoreRows shown={Math.min(cap.limit, users.length)} total={users.length} what="users" onMore={cap.showMore} />}
        {/* AD7: two-step is required for admins (0035); one without it sets it up at the next sign-in. */}
        {users?.some((user) => user.role === "admin" && !user.totp_enabled) && (
          <p className="hint">{twoStepNote()}</p>
        )}
      </section>
      {adding && (
        <AddUserPanel
          onClose={() => setAdding(false)}
          onAdded={(user) => {
            setUsers((current) => (current ? [...current, user] : [user]));
            setAdding(false);
            // #457: with email on, "Invite sent to {email}"; else the temporary password, as before.
            setNotice({ text: addedNotice(user) });
          }}
        />
      )}
      <Toast notice={notice} onClose={closeNotice} />
    </div>
  );
}

/** Disabling and the two resets ask first (each signs the user out on every device), and
 * so does allowing Delete forever (UI audit AD2: every action that takes something away). */
type Confirming = "disable" | "password" | "2fa" | "forever" | "connections";

const CONFIRM_TEXT: Record<Confirming, (name: string) => string> = {
  connections: () => REMOVE_CONNECTIONS_CONFIRM,
  disable: (name) => `Disable ${name}'s account? They are signed out and can't sign in until it is enabled again.`,
  password: (name) =>
    `Reset ${name}'s password? They are signed out everywhere and must choose a new one.`,
  "2fa": (name) =>
    `Reset ${name}'s two-step code? They are signed out, and can sign in with their password, then set up a new code.`,
  forever: allowForeverQuestion,
};

// Plain words (25 Oct 2026): the confirm button says the menu item's own verb, and the
// toast says it was done ("Disable", then "disabled.").
const CONFIRM_BUTTON: Record<Confirming, string> = {
  disable: "Disable",
  password: "Reset password",
  "2fa": "Reset two-step code",
  forever: "Allow Delete forever",
  connections: "Remove connected accounts",
};

/** What each item in a user's menu does, in one plain line under it (plain words, 25 Oct 2026). */
export const USER_MENU_HELP = {
  unlock: "Lets them try to sign in again now, without waiting for the lock to end.",
  enable: "Lets them sign in again.",
  stopForever: "Takes back their right to delete documents in Trash permanently.",
  allowForever: "Lets them delete any document in Trash permanently. Deleted documents can't be recovered.",
  password: "Makes a one-time password for you to give them. They are signed out, then choose their own when they sign in.",
  twoStep: "For a lost phone: turns off their two-step code and signs them out, so they can set up a new one.",
  disable: "Signs them out and stops them signing in until you enable them again.",
  connections: "Stops them signing in with Google. Their password still works.",
} as const;

function UserRow({
  user,
  isSelf,
  adminTwoStep,
  now,
  onChanged,
  onPasswordReset,
  onNotice,
}: {
  user: AdminUser;
  isSelf: boolean;
  adminTwoStep: boolean;
  now: number;
  onChanged: (user: AdminUser) => void;
  onPasswordReset: (revealed: Revealed) => void;
  onNotice: (notice: Notice) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState<Confirming | null>(null);

  async function act(send: () => Promise<AdminUser>, done: string) {
    setBusy(true);
    try {
      onChanged(await send());
      onNotice({ text: `${user.display_name}: ${done}` });
    } catch (caught) {
      onNotice({
        problem: true,
        text:
          caught instanceof ApiError
            ? caught.message
            : "Could not save the change. Check your connection, then try again.",
      });
    } finally {
      setBusy(false);
      setConfirming(null);
    }
  }

  const change = (changes: { role?: Role; disabled?: boolean }, done: string) =>
    act(() => updateUser(user.id, changes), done);

  const confirmed = (kind: Confirming) => {
    if (kind === "disable") return change({ disabled: true }, "disabled.");
    if (kind === "forever") return act(() => setDeleteForever(user.id, true), "Delete forever allowed.");
    if (kind === "2fa") return act(() => resetUser2fa(user.id), "two-step code reset.");
    if (kind === "connections") return act(() => removeConnections(user.id), "connected accounts removed.");
    return act(async () => {
      const reset = await resetUserPassword(user.id);
      onPasswordReset({ name: user.display_name, password: reset.temporary_password });
      return reset.user;
    }, "password reset.");
  };

  const can = userActions(user, { isSelf, now });
  const forever = foreverControl(user, { isSelf, adminTwoStep });
  const reason = forever.kind === "allow" ? forever.disabledReason : null;
  const selectId = `role-${user.id}`;
  // AD2: one menu per row. Safe actions first; the one that takes something away last, apart.
  const items: RowMenuItem[] = [
    ...(can.unlock
      ? [{ label: "Unlock", description: USER_MENU_HELP.unlock, onSelect: () => void act(() => unlockUser(user.id), "unlocked.") }]
      : []),
    ...(can.toggleDisabled && user.disabled
      ? [{ label: "Enable", description: USER_MENU_HELP.enable, onSelect: () => void change({ disabled: false }, "enabled.") }]
      : []),
    ...(forever.kind === "stop"
      ? [
          {
            label: "Stop Delete forever",
            description: USER_MENU_HELP.stopForever,
            onSelect: () => void act(() => setDeleteForever(user.id, false), "Delete forever stopped."),
          },
        ]
      : []),
    ...(forever.kind === "allow"
      ? [{ label: "Allow Delete forever", description: USER_MENU_HELP.allowForever, onSelect: () => setConfirming("forever"), disabledReason: reason }]
      : []),
    ...(can.resetPassword
      ? [{ label: "Reset password", description: USER_MENU_HELP.password, onSelect: () => setConfirming("password") }]
      : []),
    ...(can.reset2fa
      ? [{ label: "Reset two-step code", description: USER_MENU_HELP.twoStep, onSelect: () => setConfirming("2fa") }]
      : []),
    ...((user.signs_in_with ?? []).some((way) => way !== "password")
      ? [{ label: "Remove connected accounts", description: USER_MENU_HELP.connections, onSelect: () => setConfirming("connections") }]
      : []),
    ...(can.toggleDisabled && !user.disabled
      ? [{ label: "Disable", description: USER_MENU_HELP.disable, onSelect: () => setConfirming("disable"), destructive: true }]
      : []),
  ];
  return (
    <tr role="row">
      <td role="cell" className={styles.userCell}>
        <span className={styles.userName}>
          {user.display_name}
          {isSelf && <span className="muted"> (you)</span>}
        </span>
        {/* AD1: one line, cut with "…"; the whole address on hover and for screen readers. */}
        <span className={styles.userEmail} title={user.email}>
          <span className="visually-hidden">E-mail: </span>
          {user.email}
        </span>
      </td>
      <td role="cell" data-label="Role">
        <label htmlFor={selectId} className="visually-hidden">
          Role for {user.display_name}
        </label>
        <select
          id={selectId}
          className={`input ${styles.roleSelect}`}
          value={user.role}
          disabled={busy || isSelf}
          onChange={(event) => {
            const role = ROLES.find((option) => option === event.target.value);
            if (role) void change({ role }, `role changed to ${roleName(role)}.`);
          }}
        >
          {ROLES.map((role) => (
            <option key={role} value={role}>
              {roleName(role)}
            </option>
          ))}
        </select>
      </td>
      <td role="cell" data-label="Status">
        <span className={styles.statusList}>
          {userStatus(user, now).map((part) => (
            <span key={part.text} className="status-chip" data-tone={part.tone}>
              {part.text}
            </span>
          ))}
        </span>
      </td>
      <td role="cell" data-label="Signs in with">
        {signsInWith(user.signs_in_with)}
      </td>
      <td role="cell" data-label="Added" className="tabular">
        <span className={styles.date}>{formatDate(user.created_at)}</span>
      </td>
      <td role="cell" className={styles.actionsCell}>
        <span className={styles.rowActions}>
          <RowMenu name={user.display_name} items={items} />
          {busy && <span className="spinner" aria-hidden="true" />}
        </span>
        {confirming && (
          <ActionDialog title={user.display_name} onClose={() => setConfirming(null)} canClose={() => !busy}>
            <p>{CONFIRM_TEXT[confirming](user.display_name)}</p>
            {/* [Cancel] [Yes, ...]: the action last; Cancel has focus, so Enter never acts by accident. */}
            <div className="actions">
              <button type="button" className="btn" data-autofocus disabled={busy} onClick={() => setConfirming(null)}>
                Cancel
              </button>
              <button
                type="button"
                className={`btn btn-danger ${busy ? styles.busy : ""}`}
                disabled={busy}
                aria-busy={busy || undefined}
                onClick={() => void confirmed(confirming)}
              >
                {busy && <span className="spinner" aria-hidden="true" />}
                {CONFIRM_BUTTON[confirming]}
              </button>
            </div>
          </ActionDialog>
        )}
      </td>
    </tr>
  );
}

/** Connections to approve (#457 phase 1): each waiting Google account, its person, email and
 * day, with Approve and Decline; shown only while any wait (and never while no provider is on). */
function ConnectionsToApprove({ onApproved, onNotice }: { onApproved(): void; onNotice(notice: Notice): void }) {
  const [pending, setPending] = useState<PendingConnection[]>([]);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    pendingConnections().then(
      (reply) => {
        if (live) setPending(reply.items);
      },
      () => undefined, // 404 while no provider is on, or no answer: nothing to approve shows
    );
    return () => {
      live = false;
    };
  }, []);

  async function decide(row: PendingConnection, approve: boolean) {
    setBusy(row.id);
    try {
      const reply = await (approve ? approveConnection(row.id) : declineConnection(row.id));
      setPending(reply.items);
      onNotice({ text: `${row.display_name}: Google ${approve ? "approved" : "declined"}.` });
      if (approve) onApproved();
    } catch (caught) {
      onNotice({
        problem: true,
        text: caught instanceof ApiError ? caught.message : "Could not save the change. Check your connection, then try again.",
      });
    } finally {
      setBusy(null);
    }
  }

  if (pending.length === 0) return null;
  return (
    <section className={styles.section} aria-labelledby="connections-heading">
      <h2 id="connections-heading" className={styles.sectionHeading}>
        Connections to approve
      </h2>
      <ul className={styles.connections}>
        {pending.map((row) => (
          <li key={row.id} className={styles.connection}>
            <span className={styles.connectionText}>{pendingLine(row)}</span>
            <span className={styles.rowActions}>
              <button type="button" className="btn btn-primary" disabled={busy !== null} onClick={() => void decide(row, true)}>
                Approve
              </button>
              <button type="button" className="btn" disabled={busy !== null} onClick={() => void decide(row, false)}>
                Decline
              </button>
              {busy === row.id && <span className="spinner" aria-hidden="true" />}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

/**
 * The temporary password, shown once. Focus moves to its heading so a screen
 * reader hears it at once; Done (or leaving the tab) drops it from memory.
 */
function TemporaryPassword({ revealed, onDone }: { revealed: Revealed; onDone: () => void }) {
  const focusOnMount = useCallback((element: HTMLHeadingElement | null) => {
    element?.focus();
  }, []);
  return (
    <section className={`card stack ${styles.reveal}`} aria-labelledby="temporary-password-heading">
      <h3
        id="temporary-password-heading"
        ref={focusOnMount}
        tabIndex={-1}
        className={`${styles.sectionHeading} ${styles.revealHeading}`}
      >
        Temporary password for {revealed.name}
      </h3>
      <p>
        It is shown only this once. Give it to {revealed.name} yourself. They choose their own
        password when they sign in with it.
      </p>
      <div className={styles.secretRow}>
        <code className={styles.secret} translate="no">
          {revealed.password}
        </code>
        <CopyButton text={revealed.password} what="Password" buttonText="Copy password" />
      </div>
      <div>
        <button type="button" className="btn" onClick={onDone}>
          Done
        </button>
      </div>
    </section>
  );
}

const EMPTY_USER: NewUserInput = { email: "", display_name: "", role: "member", password: "" };
const ADD_USER_TITLE_ID = "add-user-heading";
const UNSAVED_QUESTION = "Close without adding? What you typed here will be lost.";

/**
 * AD3: the new-user form in a side panel, a native <dialog> (top layer, focus trap,
 * Esc, inert page). Esc, the backdrop (press and release both on it) and Close ask
 * first when something is typed; focus goes back to "Add user" when it closes.
 */
function AddUserPanel({ onClose, onAdded }: { onClose: () => void; onAdded: (user: AddedUser) => void }) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const pressTarget = useRef<EventTarget | null>(null);
  const [input, setInput] = useState<NewUserInput>(EMPTY_USER);
  const [errors, setErrors] = useState<NewUserErrors>({});
  const [serverError, setServerError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [masked, setMasked] = useState(true);
  const unsaved = input.email !== "" || input.display_name !== "" || input.password !== "";
  const askClose = () => {
    if (!busy && closeAllowed(unsaved, () => window.confirm(UNSAVED_QUESTION))) onClose();
  };

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialog.showModal();
    document.getElementById(NEW_USER_FIELD_IDS.email)?.focus();
    return () => {
      dialog.close();
      if (opener?.isConnected) opener.focus();
    };
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return; // Enter in a field while adding: never a second user
    setMasked(true); // never leave a password showing once it is sent
    setServerError(null);
    const found = validateNewUser(input);
    setErrors(found);
    const first = firstInvalidField(found);
    if (first) {
      document.getElementById(NEW_USER_FIELD_IDS[first])?.focus();
      return;
    }
    setBusy(true);
    try {
      const user = await createUser({
        ...input,
        email: input.email.trim(),
        display_name: input.display_name.trim(),
      });
      onAdded(user);
    } catch (caught) {
      const message =
        caught instanceof ApiError
          ? caught.message
          : "Could not add the user. Check your connection, then try again.";
      if (caught instanceof ApiError && caught.code === "conflict") {
        setErrors({ email: message });
      } else {
        setServerError(message);
      }
      setBusy(false);
    }
  }

  return (
    <dialog
      ref={dialogRef}
      className={styles.sidePanel}
      aria-labelledby={ADD_USER_TITLE_ID}
      onCancel={(event) => {
        event.preventDefault(); // Esc: asks first when something is typed
        askClose();
      }}
      onPointerDown={(event) => {
        pressTarget.current = event.target;
      }}
      onClick={(event) => {
        if (isBackdropClick(pressTarget.current, event.target, event.currentTarget)) askClose();
        pressTarget.current = null;
      }}
    >
      <div className={styles.sidePanelBody}>
        <div className={styles.sectionHead}>
          <h2 id={ADD_USER_TITLE_ID} className={styles.sectionHeading}>
            Add a user
          </h2>
          <IconButton icon="close" label="Close" className="btn-quiet" onClick={askClose} />
        </div>
        <form className={styles.form} onSubmit={submit} noValidate>
          <Field
            id={NEW_USER_FIELD_IDS.email}
            label="E-mail"
            error={errors.email}
            type="email"
            value={input.email}
            onChange={(email) => setInput({ ...input, email })}
          />
          <Field
            id={NEW_USER_FIELD_IDS.display_name}
            label="Name"
            error={errors.display_name}
            type="text"
            value={input.display_name}
            onChange={(display_name) => setInput({ ...input, display_name })}
          />
          <div className="field">
            <label htmlFor="new-user-role">Role</label>
            <select
              id="new-user-role"
              className="input"
              value={input.role}
              onChange={(event) => {
                const role = ROLES.find((option) => option === event.target.value);
                if (role) setInput({ ...input, role });
              }}
            >
              {ROLES.map((role) => (
                <option key={role} value={role}>
                  {roleName(role)}
                </option>
              ))}
            </select>
          </div>
          {/* AD3: the password across the panel, Generate and Copy under it. */}
          <div className={styles.passwordBlock}>
            <PasswordField
              id={NEW_USER_FIELD_IDS.password}
              label="Temporary password"
              autoComplete="new-password"
              hint={`At least ${MIN_PASSWORD_LENGTH} characters. Give it to them yourself; they set their own when they sign in.`}
              value={input.password}
              onChange={(password) => setInput({ ...input, password })}
              error={errors.password ?? null}
              masked={masked}
              onMaskedChange={setMasked}
            />
            <div className={styles.passwordTools}>
              <button
                type="button"
                className="btn"
                onClick={() => {
                  setInput({ ...input, password: generatePassword() });
                  setMasked(false); // shown, so it can be copied and given to them
                }}
              >
                Generate
              </button>
              {input.password !== "" && <CopyButton text={input.password} what="Password" buttonText="Copy" />}
            </div>
          </div>
          {serverError && (
            <p className={`error-text ${styles.formEnd}`} role="alert">
              {serverError}
            </p>
          )}
          <div className={`actions ${styles.formEnd}`}>
            <button type="button" className="btn" disabled={busy} onClick={askClose}>
              Cancel
            </button>
            <button
              type="submit"
              className={`btn btn-primary ${busy ? styles.busy : ""}`}
              disabled={busy}
              aria-busy={busy || undefined}
            >
              {busy && <span className="spinner" aria-hidden="true" />}
              Add user
            </button>
          </div>
        </form>
      </div>
    </dialog>
  );
}

function Field({
  id,
  label,
  error,
  type,
  value,
  onChange,
}: {
  id: string;
  label: string;
  error: string | undefined;
  type: "email" | "text";
  value: string;
  onChange: (value: string) => void;
}) {
  const errorId = `${id}-error`;
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        className="input"
        type={type}
        autoComplete="off"
        spellCheck={false}
        autoCapitalize={type === "email" ? "none" : "words"}
        inputMode={type === "email" ? "email" : undefined}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? errorId : undefined}
      />
      {error && (
        <span id={errorId} className="error-text">
          {error}
        </span>
      )}
    </div>
  );
}
