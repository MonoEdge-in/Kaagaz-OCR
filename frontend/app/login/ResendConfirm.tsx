"use client";

// "Send the link again" (#457) for a sign-up's admin, who cannot sign in while the
// organisation waits for approval: on the sign-up's "Check your email" screen and on the
// sign-in page's "waiting for approval" answer. The server answers the same words whoever the
// address belongs to, and sends a link only to an unconfirmed admin of a waiting sign-up.

import { useState } from "react";
import { resendConfirmEmail } from "@/lib/api";
import { RESEND_SENT, linkError } from "@/lib/emailLinks";
import styles from "./login.module.css";

export function ResendConfirm({ email }: { email: string }) {
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState<string | null>(null);

  async function resend() {
    setBusy(true);
    try {
      await resendConfirmEmail(email);
      setSaid(RESEND_SENT);
    } catch (caught) {
      setSaid(linkError(caught));
    }
    setBusy(false);
  }

  return (
    <div className={styles.help}>
      <button
        type="button"
        className="btn btn-block"
        onClick={() => void resend()}
        disabled={busy}
        aria-busy={busy || undefined}
      >
        {busy && <span className="spinner" aria-hidden="true" />}
        Send the link again
      </button>
      <p role="status">{said}</p>
    </div>
  );
}
