"use client";

// The frame around every signed-in screen, in the handoff's layout (desktop/Home.html,
// phone/PhoneHome.html) on one grid: from 1024 px the sidebar (244 px) runs the
// full height with the brand, the links and the workspace, and the top bar (68 px)
// sits over <main> beside it; the status bar (32 px, only while a screen has a line
// in it, StatusBar.tsx) and, on a phone, the tab bar with the links. The page itself
// never scrolls: only <main> and the sidebar do, each held in. Below 1024 px the top
// bar has the brand and a menu button that opens a Drawer with the same ShellMenu.
// Every list of links comes from the one navLinks. The whole frame is a .k-root, so
// the handoff's base styles and the UI kit apply inside it.
// The top bar's end: the search (Ctrl+K or Cmd+K focuses it; it opens /search?q=...,
// inside documents first, docs/architecture.md, The screens), the Upload button and the account
// menu (AccountMenu).
// <main id="main"> has no padding; screens use .page (the chat fills it).
// #457: while the person's email is not confirmed, EmailBanner sits at the top of <main>;
// an account connected in the last 14 days, ConnectedBanner (phase 1).
// Issue 468: above them, while the browser has no connection, OfflineBanner.
// #507: a session that may only connect Google (lib/session.tsx keeps it on Ways to sign in)
// says why, LinkRequiredBanner, and shows no links, search or Upload (appChrome, #507 review).

import Form from "next/form";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import type { MouseEvent, ReactNode } from "react";
import { MAX_SEARCH_LENGTH } from "@/components/documents/insideSearch";
import { Drawer } from "@/components/ui/Drawer";
import { Icon } from "@/components/ui/Icon";
import { IconButton } from "@/components/ui/IconButton";
import { ConnectedBanner, LinkRequiredBanner } from "@/components/account/ConnectedBanner";
import { canReview } from "@/components/review/review";
import { listReview } from "@/lib/api";
import { useSession } from "@/lib/session";
import { AccountMenu } from "./AccountMenu";
import { appChrome, sessionScope } from "./gate";
import { currentPageName, isCurrent, isSearchShortcut, navLinks, reviewCountText, roleName } from "./nav";
import { ShellMenu } from "./ShellMenu";
import { StatusBarSlot } from "./StatusBar";
import { SHELL_UPLOAD_INPUT_ID, UploadsProvider } from "./Uploads";
import { ClockPeek } from "./ClockPeek";
import { EmailBanner } from "./EmailBanner";
import { OfflineBanner } from "./OfflineBanner";
import { Wordmark } from "./Wordmark";
import styles from "./Shell.module.css";

const MENU_ID = "app-menu";
const NONE: readonly { provider: string; linked_at: string }[] = [];

/** The documents screen's and the dashboard's file input (DocumentsView.tsx, Dashboard.tsx). */
const UPLOAD_INPUT_ID = "upload-input";

