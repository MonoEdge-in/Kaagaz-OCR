"use client";

// Users of this customer: the list, changing a role, disabling or enabling an
// account, and adding a new user. An admin cannot disable themselves or drop
// their own admin role; the controls for that are not shown, and the server
// refuses it anyway.

import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { ApiError, createUser, listUsers, updateUser } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { AdminUser, Role } from "@/lib/types";
import { roleName } from "@/components/shell/nav";
import { ROLES, validateNewUser } from "./logic";
import type { NewUserErrors, NewUserInput } from "./logic";
import styles from "./Admin.module.css";

export function UsersPanel({ currentUserId }: { currentUserId: string }) {
  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listUsers().then(
      (result) => {
        if (!cancelled) setUsers(result.items);
      },
      (caught: unknown) => {
        if (!cancelled) {
          setError(caught instanceof ApiError ? caught.message : "Could not load users.");
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, []);

  function replace(user: AdminUser) {
    setUsers((current) =>
      current ? current.map((existing) => (existing.id === user.id ? user : existing)) : current,
    );
  }

  return (
    <div className="stack">
      <section className="card stack" aria-labelledby="users-heading">
        <h2 id="users-heading">Users</h2>
        {error && (
          <p className="error-text" role="alert">
            {error}
          </p>
        )}
        {!users && !error && (
          <p className="muted" role="status">
            Loading…
          </p>
        )}
        {users && (
          <div className={styles.tableWrap}>
            <table className="table">
              <caption className="visually-hidden">Users of this customer</caption>
              <thead>
                <tr>
                  <th scope="col">Name</th>
                  <th scope="col">E-mail</th>
                  <th scope="col">Role</th>
                  <th scope="col">Status</th>
                  <th scope="col">Added</th>
                  <th scope="col">
                    <span className="visually-hidden">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
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
          </div>
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
      setError(caught instanceof ApiError ? caught.message : "Could not save the change.");
    } finally {
      setBusy(false);
    }
  }

  const selectId = `role-${user.id}`;
  return (
    <tr>
      <td className={styles.wrap}>
        {user.display_name}
        {isSelf && <span className="muted"> (you)</span>}
      </td>
      <td className={styles.wrap}>{user.email}</td>
      <td>
        <label htmlFor={selectId} className="visually-hidden">
          Role for {user.display_name}
        </label>
        <select
          id={selectId}
          className="input"
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
      <td>
        {user.disabled ? (
          <span className="badge badge-danger">Disabled</span>
        ) : (
          <span className="badge badge-verified">Active</span>
        )}
      </td>
      <td className={styles.nowrap}>{formatDate(user.created_at)}</td>
      <td>
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
          <span aria-live="polite" className="muted">
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

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setAdded(null);
    setServerError(null);
    const found = validateNewUser(input);
    setErrors(found);
    if (Object.keys(found).length > 0) return;
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
      const message = caught instanceof ApiError ? caught.message : "Could not add the user.";
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
      <h2 id="add-user-heading">Add a user</h2>
      <form className={styles.form} onSubmit={submit} noValidate>
        <Field
          id="new-user-email"
          label="E-mail"
          error={errors.email}
          type="email"
          autoComplete="off"
          value={input.email}
          onChange={(email) => setInput({ ...input, email })}
        />
        <Field
          id="new-user-name"
          label="Name"
          error={errors.display_name}
          type="text"
          autoComplete="off"
          value={input.display_name}
          onChange={(display_name) => setInput({ ...input, display_name })}
        />
        <div className={styles.field}>
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
        <Field
          id="new-user-password"
          label="Password (at least 12 characters)"
          error={errors.password}
          type="password"
          autoComplete="new-password"
          value={input.password}
          onChange={(password) => setInput({ ...input, password })}
        />
        <div className={styles.formEnd}>
          <button type="submit" className="btn btn-primary" disabled={busy}>
            {busy ? "Adding…" : "Add user"}
          </button>
          <span aria-live="polite" className="muted">
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
  autoComplete,
  value,
  onChange,
}: {
  id: string;
  label: string;
  error: string | undefined;
  type: "email" | "text" | "password";
  autoComplete: string;
  value: string;
  onChange: (value: string) => void;
}) {
  const errorId = `${id}-error`;
  return (
    <div className={styles.field}>
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        className="input"
        type={type}
        autoComplete={autoComplete}
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
