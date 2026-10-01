"use client";

// The note on a sign-in page after signing out or a session ending (from
// ?signed_out=1 or ?ended=1: signedOutMessage, components/shell/gate.ts). It
// is filled in once the page runs, so screen readers announce it
// (role="status"), and the address loses its query, so a reload does not
// say it again.

import { useEffect } from "react";
import { useHydrated } from "@/components/ui/useHydrated";
import styles from "./login.module.css";

export function SignedOutNotice({ message }: { message: string | null }) {
  const hydrated = useHydrated();
  useEffect(() => {
    if (message !== null) window.history.replaceState(null, "", window.location.pathname);
  }, [message]);
  if (message === null) return null;
  return (
    <p role="status" className={hydrated ? styles.notice : undefined}>
      {hydrated ? message : null}
    </p>
  );
}
