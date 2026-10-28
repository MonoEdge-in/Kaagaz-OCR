// Keycloak sign-in (#435): the sign-in page in both modes, the failure words, the gates a
// Keycloak session skips, the other tabs told after a sign-in, the sign-out that goes on to
// Keycloak, Delete forever confirmed through Keycloak, and the two-step words and resets
// that are hidden while Keycloak keeps two-step sign-in.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { KeycloakSignIn, SignInChoice } from "@/app/login/KeycloakSignIn";
import { keycloakProblem, keycloakStartPath, signInErrorMessage, USE_KEYCLOAK } from "@/app/login/signIn";
import { userActions, userStatus } from "@/components/admin/logic";
import {
  canDeleteForever,
  foreverConfirmsWithKeycloak,
  foreverNeeds,
  foreverNeedsPassword,
} from "@/components/documents/trash";
import { adminActions } from "@/components/operator/logic";
import { afterKeycloakSignIn, appGate } from "@/components/shell/gate";
import { ApiError, keycloakOn } from "@/lib/api";
import type { AdminUser, Me } from "@/lib/types";

const read = (path: string) => readFileSync(join(__dirname, "..", path), "utf8");

const me = (keycloak: boolean, role: Me["user"]["role"] = "admin", granted = false): Me => ({
  user: {
    id: "u1",
    email: "asha@sharma.in",
    display_name: "Asha",
    role,
    must_change_password: true, // Kaagaz's temporary password: Keycloak owns the password now
    totp_enabled: false,
    can_delete_forever: granted,
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

const render = (props: { note: string | null; next: string; problem: string | null }, keycloak = false) =>
  renderToStaticMarkup(createElement(keycloak ? KeycloakSignIn : SignInChoice, props));

describe("the sign-in page", () => {
  // Changed on purpose (review, 28 Oct 2026): it asks the server before showing a form, so
  // with Keycloak on no password form shows that can't work.
  it("shows only its heading until the server says which sign-in is on", () => {
    const html = render({ note: null, next: "/dashboard", problem: null });
    expect(html).toContain("Sign in to Kaagaz");
    expect(html).not.toContain('type="password"');
    expect(html).not.toContain("Sign in with Keycloak");
  });

  it("back from a failed Keycloak sign-in, shows the Keycloak view and why at once", () => {
    const problem = keycloakProblem({ keycloak_error: "no_account" });
    const html = render({ note: null, next: "/dashboard", problem });
    expect(html).toContain("Sign in with Keycloak");
    expect(html).toContain('role="alert"');
    expect(html).toContain("There is no Kaagaz account for you, or it is turned off.");
  });

  it("with Keycloak on, is one link to the backend's start, and no password field", () => {
    const html = render({ note: null, next: "/admin", problem: null }, true);
    expect(html).toContain('href="/api/auth/keycloak/start?next=%2Fadmin"');
    expect(html).toContain('class="btn btn-primary btn-block');
    expect(html).not.toContain('type="password"');
    expect(html).not.toContain('role="alert"');
  });

  it("knows Keycloak is on from the session reply, signed in or not", () => {
    expect(keycloakOn({ signed_in: false })).toBe(false);
    expect(keycloakOn({ signed_in: false, keycloak: true })).toBe(true);
    expect(keycloakOn({ signed_in: true, me: me(true) })).toBe(true);
    expect(keycloakOn({ signed_in: true, me: me(false) })).toBe(false);
  });

  it("says plainly when the password sign-in was switched off since the page loaded", () => {
    const refused = new ApiError(403, "keycloak_sign_in", "Sign in with Keycloak on the sign-in page.");
    expect(signInErrorMessage(refused)).toBe(USE_KEYCLOAK);
  });
});

describe("the failure words", () => {
  it("are plain, one for each code, and never text from the address", () => {
    const codes = ["state", "cancelled", "unreachable", "token", "email_unverified", "no_account", "wrong_org", "two_step", "confirm"];
    for (const code of codes) {
      expect(keycloakProblem({ keycloak_error: code })).not.toBe("Sign-in did not work. Sign in again.");
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

  it("tells the other tabs once who signed in, and drops the mark from the address", () => {
    expect(afterKeycloakSignIn({ pathname: "/dashboard", search: "?signed_in=1" })).toBe("/dashboard");
    expect(afterKeycloakSignIn({ pathname: "/documents", search: "?trash=1&signed_in=1" })).toBe("/documents?trash=1");
    expect(afterKeycloakSignIn({ pathname: "/documents", search: "?trash=1" })).toBeNull();
    const session = read("lib/session.tsx");
    expect(session).toContain("announceSession(state.me.user.id);");
    expect(session).toContain("window.history.replaceState(null, \"\", clean);");
  });

  it("signs out of Keycloak too, after the Kaagaz session ends, unless it had ended already", () => {
    const menu = read("components/shell/AccountMenu.tsx");
    const ended = menu.indexOf("await (kind === \"everywhere\" ? logoutEverywhere() : logout());");
    const onward = menu.indexOf("if (me.session.keycloak === true && !ended) leavePage(KEYCLOAK_SIGN_OUT);");
    expect(ended).toBeGreaterThan(0);
    expect(onward).toBeGreaterThan(ended); // the Kaagaz session ends first
    expect(menu).toContain("ended = caught instanceof ApiError && caught.status === 401;");
    expect(menu).toContain("else leavePage(signedOutPath(pathname));");
  });
});

describe("Delete forever", () => {
  it("is open to a Keycloak admin, with no two-step hint", () => {
    expect(canDeleteForever(me(true))).toBe(true);
    expect(foreverNeeds(me(true))).toBeNull();
    expect(canDeleteForever(me(false))).toBe(false); // unchanged without Keycloak
    expect(foreverNeeds(me(false))).toBe("two_step");
  });

  it("asks a granted member to confirm with Keycloak instead of a password", () => {
    expect(foreverNeedsPassword(me(true, "member", true))).toBe(false);
    expect(foreverConfirmsWithKeycloak(me(true, "member", true))).toBe(true);
    expect(foreverNeedsPassword(me(false, "member", true))).toBe(true);
    expect(foreverConfirmsWithKeycloak(me(false, "member", true))).toBe(false);
    expect(keycloakStartPath("/documents?trash=1", true)).toBe(
      "/api/auth/keycloak/start?next=%2Fdocuments%3Ftrash%3D1&confirm=1",
    );
    const table = read("components/documents/DocumentsTable.tsx");
    expect(table.match(/\{confirming === "forever" && askKeycloak && <KeycloakConfirm \/>\}/g)).toHaveLength(2);
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
