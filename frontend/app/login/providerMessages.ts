// Ways to sign in (#457 phase 1) on the sign-in page, kept pure so vitest can prove them:
// the "Continue with" buttons for the providers the server switched on, and what each
// ?oidc_error=<code> from the provider's callback says. The email of "isn't connected" never
// comes in the address: the page asks GET /api/auth/oidc/notice for it, once.

export const CANCELLED = "Google sign-in was cancelled.";
export const EXPIRED = "That sign-in expired. Try again.";
export const PASSWORD_FIRST = "Sign in with your email and password first to set up your 6-digit code.";
export const REFUSED = "That account can't sign in right now. Ask your admin.";
export const LOCKED =
  "Too many tries were made on this account, so it is locked for a while. Try again later, or ask your admin to unlock it.";
export const UNKNOWN = "Google sign-in didn't work. Try again.";
export const BUSY = "Too many people are signing in right now. Try again in a few minutes.";

/** The banner for a failed provider sign-in; ``email``: the notice's, for ``not_connected``. */
export function oidcErrorMessage(code: string, email?: string): string {
  switch (code) {
    case "cancelled":
      return CANCELLED;
    case "expired":
      return EXPIRED;
    case "not_connected":
      return `${email || "That Google account"} isn't connected to Kaagaz. Sign in with your email and password, then connect it under Ways to sign in.`;
    case "password_first":
      return PASSWORD_FIRST;
    case "refused":
      return REFUSED;
    case "locked":
      return LOCKED;
    case "busy":
      return BUSY;
    default:
      return UNKNOWN;
  }
}

export interface ProviderButton {
  provider: string;
  label: string;
  href: string;
}

// The providers this page has a button for; the server says which are on.
const LABELS: Record<string, string> = { google: "Continue with Google" };

/** A full-width button per provider that is on, starting its sign-in at the API (a GET), with
 * where to go once signed in. */
export function providerButtons(providers: readonly string[], next: string): ProviderButton[] {
  return providers.flatMap((provider) => {
    const label = LABELS[provider];
    if (label === undefined) return [];
    return [{ provider, label, href: `/api/auth/oidc/${provider}/start?next=${encodeURIComponent(next)}` }];
  });
}
