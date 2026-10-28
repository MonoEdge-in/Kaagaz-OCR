"use client";

// The account menu at the header's end (UI audit G5, G6): the avatar button
// opens a native popover (Esc or a click outside closes it) with who is signed
// in, their role and workspace, the theme switch, and the two ways to sign out:
// this browser only, or every browser and phone at once (PRD A4). Sign out is
// the menu's main button; Sign out everywhere, under it, a quiet destructive item
// (issue 373: it was a large red button, louder than Sign out) that asks first in
// a dialog that gives Cancel the focus. The arrow keys move into the menu from the
// avatar and between its actions, Home and End to the first and last, as a row's
// menu does (RowMenu's onMenuKey); a theme or palette radio keeps its own arrows.

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { menuItemAfterKey } from "@/components/documents/library";
import { clearStored } from "@/components/documents/batchTray";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { Icon } from "@/components/ui/Icon";
import { PalettePicker } from "@/components/ui/PalettePicker";
import { ThemeToggle } from "@/components/ui/ThemeToggle";
import { ApiError, logout, logoutEverywhere } from "@/lib/api";
import { announceSession, leavePage } from "@/lib/sessionChannel";
import type { Me } from "@/lib/types";
import { KEYCLOAK_SIGN_OUT, signedOutPath } from "./gate";
import { initials, roleName } from "./nav";
import styles from "./Shell.module.css";

const MENU_ID = "account-menu";

/** Which sign-out is running: this browser ("here") or all of them. */
type SignOutKind = "here" | "everywhere";

/** The menu's actions the arrow keys move between: its link and its buttons, in order. */
const ACTIONS = "a[href], button:not([disabled])";

/**
 * Where an arrow key, Home or End goes in the menu's actions from `current` (null: from
 * outside them, the avatar: Down or Home to the first, Up or End to the last); null for
 * any other key.
 */
export function menuKeyTarget<T>(items: readonly T[], current: T | null, key: string): T | null {
  const at = current === null ? -1 : items.indexOf(current);
  if (at < 0 && key === "ArrowUp") return items.at(-1) ?? null;
  const next = menuItemAfterKey(at, key, items.length);
  return next === null ? null : (items[next] ?? null);
}

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
    if (me.session.keycloak === true) leavePage(KEYCLOAK_SIGN_OUT); // on to Keycloak's sign-out (#435)
    else leavePage(signedOutPath(pathname));
  }

  // Issue 373: Down or Up on the avatar opens the menu and moves into it; inside, the arrow
  // keys, Home and End move between its actions. A radio's own arrows pick a theme or palette.
  function onKey(event: KeyboardEvent<HTMLElement>) {
    const menu = document.getElementById(MENU_ID);
    if (!menu) return;
    const from = event.target instanceof HTMLElement ? event.target : null;
    if (from instanceof HTMLInputElement && from.type === "radio") return;
    if (from === avatar.current && event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    const items = [...menu.querySelectorAll<HTMLElement>(ACTIONS)];
    const next = menuKeyTarget(items, from && menu.contains(from) ? from : null, event.key);
    if (next === null) return;
    event.preventDefault();
    if (!menu.matches(":popover-open")) menu.showPopover();
    next.focus();
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
        onKeyDown={onKey}
        aria-label={`Account menu, ${name}`}
        title={`Account menu, ${name}`}
      >
        <span className={styles.avatar} aria-hidden="true">
          {initials(name)}
        </span>
      </button>

      <div id={MENU_ID} popover="auto" className={`motion-pop ${styles.account}`} onKeyDown={onKey}>
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
            className={`btn btn-primary btn-block ${styles.signOut}`}
            onClick={() => void endSession("here")}
            disabled={signingOut !== null}
            aria-busy={signingOut === "here" || undefined}
          >
            <Icon name="logout" />
            <span>{signingOut === "here" ? "Signing out…" : "Sign out"}</span>
          </button>
          <button
            type="button"
            className={`btn btn-quiet btn-block ${styles.signOut} ${styles.signOutEverywhere}`}
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
