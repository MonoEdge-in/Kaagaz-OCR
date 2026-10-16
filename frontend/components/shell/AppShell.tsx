"use client";

// The frame around every signed-in screen. From 1024 px up: a sidebar down the
// whole page, with the wordmark and ShellMenu sticking to the window. Below that: a sticky top bar (menu button,
// wordmark, the current page's name) and a Drawer with the same ShellMenu.
// Both get their links from the one navLinks list for the whole shell.
// <main id="main"> has no padding; screens use .page (the chat fills it).

import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { clearStored } from "@/components/documents/batchTray";
import { Drawer } from "@/components/ui/Drawer";
import { IconButton } from "@/components/ui/IconButton";
import { ApiError, listReview, logout, logoutEverywhere } from "@/lib/api";
import { useSession } from "@/lib/session";
import { announceSession, leavePage } from "@/lib/sessionChannel";
import { sessionScope, signedOutPath } from "./gate";
import { currentPageName, navLinks } from "./nav";
import { ShellMenu } from "./ShellMenu";
import type { SignOutKind } from "./ShellMenu";
import { Wordmark } from "./Wordmark";
import styles from "./Shell.module.css";

const MENU_ID = "app-menu";

export function AppShell({ children }: { children: ReactNode }) {
  const { me } = useSession();
  const pathname = usePathname();
  const [menuOpen, setMenuOpen] = useState(false);
  const [signingOut, setSigningOut] = useState<SignOutKind | null>(null);
  const [signOutError, setSignOutError] = useState<string | null>(null);
  const links = navLinks(me.user.role, sessionScope(me));
  const pageName = currentPageName(links, pathname);
  const showsToCheck = links.some((link) => link.toCheck);
  const [toCheck, setToCheck] = useState(0);

  // The Review link's count, asked again on every page change (one row, so it is
  // cheap; no timer of its own). A failed ask keeps the last count: it is a hint,
  // the Review page itself has the real list.
  useEffect(() => {
    if (!showsToCheck) return;
    let cancelled = false;
    listReview({ limit: 1 }).then(
      (list) => {
        if (!cancelled) setToCheck(list.total);
      },
      () => undefined,
    );
    return () => {
      cancelled = true;
    };
  }, [showsToCheck, pathname]);

  // "everywhere" raises the account's session version on the server, which
  // ends this browser's session too, so both end on the sign-in page. It is a
  // full page load, so nothing of this person stays in the tab's memory, and
  // the other tabs are told (lib/sessionChannel.ts).
  async function endSession(kind: SignOutKind) {
    setSigningOut(kind);
    setSignOutError(null);
    try {
      await (kind === "everywhere" ? logoutEverywhere() : logout());
    } catch (caught) {
      // Already signed out on the server (401): go to the sign-in page anyway.
      if (!(caught instanceof ApiError && caught.status === 401)) {
        setSignOutError(
          caught instanceof ApiError && caught.status !== 0
            ? "Could not sign out. Try again in a minute."
            : "Could not reach Kaagaz to sign out. Check your internet connection and try again.",
        );
        setSigningOut(null);
        return;
      }
    }
    clearStored(); // the upload tray's drops (PRD 5 U1) are this person's
    announceSession(null);
    leavePage(signedOutPath(pathname));
  }

  const signOut = () => endSession("here");
  // The stronger sign-out sits under the plain one: ask first, so a slip costs nothing.
  const signOutEverywhere = () => {
    if (window.confirm("Sign out on every phone and computer? You will need to sign in again on each one.")) {
      void endSession("everywhere");
    }
  };

  const menu = (
    <ShellMenu
      links={links}
      pathname={pathname}
      me={me}
      toCheck={showsToCheck ? toCheck : 0}
      signingOut={signingOut}
      signOutError={signOutError}
      onSignOut={signOut}
      onSignOutEverywhere={signOutEverywhere}
      onLinkClick={() => setMenuOpen(false)}
    />
  );

  return (
    <>
      <a className={styles.skip} href="#main">
        Skip to content
      </a>
      <div className={styles.frame}>
        <header className={styles.topbar}>
          <IconButton
            icon="menu"
            label="Open menu"
            className="btn-quiet"
            aria-haspopup="dialog"
            aria-controls={MENU_ID}
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen(true)}
          />
          <Wordmark href="/documents" />
          {pageName && (
            <span className={styles.pageName}>
              <span className="visually-hidden">, </span>
              {pageName}
            </span>
          )}
        </header>

        <div className={styles.sidebar}>
          <div className={styles.sidebarInner}>
            <div className={styles.sidebarBrand}>
              <Wordmark href="/documents" />
            </div>
            {menu}
          </div>
        </div>

        <main id="main" className={styles.main}>
          {children}
        </main>
      </div>

      <Drawer id={MENU_ID} title="Menu" open={menuOpen} onClose={() => setMenuOpen(false)}>
        {menu}
      </Drawer>
    </>
  );
}
