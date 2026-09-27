"use client";

// The frame around every signed-in screen. From 1024 px up: a sticky sidebar
// with the wordmark and ShellMenu. Below that: a sticky top bar (menu button,
// wordmark, the current page's name) and a Drawer with the same ShellMenu.
// Both get their links from the one navLinks list for the whole shell.
// <main id="main"> has no padding; screens use .page (the chat fills it).

import { usePathname, useRouter } from "next/navigation";
import { useState } from "react";
import type { ReactNode } from "react";
import { Drawer } from "@/components/ui/Drawer";
import { IconButton } from "@/components/ui/IconButton";
import { ApiError, logout } from "@/lib/api";
import { useSession } from "@/lib/session";
import { currentPageName, navLinks } from "./nav";
import { ShellMenu } from "./ShellMenu";
import { Wordmark } from "./Wordmark";
import styles from "./Shell.module.css";

const MENU_ID = "app-menu";

export function AppShell({ children }: { children: ReactNode }) {
  const { me } = useSession();
  const pathname = usePathname();
  const router = useRouter();
  const [menuOpen, setMenuOpen] = useState(false);
  const [signingOut, setSigningOut] = useState(false);
  const [signOutError, setSignOutError] = useState<string | null>(null);
  const links = navLinks(me.user.role);
  const pageName = currentPageName(links, pathname);

  async function signOut() {
    setSigningOut(true);
    setSignOutError(null);
    try {
      await logout();
      router.replace("/login");
    } catch (caught) {
      // Already signed out on the server: go to the sign-in page anyway.
      if (caught instanceof ApiError && caught.status === 401) {
        router.replace("/login");
        return;
      }
      setSignOutError(
        caught instanceof ApiError && caught.status !== 0
          ? "Could not sign out. Try again in a minute."
          : "Could not reach Kaagaz to sign out. Check your internet connection and try again.",
      );
      setSigningOut(false);
    }
  }

  const menu = (
    <ShellMenu
      links={links}
      pathname={pathname}
      me={me}
      signingOut={signingOut}
      signOutError={signOutError}
      onSignOut={signOut}
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
          <div className={styles.sidebarBrand}>
            <Wordmark href="/documents" />
          </div>
          {menu}
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
