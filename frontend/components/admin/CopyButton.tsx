"use client";

// Copies one piece of text (the 2FA set-up key, a temporary password) and
// says so in a polite live region. The button's own words never change, so a
// screen reader hears the result once. If the browser refuses (no permission,
// or not a secure page), it says to select and copy by hand instead.

import { useEffect, useState } from "react";
import styles from "./Admin.module.css";

type CopyState = "idle" | "copied" | "failed";

export function CopyButton({
  text,
  what,
  buttonText,
}: {
  text: string;
  /** What is copied, for the status line: "Key", "Password". */
  what: string;
  buttonText: string;
}) {
  const [state, setState] = useState<CopyState>("idle");

  // "Copied." clears after a few seconds, so an old status is never left behind.
  useEffect(() => {
    if (state !== "copied") return;
    const timer = window.setTimeout(() => setState("idle"), 4000);
    return () => window.clearTimeout(timer);
  }, [state]);

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setState("copied");
    } catch {
      setState("failed");
    }
  }

  return (
    <span className={styles.copy}>
      <button type="button" className="btn" onClick={copy}>
        {buttonText}
      </button>
      <span aria-live="polite" className={state === "failed" ? "error-text" : styles.saved}>
        {state === "copied" && `${what} copied.`}
        {state === "failed" && "Could not copy. Select the text and copy it yourself."}
      </span>
    </span>
  );
}
