"use client";

// Users of this customer: the list (one User column, the name with the e-mail
// under it, so neither is squeezed) with each account's status, changing a
// role, disabling or enabling an account, unlocking it, resetting its password
// or its 2FA, and adding a new user. An admin cannot disable themselves, drop
// their own admin role or reset their own 2FA; the controls for that are not
// shown, and the server refuses it anyway. An admin may allow a member or
// reviewer to delete forever (asked first; needs the admin's own two-step
// sign-in), and stop it again.
//
// A reset password comes back once, in the reply. It is shown in a card above
// the list, with Copy, until Done or until the tab is left; it is never stored.

import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";
import { useMinuteNow } from "@/components/chat/hooks";
import { roleName } from "@/components/shell/nav";
import { WrapName } from "@/components/documents/WrapName";
import { Icon } from "@/components/ui/Icon";
import { PasswordField } from "@/components/ui/PasswordField";
import {
  ApiError,
  createUser,
  listUsers,
  resetUser2fa,
  resetUserPassword,
  setDeleteForever,
  unlockUser,
  updateUser,
} from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { AdminUser, Role } from "@/lib/types";
import table from "@/components/documents/Table.module.css";
import { CopyButton } from "./CopyButton";
import {
  MIN_PASSWORD_LENGTH,
  NEW_USER_FIELD_IDS,
  ROLES,
  allowForeverQuestion,
  firstInvalidField,
  foreverControl,
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
  twoStepAllowed,
}: {
  currentUserId: string;
  adminTwoStep: boolean;
  twoStepAllowed: boolean;
}) {
  const [attempt, setAttempt] = useState(0);
  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [error, setError] = useState<{ attempt: number; message: string } | null>(null);
  const [revealed, setRevealed] = useState<Revealed | null>(null);
  // Rounded to the minute, so "Locked until" and Unlock clear on their own.
  const now = useMinuteNow();

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
    setUsers((current) =>
      current ? current.map((existing) => (existing.id === user.id ? user : existing)) : current,
    );
  }

  return (
    <div className={styles.panelStack}>
      <section className={styles.section} aria-labelledby="users-heading">
        <h2 id="users-heading" className={styles.sectionHeading}>
          Users
        </h2>
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
          <div className={table.reflowWrap}>
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
                  <th scope="col" role="columnheader" className={styles.addedColumn}>
                    Added
                  </th>
                  <th scope="col" role="columnheader" className={styles.actionsColumn}>
                    <span className="visually-hidden">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody role="rowgroup">
                {users.map((user) => (
                  <UserRow
                    key={user.id}
                    user={user}
                    isSelf={user.id === currentUserId}
                    adminTwoStep={adminTwoStep}
                    twoStepAllowed={twoStepAllowed}
                    now={now}
                    onChanged={replace}
                    onPasswordReset={setRevealed}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <AddUserForm
        onAdded={(user) => setUsers((current) => (current ? [...current, user] : [user]))}
      />
    </div>
  );
}

/** The two resets ask first (each signs the user out on every device), and so does
 * allowing Delete forever. */
type Confirming = "password" | "2fa" | "forever";

const CONFIRM_TEXT: Record<Confirming, (name: string) => string> = {
  password: (name) =>
    `Reset ${name}'s password? They are signed out everywhere and must choose a new one.`,
  "2fa": (name) =>
    `Reset ${name}'s 2FA? They are signed out and set up the app again at their next ` +
    "admin sign-in.",
  forever: allowForeverQuestion,
};

function UserRow({
  user,
  isSelf,
  adminTwoStep,
  twoStepAllowed,
  now,
  onChanged,
  onPasswordReset,
}: {
  user: AdminUser;
  isSelf: boolean;
  adminTwoStep: boolean;
  twoStepAllowed: boolean;
  now: number;
  onChanged: (user: AdminUser) => void;
  onPasswordReset: (revealed: Revealed) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<Confirming | null>(null);

  // Focus goes to Cancel when a question opens, so Enter never resets by accident.
  const focusOnMount = useCallback((element: HTMLButtonElement | null) => {
    element?.focus();
  }, []);

  async function act(send: () => Promise<AdminUser>, done: string) {
    setBusy(true);
    setError(null);
    setSaved(null);
    try {
      onChanged(await send());
      setSaved(done);
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : "Could not save the change. Check your connection, then try again.",
      );
    } finally {
      setBusy(false);
      setConfirming(null);
    }
  }

  const change = (changes: { role?: Role; disabled?: boolean }, done: string) =>
    act(() => updateUser(user.id, changes), done);

  const confirmed = (kind: Confirming) =>
    kind === "forever"
      ? act(() => setDeleteForever(user.id, true), "Delete forever allowed.")
      : kind === "password"
        ? act(async () => {
            const reset = await resetUserPassword(user.id);
            onPasswordReset({ name: user.display_name, password: reset.temporary_password });
            return reset.user;
          }, "Password reset.")
        : act(() => resetUser2fa(user.id), "2FA reset.");

  const can = userActions(user, { isSelf, now, twoStepAllowed });
  const forever = foreverControl(user, { isSelf, adminTwoStep, twoStepAllowed });
  const reasonId = `forever-reason-${user.id}`;
  const reason = forever.kind === "allow" ? forever.disabledReason : null;
  const foreverWord = forever.kind === "stop" ? "Stop" : "Allow";
  const selectId = `role-${user.id}`;
  const confirmId = `confirm-${user.id}`;
  return (
    <tr role="row">
      <td role="cell" className={styles.wrap}>
        <span className={styles.userName}>
          {user.display_name}
          {isSelf && <span className="muted"> (you)</span>}
        </span>
        <span className={styles.userEmail}>
          <span className="visually-hidden">E-mail: </span>
          <WrapName name={user.email} />
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
            if (role) void change({ role }, `Role changed to ${roleName(role)}.`);
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
      <td role="cell" data-label="Added" className="tabular">
        {formatDate(user.created_at)}
      </td>
      <td role="cell" className={styles.actionsCell}>
        {confirming ? (
          <div className={styles.confirm} role="group" aria-labelledby={confirmId}>
            <span id={confirmId}>{CONFIRM_TEXT[confirming](user.display_name)}</span>
            <div className={styles.confirmButtons}>
              <button
                type="button"
                className="btn btn-danger"
                disabled={busy}
                onClick={() => void confirmed(confirming)}
              >
                {busy ? (
                  <>
                    <span className="spinner" aria-hidden="true" />
                    {confirming === "forever" ? "Saving…" : "Resetting…"}
                  </>
                ) : confirming === "forever" ? (
                  "Yes, allow"
                ) : (
                  "Yes, reset"
                )}
              </button>
              <button
                ref={focusOnMount}
                type="button"
                className="btn"
                disabled={busy}
                onClick={() => setConfirming(null)}
              >
                Cancel
              </button>
            </div>
          </div>
        ) : (
          <div className={styles.actions}>
            {can.unlock && (
              <button
                type="button"
                className="btn"
                disabled={busy}
                onClick={() => void act(() => unlockUser(user.id), "Unlocked.")}
                aria-label={`Unlock ${user.display_name}`}
              >
                Unlock
              </button>
            )}
            {can.toggleDisabled && (
              <button
                type="button"
                className={user.disabled ? "btn" : "btn btn-danger"}
                disabled={busy}
                onClick={() =>
                  void change(
                    { disabled: !user.disabled },
                    user.disabled ? "Account enabled." : "Account disabled.",
                  )
                }
                aria-label={`${user.disabled ? "Enable" : "Disable"} ${user.display_name}`}
              >
                {user.disabled ? "Enable" : "Disable"}
              </button>
            )}
            {can.resetPassword && (
              <button
                type="button"
                className="btn"
                disabled={busy}
                onClick={() => setConfirming("password")}
                aria-label={`Reset password for ${user.display_name}`}
              >
                Reset password
              </button>
            )}
            {can.reset2fa && (
              <button
                type="button"
                className="btn"
                disabled={busy}
                onClick={() => setConfirming("2fa")}
                aria-label={`Reset 2FA for ${user.display_name}`}
              >
                Reset 2FA
              </button>
            )}
            {forever.kind !== "none" && (
              <button
                type="button"
                className="btn"
                disabled={busy || reason !== null}
                onClick={() =>
                  forever.kind === "stop"
                    ? void act(() => setDeleteForever(user.id, false), "Delete forever stopped.")
                    : setConfirming("forever")
                }
                aria-label={`${foreverWord} Delete forever for ${user.display_name}`}
                aria-describedby={reason ? reasonId : undefined}
              >
                {foreverWord} Delete forever
              </button>
            )}
            {reason && (
              <span id={reasonId} className="muted">
                {reason}
              </span>
            )}
          </div>
        )}
        <span aria-live="polite" className={styles.saved}>
          {saved ?? ""}
        </span>
        {error && (
          <p className="error-text" role="alert">
            {error}
          </p>
        )}
      </td>
    </tr>
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

function AddUserForm({ onAdded }: { onAdded: (user: AdminUser) => void }) {
  const [input, setInput] = useState<NewUserInput>(EMPTY_USER);
  const [errors, setErrors] = useState<NewUserErrors>({});
  const [serverError, setServerError] = useState<string | null>(null);
  const [added, setAdded] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [masked, setMasked] = useState(true);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMasked(true); // never leave a password showing once it is sent
    setAdded(null);
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
      setInput(EMPTY_USER);
      setAdded(
        `Added ${user.display_name}. They choose their own password when they first sign in.`,
      );
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
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card stack" aria-labelledby="add-user-heading">
      <h2 id="add-user-heading" className={styles.sectionHeading}>
        Add a user
      </h2>
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
        <PasswordField
          id={NEW_USER_FIELD_IDS.password}
          label="Temporary password"
          autoComplete="new-password"
          hint={`At least ${MIN_PASSWORD_LENGTH} characters. They set their own when they sign in.`}
          value={input.password}
          onChange={(password) => setInput({ ...input, password })}
          error={errors.password ?? null}
          masked={masked}
          onMaskedChange={setMasked}
        />
        <div className={styles.formEnd}>
          <button type="submit" className="btn btn-primary" disabled={busy}>
            {busy ? (
              <>
                <span className="spinner" aria-hidden="true" />
                Adding…
              </>
            ) : (
              "Add user"
            )}
          </button>
          <span aria-live="polite" className={styles.saved}>
            {added ?? ""}
          </span>
          {serverError && (
            <span className="error-text" role="alert">
              {serverError}
            </span>
          )}
        </div>
      </form>
    </section>
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
