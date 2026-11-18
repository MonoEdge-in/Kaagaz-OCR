// Ways to sign in (#457 phase 1) on the sign-in page, kept pure so vitest can prove them:
// the "Continue with" buttons for the providers the server switched on, and what each
// ?oidc_error=<code> from the provider's callback says. The email of "isn't connected" never
// comes in the address: the page asks GET /api/auth/oidc/notice for it, once.

export const CANCELLED = "Google sign-in was cancelled.";
export const EXPIRED = "That sign-in took too long. Go back and sign in again.";
export const PASSWORD_FIRST = "Sign in with your email and password first to set up your 6-digit code.";
export const REFUSED = "That account can't sign in right now. Ask your admin.";
export const LOCKED =
  "Too many tries were made on this account, so it is locked for a while. Try again later, or ask your admin to unlock it.";
export const UNKNOWN = "Google sign-in didn't work. Try again.";
export const BUSY = "Too many people are signing in right now. Try again in a few minutes.";
// #507: the organisation's own rules for Google sign-in.
export const GOOGLE_OFF = "Google sign-in is off for your organisation. Sign in with your email and password.";
export const ROLE_REFUSED =
  "Your organisation doesn't use Google sign-in for your role. Sign in with your email and password.";
export const DOMAIN_REFUSED = "That Google account isn't from your organisation's domain. Choose your work account.";
export const WAITING =
  "Your Google account is waiting for your admin to approve it. Until then, sign in with your email and password.";
/** A right password for a role that signs in with Google only (403 password_refused). */
export const PASSWORD_REFUSED = "Your organisation asks you to sign in with Google. Use Continue with Google.";

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
    case "google_off":
      return GOOGLE_OFF;
    case "role_refused":
      return ROLE_REFUSED;
    case "domain_refused":
      return DOMAIN_REFUSED;
    case "waiting":
      return WAITING;
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
export function providerButtons(providers: readonly string[], next: string, legacyTemporary = false): ProviderButton[] {
  return providers.flatMap((provider) => {
    const original = LABELS[provider];
    const label = original && legacyTemporary ? `${original} (existing Kaagaz sign-in)` : original;
    if (label === undefined) return [];
    return [{ provider, label, href: `/api/auth/oidc/${provider}/start?next=${encodeURIComponent(next)}` }];
  });
}
