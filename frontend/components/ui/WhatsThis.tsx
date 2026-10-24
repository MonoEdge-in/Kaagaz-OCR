"use client";

// "What's this?" beside a setting or feature name (plain words, 25 Oct 2026): a small quiet
// button that shows or hides one plain sentence under it. A real <button> with
// aria-expanded and aria-controls, so Enter, Space and a tap all work and a screen reader
// hears whether it is open; never a hover-only tooltip. Use it where there is no room for
// the sentence to be shown all the time; where there is room, show it as a .hint instead.

import { useId, useState } from "react";
import styles from "./WhatsThis.module.css";

export function WhatsThis({ topic, text }: { topic: string; text: string }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <span className={styles.whatsThis}>
      <button
        type="button"
        className={styles.toggle}
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((shown) => !shown)}
      >
        What&apos;s this?<span className="visually-hidden"> {topic}</span>
      </button>
      <span id={id} className={styles.text} hidden={!open}>
        {text}
      </span>
    </span>
  );
}
