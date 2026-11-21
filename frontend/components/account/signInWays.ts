// What is left of Account, Ways to sign in (#457 phase 1), now that Google sign-in and the page
// are gone (21 Nov 2026): the words the admin screens still use for People and Settings.

/** #507 review: People's tag for a person whose connected Google account's domain is not checked. */
export const DOMAIN_UNCHECKED = "Sign in with Google once so Kaagaz can check its domain.";

const NAMES: Record<string, string> = { google: "Google" };

/** "Google" for google; an unknown provider keeps its own name. */
export function providerName(provider: string): string {
  return NAMES[provider] ?? provider;
}

/** "Google", or "Google or Microsoft": only the providers that are on (#457 review). */
export function providersText(providers: readonly string[]): string {
  return providers.map(providerName).join(" or ");
}
