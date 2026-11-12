// The emailed links' rules (#457 phase 0), kept pure so vitest can prove them. A link's
// token travels in the address's fragment (#t=...), which the browser never sends to a
// server, so it is in no server or proxy log. A page reads it once, clears it from the
// address bar (history.replaceState) and posts it.

import { ApiError } from "@/lib/api";

export const LINK_GONE = "That link has expired or was already used.";
export const LINK_MISSING = "This page needs the link from your email. Open the link in the email again.";
export const FORGOT_SENT = "If that email has an account, we've sent a link.";
export const RESEND_SENT = "If that email is waiting to be confirmed, we've sent a new link.";
export const NETWORK_FAILURE = "Could not reach Kaagaz. Check your internet connection and try again.";
export const EMAIL_DOWN = "This is not working right now. Try again in a minute.";
export const TOO_MANY_LINKS = "We sent a few links already. Try again in an hour.";
export const EMAIL_OFF = "Email isn't switched on for Kaagaz yet.";
export const SEND_FAILED = "We couldn't send the email. Try again in a minute.";

// What to do about a gone link, on each link's page (#457 review).
const GONE_NEXT = {
  reset: "Ask for a new one from Forgot password.",
  invite: "Ask your admin to send a new invite.",
  confirm: "Sign in to get a new link.",
} as const;

export type LinkPage = keyof typeof GONE_NEXT;

/** "That link has expired or was already used.", then what to do on this page. */
export function linkGone(page: LinkPage): string {
  return `${LINK_GONE} ${GONE_NEXT[page]}`;
}

/** The confirm page's words: the wait is said only for a sign-up waiting for approval. */
export function confirmedText(pending: boolean): string {
  return pending
    ? "Your email is confirmed. Your organisation is waiting for approval from the Kaagaz team."
    : "Your email is confirmed.";
}

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

/** What an email page says when the server says no; ``page``: a link's page, which says what to
 * do about a gone link. A 404 is email switched off. Anything unexpected is logged. */
export function linkError(caught: unknown, page?: LinkPage): string {
  if (caught instanceof ApiError) {
    if (caught.code === "link_expired") return page ? linkGone(page) : LINK_GONE;
    if (caught.status === 404) return EMAIL_OFF;
    if (caught.status === 400) return caught.message.trim() || LINK_GONE;
    if (caught.status === 429) return TOO_MANY_LINKS;
    if (caught.status === 0) return NETWORK_FAILURE;
  } else if (caught instanceof TypeError) {
    return NETWORK_FAILURE;
  }
  console.error("email request failed", caught);
  return EMAIL_DOWN;
}

/** Issue 468: what the confirm page says when the request failed. The page already took the
 * token out of the address bar, so reloading cannot help: a lost connection or a server problem
 * says to open the email link again. */
export const CONFIRM_NETWORK_FAILURE =
  "Could not reach Kaagaz. Check your internet connection, then open the email link again.";
export const CONFIRM_DOWN = "This is not working right now. In a minute, open the email link again.";

export function confirmError(caught: unknown): string {
  const said = linkError(caught, "confirm");
  if (said === NETWORK_FAILURE) return CONFIRM_NETWORK_FAILURE;
  if (said === EMAIL_DOWN) return CONFIRM_DOWN;
  return said;
}

/** The app's banner while the signed-in person's email is not confirmed. ``sent``: this browser
 * sent a link just now. People made before email had none sent, so "We sent" waits for it. */
export function confirmBanner(email: string, sent: boolean): { text: string; button: string } {
  return sent
    ? { text: `Confirm your email. We sent a link to ${email}.`, button: "Send again" }
    : { text: `Confirm your email, ${email}.`, button: "Send link" };
}
