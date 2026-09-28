"use client";

// Users of this customer: the list, changing a role, disabling or enabling an
// account, and adding a new user. An admin cannot disable themselves or drop
// their own admin role; the controls for that are not shown, and the server
// refuses it anyway.

import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { roleName } from "@/components/shell/nav";
import { Icon } from "@/components/ui/Icon";
import { PasswordField } from "@/components/ui/PasswordField";
import { ApiError, createUser, listUsers, updateUser } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { AdminUser, Role } from "@/lib/types";
import table from "@/components/documents/Table.module.css";
import {
  MIN_PASSWORD_LENGTH,
  NEW_USER_FIELD_IDS,
  ROLES,
  firstInvalidField,
  validateNewUser,
} from "./logic";
import type { NewUserErrors, NewUserInput } from "./logic";
import styles from "./Admin.module.css";

const SKELETON_ROWS = [0, 1, 2];

export function UsersPanel({ currentUserId }: { currentUserId: string }) {
  const [attempt, setAttempt] = useState(0);
  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [error, setError] = useState<{ attempt: number; message: string } | null>(null);

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
          <table className={`table ${table.reflow}`} role="table">
            <caption className="visually-hidden">Users of this customer</caption>
            <thead role="rowgroup">
              <tr role="row">
                <th scope="col" role="columnheader">
                  Name
                </th>
                <th scope="col" role="columnheader">
                  E-mail
                </th>
                <th scope="col" role="columnheader">
                  Role
                </th>
                <th scope="col" role="columnheader">
                  Status
                </th>
                <th scope="col" role="columnheader">
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
                  onChanged={replace}
                />
              ))}
            </tbody>
          </table>
        )}
      </section>
      <AddUserForm
        onAdded={(user) => setUsers((current) => (current ? [...current, user] : [user]))}
      />
    </div>
  );
}

function UserRow({
  user,
  isSelf,
  onChanged,
}: {
  user: AdminUser;
  isSelf: boolean;
  onChanged: (user: AdminUser) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  async function change(changes: { role?: Role; disabled?: boolean }, done: string) {
    setBusy(true);
    setError(null);
    setSaved(null);
    try {
      onChanged(await updateUser(user.id, changes));
      setSaved(done);
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : "Could not save the change. Check your connection, then try again.",
      );
    } finally {
      setBusy(false);
    }
  }

  const selectId = `role-${user.id}`;
  return (
    <tr role="row">
      <td role="cell" className={styles.nameCell}>
        {user.display_name}
        {isSelf && <span className="muted"> (you)</span>}
      </td>
      <td role="cell" data-label="E-mail" className={styles.wrap}>
        {user.email}
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
        <span className="status-chip" data-tone={user.disabled ? "danger" : "ready"}>
          {user.disabled ? "Disabled" : "Active"}
        </span>
      </td>
      <td role="cell" data-label="Added" className={`${styles.nowrap} tabular`}>
        {formatDate(user.created_at)}
      </td>
      <td role="cell" className={styles.actionsCell}>
        <div className={styles.actions}>
          {!isSelf && (
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
          <span aria-live="polite" className={styles.saved}>
            {saved ?? ""}
          </span>
          {error && (
            <span className="error-text" role="alert">
              {error}
            </span>
          )}
        </div>
      </td>
    </tr>
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
      setAdded(`Added ${user.display_name}.`);
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
          label="Password"
          autoComplete="new-password"
          hint={`At least ${MIN_PASSWORD_LENGTH} characters.`}
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
