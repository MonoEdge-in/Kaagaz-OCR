// What the signed-in person's session allows the app to show, read from Me in
// one place. The reads deny by default: only scope "admin" is an admin
// session (anything else gets no Admin link), only true asks for a new
// password, and only true is an admin's two-step sign-in. The server checks the same things on every request; this only
// picks the screen.

import type { Me, SessionScope } from "@/lib/types";

export function sessionScope(me: Me): SessionScope {
  return me.session.scope === "admin" ? "admin" : "app";
}

export function mustChangePassword(me: Me): boolean {
  return me.user.must_change_password === true;
}

/**
 * "set_password": show only the set-new-password screen (PRD A5), in either scope.
 * "set_two_step": an admin without two-step sign-in (MFA required, 16 Oct 2026); the
 * server refuses everything else, so the browser signs out and the sign-in sets it up.
 */
export type AppGate = "set_password" | "set_two_step" | "app";

export function appGate(me: Me): AppGate {
  if (mustChangePassword(me)) return "set_password";
  return me.user.role === "admin" && me.user.totp_enabled !== true ? "set_two_step" : "app";
}

/**
 * Where to sign in when there is no session, or it has just ended: /login, the one
 * sign-in (16 Oct 2026). From the admin screens (/admin and below) it comes back to /admin.
 */
export function signInPath(pathname: string): string {
  return pathname === "/admin" || pathname.startsWith("/admin/") ? "/login?next=/admin" : "/login";
}

function withNote(path: string, note: string): string {
  return `${path}${path.includes("?") ? "&" : "?"}${note}=1`;
}

/** The sign-in page after a session ended while a screen was open (it says so). */
export function endedPath(pathname: string): string {
  return withNote(signInPath(pathname), "ended");
}

/** The sign-in page after signing out (it says so). */
export function signedOutPath(pathname: string): string {
  return withNote(signInPath(pathname), "signed_out");
}

/** The sign-in page for an admin who must set up two-step sign-in first (it says so). */
export function twoStepPath(pathname: string): string {
  return withNote(signInPath(pathname), "two_step");
}

/** The note on a sign-in page, from its query: ?signed_out=1, ?ended=1 or ?two_step=1. */
export function signedOutMessage(query: Record<string, string | string[] | undefined>): string | null {
  if (query.signed_out === "1") return "You signed out.";
  if (query.ended === "1") return "Your session ended. Sign in again to continue.";
  if (query.two_step === "1") {
    return "Set up two-step sign-in to continue. Admins need it to keep your organisation safe. Sign in to start.";
  }
  return null;
}

/** Where a sign-in goes: /admin when it came from there (?next=/admin), else the dashboard,
 * the home screen (File management D1). Only these two, so the address can never send anyone
 * to another site. */
export function pathAfterSignIn(query: Record<string, string | string[] | undefined>): string {
  return query.next === "/admin" ? "/admin" : "/dashboard";
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
  if (now === null) return signedOutPath(here.pathname);
  return here.href;
}