export function AppShell({ children }: { children: ReactNode }) {
  const { me, refresh } = useSession();
  const pathname = usePathname();
  const [menuOpen, setMenuOpen] = useState(false);
  const chrome = appChrome(me);
  const links = chrome ? navLinks(me.user.role, sessionScope(me)) : [];
  const pageName = currentPageName(links, pathname);
  const [statusBar, setStatusBar] = useState<HTMLDivElement | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  // D-7: a reviewer's and an admin's Review link counts the values waiting there, asked
  // again on each page change (a value checked in Review lowers it). A member never asks.
  const reviewer = chrome && canReview(me.user.role);
  const [reviewTotal, setReviewTotal] = useState<number | null>(null);
  useEffect(() => {
    if (!reviewer) return;
    let cancelled = false;
    listReview({ limit: 1 }).then(
      (reply) => {
        if (!cancelled) setReviewTotal(reply.total);
      },
      () => undefined, // no count is shown rather than a wrong one
    );
    return () => {
      cancelled = true;
    };
  }, [reviewer, pathname]);
  const reviewCount = reviewer ? reviewCountText(reviewTotal) : null;

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

  // Upload opens a file picker on every page (D-12): the screen's own on the documents
  // screen and the dashboard (into the folder or department shown), the shell's
  // elsewhere; the files go to the one tray in the status bar (Uploads.tsx).
  function upload(event: MouseEvent<HTMLAnchorElement>) {
    const input = document.getElementById(UPLOAD_INPUT_ID) ?? document.getElementById(SHELL_UPLOAD_INPUT_ID);
    if (!(input instanceof HTMLInputElement)) return;
    event.preventDefault();
    input.click();
  }

  const menu = (
    <ShellMenu
      links={links}
      pathname={pathname}
      onLinkClick={() => setMenuOpen(false)}
      reviewCount={reviewCount}
    />
  );

  return (
    <>
      <a className={styles.skip} href="#main">
        Skip to content
      </a>
      <div className={`app-shell k-root ${styles.app}`}>
        <header className={styles.header}>
          {chrome && (
            <IconButton
              icon="menu"
              label="Open menu"
              className={`btn-quiet ${styles.menuButton}`}
              aria-haspopup="dialog"
              aria-controls={MENU_ID}
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen(true)}
            />
          )}
          <span className={styles.headerBrand}>
            <Wordmark href="/dashboard" />
          </span>
          {pageName && (
            <span className={styles.pageName}>
              <span className="visually-hidden">, </span>
              {pageName}
            </span>
          )}

          <div className={styles.headerEnd}>
            {chrome && (
              <>
                <Form action="/search" role="search" className={styles.search}>
                  <label htmlFor="app-search" className="visually-hidden">
                    Search your documents
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
                    placeholder="Search inside documents, or a name"
                    aria-keyshortcuts="Control+K Meta+K"
                    title="Search your documents (Ctrl+K, or Cmd+K on a Mac)"
                  />
                </Form>
                <Link href="/documents" className={`btn btn-primary ${styles.upload}`} onClick={upload}>
                  <Icon name="upload" />
                  <span className="narrow-hide">Upload</span>
                </Link>
              </>
            )}
            <AccountMenu me={me} onChanged={refresh} />
          </div>
        </header>

        <div className={styles.sidebar}>
          <div className={styles.brand}>
            <Wordmark href="/dashboard" />
            <ClockPeek />
          </div>
          {chrome && menu}
          <div className={styles.sideFoot}>
            <p className={styles.sideFootName}>{me.customer.name}</p>
            <p>
              {me.user.display_name} · {roleName(me.user.role)}
            </p>
          </div>
        </div>

        <main
          id="main"
          className={styles.main}
          // A 1px edge under the header once the content has scrolled (UI audit G11).
          onScroll={(event) => event.currentTarget.toggleAttribute("data-scrolled", event.currentTarget.scrollTop > 0)}
        >
          <OfflineBanner />
          {!me.user.email_confirmed && <EmailBanner email={me.user.email} />}
          {me.user.google_link_required && <LinkRequiredBanner unchecked={me.user.google_unchecked === true} />}
          <ConnectedBanner items={me.recent_connections ?? NONE} />
          <StatusBarSlot value={statusBar}>
            <UploadsProvider>{children}</UploadsProvider>
          </StatusBarSlot>
        </main>

        <div ref={setStatusBar} className={`app-statusbar ${styles.statusbar}`} />

        {/* A phone: the links at the thumb, the rest in the menu. */}
        {chrome && (
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
                  <span className={styles.bottomIcon}>
                    <Icon name={link.icon} />
                    {link.count === "review" && reviewCount !== null && (
                      <span className={styles.bottomCount}>
                        {reviewCount}
                        <span className="visually-hidden"> to review</span>
                      </span>
                    )}
                  </span>
                  <span>{link.label}</span>
                </Link>
              );
            })}
          </nav>
        )}
      </div>

      <Drawer id={MENU_ID} title="Menu" open={menuOpen} onClose={() => setMenuOpen(false)}>
        {menu}
      </Drawer>
    </>
  );
}
