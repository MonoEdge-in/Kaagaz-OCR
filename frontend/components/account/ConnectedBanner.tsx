"use client";

// "Google connected on 30 Oct. Not you? Disconnect it under Ways to sign in." at the top of
// every signed-in screen (#457 phase 1), for each
// account connected and approved in the last 14 days (Me's recent_connections): if it was not
// the person, they see it at once. Dismissed for good on this browser (localStorage, which
// may be off: then it shows again next time, which is safe).

import Link from "next/link";
import { useState } from "react";
import { IconButton } from "@/components/ui/IconButton";
import { LINK_REQUIRED, bannerText } from "./signInWays";
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
          <p className={styles.bannerText}>
            {bannerText(item)} Not you? Disconnect it under <Link href="/settings/sign-in">Ways to sign in</Link>.
          </p>
          <IconButton icon="close" label="Dismiss" onClick={() => dismiss(item)} />
        </div>
      ))}
    </>
  );
}

/** #507: a session that may only connect Google first (me.google_link_required), at the top of
 * Ways to sign in, where the app sends the person. Never dismissed: it ends with the first
 * Google account that passes the organisation's rules. */
export function LinkRequiredBanner() {
  return (
    <div className={styles.banner} role="status">
      <p className={styles.bannerText}>{LINK_REQUIRED}</p>
    </div>
  );
}
