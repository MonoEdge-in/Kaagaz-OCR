"use client";

import { useEffect, useState } from "react";
import { isKeycloakAction, keycloakCallbackError, keycloakReauthNotice } from "@/lib/keycloak";
import styles from "./Shell.module.css";

/** Explains that the callback proved identity only; the user must repeat and confirm the action. */
export function KeycloakReauthNotice() {
  const [notice, setNotice] = useState<{ text: string; error: boolean } | null>(null);

  useEffect(() => {
    const url = new URL(window.location.href);
    const error = url.searchParams.get("reauth_error");
    const action = url.searchParams.get("reauth");
    const message = error
      ? keycloakCallbackError(error)
      : isKeycloakAction(action)
        ? keycloakReauthNotice(action)
        : null;
    if (!message) return;
    url.searchParams.delete("reauth");
    url.searchParams.delete("reauth_error");
    window.history.replaceState(window.history.state, "", `${url.pathname}${url.search}${url.hash}`);
    const timer = window.setTimeout(() => setNotice({ text: message, error: Boolean(error) }), 0);
    return () => window.clearTimeout(timer);
  }, []);

  if (!notice) return null;
  return (
    <p className={`${styles.reauthNotice}${notice.error ? ` ${styles.reauthError}` : ""}`} role={notice.error ? "alert" : "status"}>
      {notice.text}
    </p>
  );
}
