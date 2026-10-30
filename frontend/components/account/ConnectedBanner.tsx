"use client";

// "Google connected on 30 Oct." at the top of every signed-in screen (#457 phase 1), for each
// account connected and approved in the last 14 days (Me's recent_connections): if it was not
// the person, they see it at once. Dismissed for good on this browser (localStorage, which
// may be off: then it shows again next time, which is safe).

import { useState } from "react";
import { IconButton } from "@/components/ui/IconButton";
import { bannerText } from "./signInWays";
import styles from "./WaysToSignIn.module.css";

type Recent = { provider: string; linked_at: string };

const key = (item: Recent) => `kaagaz.connected.${item.provider}.${item.linked_at}`;

function dismissed(item: Recent): boolean {
  try {
    return window.localStorage.getItem(key(item)) === "1";
  } catch {
    return false;
  }
}

export function ConnectedBanner({ items }: { items: readonly Recent[] }) {
  // Dismissed in this tab; the rest come from localStorage (the shell renders in the browser only).
  const [hidden, setHidden] = useState<readonly string[]>([]);
  const shown = items.filter((item) => !hidden.includes(key(item)) && !dismissed(item));

  function dismiss(item: Recent) {
    try {
      window.localStorage.setItem(key(item), "1");
    } catch {
      // not kept: it shows again next time
    }
    setHidden((was) => [...was, key(item)]);
  }

  return (
    <>
      {shown.map((item) => (
        <div key={key(item)} className={styles.banner} role="status">
          <p className={styles.bannerText}>{bannerText(item)}</p>
          <IconButton icon="close" label="Dismiss" onClick={() => dismiss(item)} />
        </div>
      ))}
    </>
  );
}
