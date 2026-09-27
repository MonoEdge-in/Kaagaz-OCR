"use client";

// The sign-in form. The backend gives the same short message for an unknown
// e-mail, a wrong password or a disabled account, and we show it as is.

import { useRouter } from "next/navigation";
import { useState } from "react";
import type { FormEvent } from "react";
import { ApiError, login } from "@/lib/api";
import styles from "./login.module.css";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email.trim(), password);
      router.replace("/documents");
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not reach the server.");
      setBusy(false);
    }
  }

  return (
    <main className={styles.page}>
      <form className={`card stack ${styles.form}`} onSubmit={submit} noValidate>
        <div>
          <h1>Kaagaz</h1>
          <p className="muted">Sign in to ask your documents.</p>
        </div>
        <div className={styles.field}>
          <label htmlFor="login-email">E-mail</label>
          <input
            id="login-email"
            className="input"
            type="email"
            autoComplete="username"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            aria-describedby={error ? "login-error" : undefined}
          />
        </div>
        <div className={styles.field}>
          <label htmlFor="login-password">Password</label>
          <input
            id="login-password"
            className="input"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            aria-describedby={error ? "login-error" : undefined}
          />
        </div>
        <p id="login-error" className="error-text" role="alert">
          {error ?? ""}
        </p>
        <button
          type="submit"
          className="btn btn-primary"
          disabled={busy || email.trim() === "" || password === ""}
        >
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </main>
  );
}
