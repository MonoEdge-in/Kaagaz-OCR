"use client";

// The account menu at the header's end (UI audit G5, G6): the avatar button
// opens a native popover (Esc or a click outside closes it) with who is signed
// in, their role and workspace, the theme switch, and the two ways to sign out:
// this browser only, or every browser and phone at once (PRD A4). Sign out
// everywhere is destructive, in its own row, and asks first in a dialog that
// gives Cancel the focus.

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useRef, useState } from "react";
import { clearStored } from "@/components/documents/batchTray";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { Icon } from "@/components/ui/Icon";
import { PalettePicker } from "@/components/ui/PalettePicker";
import { ThemeToggle } from "@/components/ui/ThemeToggle";
import { ApiError, logout, logoutEverywhere } from "@/lib/api";
import { announceSession, leavePage } from "@/lib/sessionChannel";
import type { Me } from "@/lib/types";
import { signedOutPath } from "./gate";
import { initials, roleName } from "./nav";
import styles from "./Shell.module.css";

const MENU_ID = "account-menu";

/** Which sign-out is running: this browser ("here") or all of them. */
type SignOutKind = "here" | "everywhere";

export function AccountMenu({ me }: { me: Me }) {
  const pathname = usePathname();
  const avatar = useRef<HTMLButtonElement>(null);
  const [signingOut, setSigningOut] = useState<SignOutKind | null>(null);
  const [failure, setFailure] = useState<{ kind: SignOutKind; message: string } | null>(null);
  const [confirming, setConfirming] = useState(false);
  const name = me.user.display_name;

  // "everywhere" raises the account's session version on the server, which
  // ends this browser's session too, so both end on the sign-in page. It is a
  // full page load, so nothing of this person stays in the tab's memory, and
  // the other tabs are told (lib/sessionChannel.ts).
  async function endSession(kind: SignOutKind) {
    setSigningOut(kind);
    setFailure(null);
    try {
      await (kind === "everywhere" ? logoutEverywhere() : logout());
    } catch (caught) {
      // Already signed out on the server (401): go to the sign-in page anyway.
      if (!(caught instanceof ApiError && caught.status === 401)) {
        setFailure({
          kind,
          message:
            caught instanceof ApiError && caught.status !== 0
              ? "Could not sign out. Try again in a minute."
              : "Could not reach Kaagaz to sign out. Check your internet connection and try again.",
        });
        setSigningOut(null);
        return;
      }
    }
    clearStored(); // the upload tray's drops (PRD 5 U1) are this person's
    announceSession(null);
    leavePage(signedOutPath(pathname));
  }

  function closeConfirm() {
    setConfirming(false);
    setFailure(null);
    avatar.current?.focus(); // the menu that opened the dialog has closed
  }

  return (
    <>
      <button
        ref={avatar}
        type="button"
        className={`btn btn-quiet ${styles.avatarButton}`}
        popoverTarget={MENU_ID}
        aria-label={`Account menu, ${name}`}
        title={`Account menu, ${name}`}
      >
        <span className={styles.avatar} aria-hidden="true">
          {initials(name)}
        </span>
      </button>

      <div id={MENU_ID} popover="auto" className={`motion-pop ${styles.account}`}>
        <div className={styles.user}>
          <p className={styles.userName}>{name}</p>
          <p className={styles.userMeta}>Role: {roleName(me.user.role)}</p>
          <p className={styles.userMeta}>Workspace: {me.customer.name}</p>
        </div>

        <ThemeToggle />
        {/* Tushar, 25 Oct 2026: six colour palettes, for this person only, beside the theme. */}
        <PalettePicker />

        {/* Spec 17 Oct 2026: the member's own AI settings, within what the admin left open. */}
        <Link
          href="/settings/ai"
          className={`btn btn-quiet btn-block ${styles.signOut}`}
          onClick={() => document.getElementById(MENU_ID)?.hidePopover()}
        >
          <Icon name="admin" />
          <span>AI settings</span>
        </Link>

        <div className={styles.signOuts}>
          <button
            type="button"
            className={`btn btn-quiet btn-block ${styles.signOut}`}
            onClick={() => void endSession("here")}
            disabled={signingOut !== null}
            aria-busy={signingOut === "here" || undefined}
          >
            <Icon name="logout" />
            <span>{signingOut === "here" ? "Signing out…" : "Sign out"}</span>
          </button>
          <button
            type="button"
            className={`btn btn-destructive btn-block ${styles.signOut}`}
            onClick={() => setConfirming(true)}
            disabled={signingOut !== null}
          >
            <Icon name="logout" />
            <span>Sign out everywhere</span>
          </button>
        </div>
        {failure?.kind === "here" && (
          <p className="error-text" role="alert">
            {failure.message}
          </p>
        )}
      </div>

      <ConfirmDialog
        open={confirming}
        title="Sign out everywhere?"
        confirmLabel="Sign out everywhere"
        busy={signingOut === "everywhere"}
        error={failure?.kind === "everywhere" ? failure.message : null}
        onConfirm={() => void endSession("everywhere")}
        onCancel={closeConfirm}
      >
        <p>
          This signs you out on every phone and computer, this one too. You will need to sign in
          again on each one.
        </p>
      </ConfirmDialog>
    </>
  );
}
