// What the signed-in person's session allows the app to show, read from Me in
// one place. Both reads deny by default: only scope "admin" is an admin
// session (anything else gets no Admin link), and only true asks for a new
// password. The server checks the same things on every request; this only
// picks the screen.

import type { Me, SessionScope } from "@/lib/types";

export function sessionScope(me: Me): SessionScope {
  return me.session.scope === "admin" ? "admin" : "app";
}

export function mustChangePassword(me: Me): boolean {
  return me.user.must_change_password === true;
}

/** "set_password": show only the set-new-password screen (PRD A5), in either scope. */
export type AppGate = "set_password" | "app";

export function appGate(me: Me): AppGate {
  return mustChangePassword(me) ? "set_password" : "app";
}

/**
 * Where to sign in when there is no session, or it has just ended: the admin
 * screens (/admin and below) go to the admin sign-in, every other page to /login.
 */
export function signInPath(pathname: string): string {
  return pathname === "/admin" || pathname.startsWith("/admin/") ? "/admin/login" : "/login";
}

/** The sign-in page after a session ended while a screen was open (it says so). */
export function endedPath(pathname: string): string {
  return `${signInPath(pathname)}?ended=1`;
}

/** The note on a sign-in page, from its query: ?signed_out=1 or ?ended=1. */
export function signedOutMessage(query: Record<string, string | string[] | undefined>): string | null {
  if (query.signed_out === "1") return "You signed out.";
  if (query.ended === "1") return "Your session ended. Sign in again to continue.";
  return null;
}

/**
 * Where a tab signed in as `mine` goes when another tab says `now` is signed
 * in (null: nobody). Null means stay: it is still the same person. Otherwise
 * the tab shows someone who is no longer signed in, so it loads a fresh page:
 * the sign-in page, or this page again as the person signed in now.
 */
export function pageAfterSessionChange(
  mine: string,
  now: string | null,
  here: { pathname: string; href: string },
): string | null {
  if (now === mine) return null;
  if (now === null) return `${signInPath(here.pathname)}?signed_out=1`;
  return here.href;
}
