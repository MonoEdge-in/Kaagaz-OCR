// Keycloak is the only sign-in (21 Nov 2026): /login is one heading and one button, the old
// pages are gone, and the account menu's two items open Keycloak's own pages.

import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { SignInPanel } from "@/app/login/SignInPanel";
import { keycloakAccountHref } from "@/lib/keycloak";

const root = join(__dirname, "..");
const render = (props: { note?: string | null; next?: string; keycloakError?: string | null }) =>
  renderToStaticMarkup(createElement(SignInPanel, { note: null, next: "/documents", ...props }));

describe("the sign-in page", () => {
  it("is the heading and one Sign in button that starts the company sign-in and keeps next", () => {
    const html = render({});
    expect(html).toContain("Sign in to Kaagaz");
    expect(html.match(/<a /g)).toHaveLength(1);
    expect(html).toContain('href="/api/auth/keycloak/start?next=%2Fdocuments"');
    expect(html).toContain(">Sign in</a>");
    for (const gone of ["<input", "<form", "password", "Password", "Forgot", "Sign up", "Google", "code"]) {
      expect(html).not.toContain(gone);
    }
  });

  it("says why a sign-in did not finish, above the button, in the fixed words", () => {
    const html = render({ keycloakError: "refused" });
    expect(html).toContain("Your company account could not verify this action. No change was made.");
    expect(html.indexOf("role=\"alert\"")).toBeLessThan(html.indexOf("<a "));
    expect(render({ keycloakError: "password=secret" })).not.toContain("secret");
  });

  it("says a sign out or an ended session in a status note", () => {
    // The words are filled in once the page runs (SignedOutNotice), so screen readers announce them.
    expect(render({ note: "You signed out." })).toContain('role="status"');
  });
});

describe("the removed pages", () => {
  it("are not in the app any more", () => {
    for (const gone of [
      "app/signup",
      "app/forgot-password",
      "app/reset-password/page.tsx",
      "app/login/SignInForm.tsx",
      "app/login/ProviderButtons.tsx",
      "app/login/LimitedMfaEnrollment.tsx",
      "components/shell/ChangePasswordDialog.tsx",
      "components/shell/TwoStepDialog.tsx",
      "components/account/KeycloakLinkPanel.tsx",
    ]) {
      expect(existsSync(join(root, gone)), gone).toBe(false);
    }
  });
});

describe("the account menu", () => {
  it("opens Keycloak's own pages for the password and the two-step sign-in, and comes back", () => {
    expect(keycloakAccountHref("UPDATE_PASSWORD", "/documents")).toBe(
      "/api/auth/keycloak/start?next=%2Fdocuments&action=UPDATE_PASSWORD",
    );
    expect(keycloakAccountHref("CONFIGURE_TOTP", "https://outside.example")).toBe(
      "/api/auth/keycloak/start?next=%2Fdashboard&action=CONFIGURE_TOTP",
    );
    const menu = readFileSync(join(root, "components/shell/AccountMenu.tsx"), "utf8");
    expect(menu).toContain('keycloakAccountHref("UPDATE_PASSWORD"');
    expect(menu).toContain('keycloakAccountHref("CONFIGURE_TOTP"');
    expect(menu).not.toContain("ChangePasswordDialog");
  });
});
