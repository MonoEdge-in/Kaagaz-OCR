// Account, Ways to sign in (#457 phase 1), kept pure so vitest can prove the words: each row,
// the notice after coming back from Google (?connected=, ?waiting=, ?error=), whether Connect
// asks for the code, the disconnect question and the app's "Google connected" banner.

import { BUSY, DOMAIN_REFUSED } from "@/app/login/providerMessages";
import { errorWords } from "@/lib/errorWords";
import { formatDate } from "@/lib/format";
import type { ConnectedIdentity, Me } from "@/lib/types";

export const TITLE = "Ways to sign in";
export const WAITING = "Waiting for your admin's approval";
/** #507: the banner of a session that may only connect Google (me.google_link_required). */
export const LINK_REQUIRED = "Your organisation asks you to sign in with Google. Connect your Google account to continue.";

const NAMES: Record<string, string> = { google: "Google" };

/** Why the list did not load, and what to do (issue 468): Try again sits beside it. */
export const waysLoadText = (caught: unknown): string => `Could not load your ways to sign in. ${errorWords(caught)}`;

/** "Google" for google; an unknown provider keeps its own name. */
export function providerName(provider: string): string {
  return NAMES[provider] ?? provider;
}

/** "Google", or "Google or Microsoft": only the providers that are on (#457 review). */
export function providersText(providers: readonly string[]): string {
  return providers.map(providerName).join(" or ");
}

/** The line under the page's title; nothing while no provider is on. */
export function introText(providers: readonly string[]): string | null {
  return providers.length > 0 ? `Sign in with ${providersText(providers)} as well as your password.` : null;
}

export function passwordRow(email: string): string {
  return `Email and password: ${email}`;
}

/** One provider's row: not connected, connected with its email and day, or waiting. */
export function rowText(provider: string, identity: ConnectedIdentity | null): string {
  const name = providerName(provider);
  if (identity === null) return `${name}: Not connected`;
  const email = identity.email ?? "an account";
  if (identity.status === "pending") return `${name}: ${email}, ${WAITING}`;
  return `${name}: ${email}, connected ${formatDate(identity.linked_at)}`;
}

export interface PageNotice {
  kind: "done" | "error";
  text: string;
}

const ERRORS: Record<string, string> = {
  taken: "This Google account already belongs to a Kaagaz user.",
  has_provider: "A Google account is already connected. Disconnect it first to connect another.",
  signed_out: "Sign in again, then connect Google.",
  cancelled: "Google sign-in was cancelled.",
  expired: "That sign-in expired. Try again.",
  busy: BUSY,
  // #507: the organisation's rules, checked again when Google comes back.
  google_off: "Google sign-in is off for your organisation.",
  role_refused: "Your organisation doesn't use Google sign-in for your role.",
  domain_refused: DOMAIN_REFUSED,
};

/** What the page says on its way back from Google; ``email``: the connected account's. */
export function pageNotice(params: Record<string, string | undefined>, email?: string | null): PageNotice | null {
  if (params.connected) {
    const name = providerName(params.connected);
    return { kind: "done", text: `${name} connected. You can now sign in with ${email || "it"}.` };
  }
  if (params.waiting) return { kind: "done", text: "Sent to your admin for approval." };
  if (params.error) return { kind: "error", text: ERRORS[params.error] ?? "Connecting Google didn't work. Try again." };
  return null;
}

/** "a.com", "a.com or b.in", "a.com, b.in or c.org". */
function domainsText(domains: readonly string[]): string {
  return domains.length < 2 ? domains.join("") : `${domains.slice(0, -1).join(", ")} or ${domains[domains.length - 1]}`;
}

/** The organisation's rules for this person (#507), under the list; null when there are none.
 * ``limited``: the banner already says Google is asked for, so only the domains are added. */
export function rulesText(rules: { google_only: boolean; domains: string[] } | undefined, limited: boolean): string | null {
  if (!rules) return null;
  const parts: string[] = [];
  if (rules.google_only && !limited) parts.push("Your organisation asks you to sign in with Google.");
  if (rules.domains.length > 0) parts.push(`Use a Google account from ${domainsText(rules.domains)}.`);
  return parts.length > 0 ? parts.join(" ") : null;
}

/** Connect asks for the 6-digit code from anyone with two-step sign-in on (always an admin). */
export function needsCode(me: Me): boolean {
  return me.user.totp_enabled;
}

/** Asked before Disconnect, or before cancelling one that waits for approval. */
export function disconnectQuestion(
  provider: string,
  identity: ConnectedIdentity,
): { title: string; text: string; button: string } {
  const name = providerName(provider);
  const email = identity.email ?? `this ${name} account`;
  if (identity.status === "pending") {
    return {
      title: `Cancel the ${name} request?`,
      text: `Your admin won't be asked to approve ${email}. You can connect it again later.`,
      button: "Cancel request",
    };
  }
  return {
    title: `Disconnect ${name}?`,
    text: `You won't be able to sign in with ${email}. You can connect it again later.`,
    button: "Disconnect",
  };
}

const bannerDay = new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", timeZone: "Asia/Kolkata" });

/** The app's banner for an account connected in the last 14 days: "Google connected on 30 Oct." */
export function bannerText(item: { provider: string; linked_at: string }): string {
  return `${providerName(item.provider)} connected on ${bannerDay.format(new Date(item.linked_at))}.`;
}
