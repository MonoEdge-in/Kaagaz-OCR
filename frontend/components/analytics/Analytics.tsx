"use client";

// The Google Analytics choice on the public pages (#457 phase 4), mounted by their frame
// (app/login/AuthFrame.tsx) only when GA_ID is set. Nothing loads until Accept;
// Accept and Decline are remembered on this browser (ga.ts). The banner sits in the
// page under the form, so it never covers the form's buttons.

import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { Button, ButtonGroup } from "@/components/ui/Button/Button";
import { useHydrated } from "@/components/ui/useHydrated";
import { BANNER_TEXT, readConsent, saveConsent, startGtag, viewFor } from "./ga";
import type { Consent } from "./ga";
import styles from "./Analytics.module.css";

const storage = () => window.localStorage;

export function Analytics({ id }: { id: string }) {
  // The choice is read in the browser only, so the server's page never shows the banner.
  const hydrated = useHydrated();
  const pathname = usePathname() ?? "";
  // With the query string, read in the browser only: /login?step=code is never tracked.
  const path = hydrated ? pathname + window.location.search : pathname;
  const [chosen, setChosen] = useState<Consent | null>(null);
  const view = hydrated ? viewFor(id, path, chosen ?? readConsent(storage)) : "off";

  useEffect(() => {
    if (view === "on") startGtag(window, document, id, path);
  }, [view, id, path]);

  if (view !== "ask") return null;

  function choose(consent: Consent) {
    saveConsent(storage, consent);
    setChosen(consent);
  }

  return (
    <section className={styles.banner} aria-label="Google Analytics">
      <p className={styles.text}>{BANNER_TEXT}</p>
      <ButtonGroup>
        <Button onClick={() => choose("accepted")}>Accept analytics</Button>
        <Button onClick={() => choose("declined")}>Decline analytics</Button>
      </ButtonGroup>
    </section>
  );
}
