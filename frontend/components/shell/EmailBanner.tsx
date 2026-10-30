"use client";

// "Confirm your email, {email}. [Send link]" (#457 phase 0), at the top of every signed-in
// screen while the person's email is not confirmed; once sent, "Confirm your email. We sent a
// link to {email}. [Send again]". A banner, not a block: people made before email was sent are
// asked, never locked out. The server sends at most 3 links an hour; a refusal, or a send that
// failed (``sent: false``), says so in words.

import { useState } from "react";
import { Icon } from "@/components/ui/Icon";
import { sendConfirmEmail } from "@/lib/api";
import { SEND_FAILED, confirmBanner, linkError } from "@/lib/emailLinks";
import styles from "./Shell.module.css";

export function EmailBanner({ email }: { email: string }) {
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState<string | null>(null);
  const [sent, setSent] = useState(false);
  const copy = confirmBanner(email, sent);

  async function sendAgain() {
    setBusy(true);
    try {
      const reply = await sendConfirmEmail();
      if (reply.sent) setSent(true);
      setSaid(reply.sent ? null : SEND_FAILED);
    } catch (caught) {
      setSaid(linkError(caught));
    }
    setBusy(false);
  }

  return (
    <div className={styles.emailBanner} role="region" aria-label="Confirm your email">
      <Icon name="alert" />
      <p className={styles.emailBannerText}>{copy.text}</p>
      <button type="button" className="btn" onClick={() => void sendAgain()} disabled={busy} aria-busy={busy || undefined}>
        {busy && <span className="spinner" aria-hidden="true" />}
        {copy.button}
      </button>
      <p className={styles.emailBannerSaid} role="status">
        {said}
      </p>
    </div>
  );
}
