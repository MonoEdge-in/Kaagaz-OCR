// Account, Ways to sign in (#457 phase 1), kept pure so vitest can prove the words: each row,
// the notice after coming back from Google (?connected=, ?waiting=, ?error=), whether Connect
// asks for the code, the disconnect question and the app's "Google connected" banner.

import { BUSY } from "@/app/login/providerMessages";
import { formatDate } from "@/lib/format";
import type { ConnectedIdentity, Me } from "@/lib/types";

export const TITLE = "Ways to sign in";
export const INTRO = "Sign in with Google or Microsoft as well as your password.";
export const WAITING = "Waiting for your admin's approval";

const NAMES: Record<string, string> = { google: "Google" };

/** "Google" for google; an unknown provider keeps its own name. */
export function providerName(provider: string): string {
  return NAMES[provider] ?? provider;
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
