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
 * Not an admin who turned their own off (Tushar, 30 Oct 2026): it stays off until they
 * turn it on from the Account menu. Only true counts as turned off.
 * "connect_google" (#507): the organisation asks this person to sign in with Google and they
 * have no Google account that passes its rules yet; the server refuses every route but Ways to
 * sign in (WAYS_PATH), so the app shows only that page. Only true counts.
 */
export type AppGate = "set_password" | "set_two_step" | "connect_google" | "app";

/** Account, Ways to sign in. */
export const WAYS_PATH = "/settings/sign-in";

export function appGate(me: Me): AppGate {
  if (mustChangePassword(me)) return "set_password";
  const needsTwoStep = me.user.totp_enabled !== true && me.user.two_step_turned_off !== true;
  if (me.user.role === "admin" && needsTwoStep) return "set_two_step";
  return me.google_link_required === true ? "connect_google" : "app";
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

/**
 * The sign-in page after a session ended while a screen was open (it says so). Signing in comes
 * back to the screen's path (issue 468), when it is a safe one. The path only, never its query:
 * the address is left in the tab, and a query holds the person's own words (a search, a
 * question), which the next person to sign in on this tab must not get. A search's words are
 * kept as that person's draft instead (searchToKeep).
 */
export function endedPath(place: string): string {
  const path = place.split(/[?#]/, 1)[0] ?? "";
  if (safeNextPath(path) === null) return withNote(signInPath(path), "ended");
  return `/login?next=${encodeURIComponent(path)}&ended=1`;
}

/** The draft screen that keeps a search's words when a session ends on /search (SEC1, #468). */
export const SEARCH_DRAFT = "search";

/** The words of the search open at a session's end, for that person's draft, or null. */
export function searchToKeep(pathname: string, search: string): { q: string } | null {
  if (pathname !== "/search") return null;
  const q = (new URLSearchParams(search).get("q") ?? "").trim();
  return q ? { q } : null;
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

/**
 * A path on this site to come back to after signing in, or null (issue 468). Only a path that
 * starts with one "/": never "//" or "/\" (another site), no backslash or control character
 * anywhere (a browser drops a tab or new line, turning "/\t/x" into "//x"), and no ":" before
 * its query (no scheme). Each layer of %-encoding of the path is checked the same way.
 */
export function safeNextPath(next: unknown): string | null {
  if (typeof next !== "string" || next.length > MAX_NEXT_LENGTH || hasUnsafeCharacter(next)) return null;
  let route = next.split(/[?#]/, 1)[0] ?? "";
  for (let layer = 0; layer < MAX_ENCODINGS; layer++) {
    if (!route.startsWith("/") || route.startsWith("//") || route.includes(":") || hasUnsafeCharacter(route)) {
      return null;
    }
    let decoded: string;
    try {
      decoded = decodeURIComponent(route);
    } catch {
      return null;
    }
    if (decoded === route) return next;
    route = decoded;
  }
  return null; // still encoded after this many layers: refused rather than guessed
}

const MAX_NEXT_LENGTH = 2000;
const MAX_ENCODINGS = 4;

/** A control character (below space, or DEL) or a backslash. */
const hasUnsafeCharacter = (text: string) =>
  Array.from(text).some((char) => char.charCodeAt(0) < 0x20 || char.charCodeAt(0) === 0x7f || char === "\\");

/** Where a sign-in goes: the safe ?next= path it came from (safeNextPath), else the dashboard,
 * the home screen (File management D1), so the address can never send anyone to another site.
 * An /admin path stays one: the admin screen itself shows a non-admin it is for admins. */
export function pathAfterSignIn(query: Record<string, string | string[] | undefined>): string {
  return safeNextPath(query.next) ?? "/dashboard";
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
