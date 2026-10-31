"use client";

// Issue 468: while the browser says it has no connection, one line at the top of the app and
// the operator console says so. It listens to the browser's online and offline events (and
// reads navigator.onLine at the start) and goes away on reconnect. A polite live region, there
// from the start so the line is announced; it never takes focus. Nothing is cached for it:
// screens keep only what they already show.

import { useSyncExternalStore } from "react";
import { Icon } from "@/components/ui/Icon";
import styles from "./Shell.module.css";

export const OFFLINE_TEXT = "You're offline. Showing what was already loaded. Kaagaz will try again when you're back.";

function subscribe(onChange: () => void): () => void {
  window.addEventListener("online", onChange);
  window.addEventListener("offline", onChange);
  return () => {
    window.removeEventListener("online", onChange);
    window.removeEventListener("offline", onChange);
  };
}

/** Only an explicit false is offline: a browser that does not say counts as online. */
export const isOffline = (onLine: boolean | undefined) => onLine === false;

const offlineNow = () => isOffline(navigator.onLine);
const onServer = () => false;

export function OfflineBanner() {
  const offline = useSyncExternalStore(subscribe, offlineNow, onServer);
  return (
    <div role="status" aria-live="polite">
      {offline ? (
        <p className={styles.offlineBanner}>
          <Icon name="alert" />
          {OFFLINE_TEXT}
        </p>
      ) : null}
    </div>
  );
}
