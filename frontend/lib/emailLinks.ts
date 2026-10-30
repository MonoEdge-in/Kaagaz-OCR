// The emailed links' rules (#457 phase 0), kept pure so vitest can prove them. A link's
// token travels in the address's fragment (#t=...), which the browser never sends to a
// server, so it is in no server or proxy log. A page reads it once, clears it from the
// address bar (history.replaceState) and posts it.

import { ApiError } from "@/lib/api";

export const LINK_GONE = "That link has expired or was already used.";
export const LINK_MISSING = "This page needs the link from your email. Open the link in the email again.";
export const FORGOT_SENT = "If that email has an account, we've sent a link.";
export const NETWORK_FAILURE = "Could not reach Kaagaz. Check your internet connection and try again.";
export const EMAIL_DOWN = "This is not working right now. Try again in a minute.";
export const TOO_MANY_LINKS = "We sent a few links already. Try again in an hour.";

// The server's tokens: URL-safe base64, 43 characters for 32 bytes (at most 128 accepted).
const TOKEN = /^[A-Za-z0-9_-]{1,128}$/;

/** The token in a fragment such as "#t=abc", else null. */
export function tokenFromHash(hash: string): string | null {
  const token = new URLSearchParams(hash.replace(/^#/, "")).get("t");
  return token !== null && TOKEN.test(token) ? token : null;
}

/** The token from this page's address, cleared from the address bar and its history entry. */
export function takeToken(): string | null {
  const token = tokenFromHash(window.location.hash);
  if (window.location.hash) {
    window.history.replaceState(null, "", window.location.pathname + window.location.search);
  }
  return token;
}

/** What a link page says when the server says no. A 404 is email switched off: the link is no good. */
export function linkError(caught: unknown): string {
  if (caught instanceof ApiError) {
    if (caught.code === "link_expired" || caught.status === 404) return LINK_GONE;
    if (caught.status === 400) return caught.message.trim() || LINK_GONE;
    if (caught.status === 429) return TOO_MANY_LINKS;
    if (caught.status === 0) return NETWORK_FAILURE;
    return EMAIL_DOWN;
  }
  if (caught instanceof TypeError) return NETWORK_FAILURE;
  return EMAIL_DOWN;
}

/** The app's banner while the signed-in person's email is not confirmed. */
export function confirmBanner(email: string): string {
  return `Confirm your email. We sent a link to ${email}.`;
}
