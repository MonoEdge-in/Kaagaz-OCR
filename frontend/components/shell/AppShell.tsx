"use client";

// The frame around every signed-in screen, in fixed zones on one grid (brief C):
// the header (56 px, 48 px on a phone), then the side nav (240 px, from 1024 px
// up) beside <main>, the status bar (32 px, only while a screen has a line in
// it, StatusBar.tsx) and, on a phone, a bottom bar with the links. The page
// itself never scrolls: only <main> and the side nav do, each held in. Below
// 1024 px the header has a menu button that opens a Drawer with the same
// ShellMenu. Every list of links comes from the one navLinks.
// The header's end (UI audit G5): the documents search (Ctrl+K or Cmd+K
// focuses it; it opens /documents?q=..., the documents screen's own search),
// the Upload button and the account menu (AccountMenu).
// <main id="main"> has no padding; screens use .page (the chat fills it).

import Form from "next/form";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import type { MouseEvent, ReactNode } from "react";
import { MAX_SEARCH_LENGTH } from "@/components/documents/insideSearch";
import { Drawer } from "@/components/ui/Drawer";
import { Icon } from "@/components/ui/Icon";
import { IconButton } from "@/components/ui/IconButton";
import { useSession } from "@/lib/session";
import { AccountMenu } from "./AccountMenu";
import { sessionScope } from "./gate";
import { currentPageName, isCurrent, isSearchShortcut, navLinks } from "./nav";
import { ShellMenu } from "./ShellMenu";
import { StatusBarSlot } from "./StatusBar";
import { Wordmark } from "./Wordmark";
import styles from "./Shell.module.css";

const MENU_ID = "app-menu";

/** The documents screen's and the dashboard's file input (DocumentsView.tsx, Dashboard.tsx). */
const UPLOAD_INPUT_ID = "upload-input";

export function AppShell({ children }: { children: ReactNode }) {
  const { me } = useSession();
  const pathname = usePathname();
  const [menuOpen, setMenuOpen] = useState(false);
  const links = navLinks(me.user.role, sessionScope(me));
  const pageName = currentPageName(links, pathname);
  const [statusBar, setStatusBar] = useState<HTMLDivElement | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  // Ctrl+K (Cmd+K on a Mac) anywhere: to the header's search.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!isSearchShortcut(event)) return;
      event.preventDefault();
      searchRef.current?.focus();
      searchRef.current?.select();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // On the documents screen, Upload opens its file picker at once; elsewhere it
  // goes there, to the upload area and its Upload files button.
  function upload(event: MouseEvent<HTMLAnchorElement>) {
    const input = document.getElementById(UPLOAD_INPUT_ID);
    if (!(input instanceof HTMLInputElement)) return;
    event.preventDefault();
    input.click();
  }

  const menu = (
    <ShellMenu
      links={links}
      pathname={pathname}
      onLinkClick={() => setMenuOpen(false)}
    />
  );

  return (
    <>
      <a className={styles.skip} href="#main">
        Skip to content
      </a>
      <div className={`app-shell ${styles.app}`}>
        <header className={styles.header}>
          <IconButton
            icon="menu"
            label="Open menu"
            className={`btn-quiet ${styles.menuButton}`}
            aria-haspopup="dialog"
            aria-controls={MENU_ID}
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen(true)}
          />
          <Wordmark href="/dashboard" />
          {pageName && (
            <span className={styles.pageName}>
              <span className="visually-hidden">, </span>
              {pageName}
            </span>
          )}

          <div className={styles.headerEnd}>
            <Form action="/documents" role="search" className={styles.search}>
              <label htmlFor="app-search" className="visually-hidden">
                Find a document
              </label>
              <Icon name="search" className={styles.searchIcon} />
              <input
                ref={searchRef}
                id="app-search"
                name="q"
                className={`input ${styles.searchInput}`}
                type="search"
                enterKeyHint="search"
                autoComplete="off"
                maxLength={MAX_SEARCH_LENGTH}
                placeholder="Find a document"
                aria-keyshortcuts="Control+K Meta+K"
                title="Find a document by name (Ctrl+K, or Cmd+K on a Mac)"
              />
            </Form>
            <Link href="/documents" className={`btn btn-primary ${styles.upload}`} onClick={upload}>
              <Icon name="upload" />
              <span className="narrow-hide">Upload</span>
            </Link>
            <AccountMenu me={me} />
          </div>
        </header>

        <div className={styles.sidebar}>{menu}</div>

        <main
          id="main"
          className={styles.main}
          // A 1px edge under the header once the content has scrolled (UI audit G11).
          onScroll={(event) => event.currentTarget.toggleAttribute("data-scrolled", event.currentTarget.scrollTop > 0)}
        >
          <StatusBarSlot value={statusBar}>{children}</StatusBarSlot>
        </main>

        <div ref={setStatusBar} className={`app-statusbar ${styles.statusbar}`} />

        {/* A phone: the links at the thumb, the rest in the menu. */}
        <nav aria-label="Quick links" className={styles.bottombar}>
          {links.map((link) => {
            const current = isCurrent(link.href, pathname);
            return (
              <Link
                key={link.href}
                href={link.href}
                className={styles.bottomLink}
                aria-current={current ? "page" : undefined}
              >
                <Icon name={link.icon} />
                <span>{link.label}</span>
              </Link>
            );
          })}
        </nav>
      </div>

      <Drawer id={MENU_ID} title="Menu" open={menuOpen} onClose={() => setMenuOpen(false)}>
        {menu}
      </Drawer>
    </>
  );
}
