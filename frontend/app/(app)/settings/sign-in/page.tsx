// /settings/sign-in: Account, Sign-in and security (21 Nov 2026). Keycloak is the only sign-in,
// so the password and the authenticator app live on its own pages; these two buttons open them
// and bring the person back here.

import { keycloakAccountHref } from "@/lib/keycloak";

export const metadata = { title: "Sign-in and security" };

export default function SignInSettingsPage() {
  return (
    <div className="page">
      <header className="page-header">
        <h1>Sign-in and security</h1>
        <p className="muted">Your company account keeps your password and your two-step sign-in.</p>
      </header>
      <div className="stack">
        <a className="btn btn-primary" href={keycloakAccountHref("UPDATE_PASSWORD", "/settings/sign-in")}>
          Change password
        </a>
        <a className="btn" href={keycloakAccountHref("CONFIGURE_TOTP", "/settings/sign-in")}>
          Two-step sign-in
        </a>
      </div>
    </div>
  );
}
