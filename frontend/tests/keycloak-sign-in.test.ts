// Keycloak sign-in (#435): the sign-in page in both modes, the failure words, the gates a
// Keycloak session skips, the sign-out that goes on to Keycloak, and the two-step words and
// resets that are hidden while Keycloak keeps two-step sign-in.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { KeycloakSignIn, SignInChoice } from "@/app/login/KeycloakSignIn";
import { keycloakOn, keycloakProblem, keycloakStartPath } from "@/app/login/signIn";
import { userActions, userStatus } from "@/components/admin/logic";
import { adminActions } from "@/components/operator/logic";
import { appGate, KEYCLOAK_SIGN_OUT } from "@/components/shell/gate";
import type { AdminUser, Me } from "@/lib/types";

const read = (path: string) => readFileSync(join(__dirname, "..", path), "utf8");

const me = (keycloak: boolean): Me => ({
  user: {
    id: "u1",
    email: "asha@sharma.in",
    display_name: "Asha",
    role: "admin",
    must_change_password: true, // Kaagaz's temporary password: Keycloak owns the password now
    totp_enabled: false,
    can_delete_forever: false,
  },
  customer: { id: "c1", name: "Sharma Traders" },
  session: keycloak ? { scope: "admin", keycloak: true } : { scope: "admin" },
});

const admin: AdminUser = {
  id: "u2",
  email: "ravi@sharma.in",
  display_name: "Ravi",
  role: "admin",
  disabled: false,
  created_at: "2026-09-01T10:00:00+00:00",
  locked_until: null,
  totp_enabled: true,
  must_change_password: false,
  can_delete_forever: false,
};

describe("the sign-in page", () => {
  it("is the e-mail and password form until the server says Keycloak is on", () => {
    const html = renderToStaticMarkup(createElement(SignInChoice, { note: null, next: "/dashboard", problem: null }));
    expect(html).toContain('type="password"');
    expect(html).not.toContain("Sign in with Keycloak");
  });

  it("with Keycloak on, is one link to the backend's start, and no password field", () => {
    const html = renderToStaticMarkup(createElement(KeycloakSignIn, { note: null, next: "/admin", problem: null }));
    expect(html).toContain('href="/api/auth/keycloak/start?next=%2Fadmin"');
    expect(html).toContain('class="btn btn-primary btn-block');
    expect(html).toContain("Sign in with Keycloak");
    expect(html).not.toContain("password\"");
    expect(html).not.toContain('role="alert"');
  });

  it("says why a Keycloak sign-in failed, as an alert", () => {
    const problem = keycloakProblem({ keycloak_error: "no_account" });
    const html = renderToStaticMarkup(createElement(KeycloakSignIn, { note: null, next: "/dashboard", problem }));
    expect(html).toContain('role="alert"');
    expect(html).toContain("There is no Kaagaz account for you, or it is turned off.");
  });

  it("knows Keycloak is on from the session reply, signed in or not", () => {
    expect(keycloakOn({ signed_in: false })).toBe(false);
    expect(keycloakOn({ signed_in: false, keycloak: true })).toBe(true);
    expect(keycloakOn({ signed_in: true, me: me(true) })).toBe(true);
    expect(keycloakOn({ signed_in: true, me: me(false) })).toBe(false);
  });

  it("sends where to come back to, encoded", () => {
    expect(keycloakStartPath("/dashboard")).toBe("/api/auth/keycloak/start?next=%2Fdashboard");
  });
});

describe("the failure words", () => {
  it("are plain, one for each code, and never text from the address", () => {
    for (const code of ["state", "cancelled", "unreachable", "token", "email_unverified", "no_account"]) {
      expect(keycloakProblem({ keycloak_error: code })).toMatch(/\.$/);
    }
    expect(keycloakProblem({ keycloak_error: "<b>made-up</b>" })).toBe("Sign-in did not work. Sign in again.");
    expect(keycloakProblem({ keycloak_error: "toString" })).toBe("Sign-in did not work. Sign in again.");
    expect(keycloakProblem({ keycloak_error: ["state", "token"] })).toBeNull();
    expect(keycloakProblem({})).toBeNull();
  });
});

describe("a Keycloak session", () => {
  it("never shows the set-new-password or set-up-two-step screens", () => {
    expect(appGate(me(true))).toBe("app");
    expect(appGate(me(false))).toBe("set_password"); // unchanged without Keycloak
  });

  it("signs out of Keycloak too, after the Kaagaz session ends", () => {
    expect(KEYCLOAK_SIGN_OUT).toBe("/api/auth/keycloak/logout");
    const menu = read("components/shell/AccountMenu.tsx");
    const ended = menu.indexOf("await (kind === \"everywhere\" ? logoutEverywhere() : logout());");
    const onward = menu.indexOf("if (me.session.keycloak === true) leavePage(KEYCLOAK_SIGN_OUT);");
    expect(ended).toBeGreaterThan(0);
    expect(onward).toBeGreaterThan(ended); // the Kaagaz session ends first
    expect(menu).toContain("else leavePage(signedOutPath(pathname));");
  });
});

describe("two-step sign-in while Keycloak keeps it", () => {
  it("has no Two-step panel in Admin, Settings", () => {
    expect(read("components/admin/AdminView.tsx")).toContain("{!keycloak && twoStep}");
  });

  it("has no two-step words or reset on the users list", () => {
    expect(userStatus(admin, 0).map((part) => part.text)).toContain("Two-step on");
    expect(userStatus(admin, 0, true).map((part) => part.text)).toEqual(["Active"]);
    expect(userActions(admin, { isSelf: false, now: 0 }).reset2fa).toBe(true);
    expect(userActions(admin, { isSelf: false, now: 0, keycloak: true }).reset2fa).toBe(false);
  });

  it("has no Reset two-step in the operator's admins list", () => {
    const admin = { two_step: true, locked: false, disabled: false };
    expect(adminActions(admin, "active")).toEqual(["disable", "reset-2fa"]);
    expect(adminActions(admin, "active", true)).toEqual(["disable"]);
  });
});
