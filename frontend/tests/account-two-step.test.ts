// Two-step codes for everyone (#457 phase 3): Account, Two-step sign-in. Admins keep theirs
// on; a member or reviewer turns it on (password, the QR code or key, a first code) and off
// (password and a code). The admin's People list says Two-step: On or Off for each user, and
// offers "Reset two-step code" for anyone with codes on, never for yourself.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { userActions, userStatus } from "@/components/admin/logic";
import { TwoStepDialog } from "@/components/shell/TwoStepDialog";
import {
  LOST_CODE,
  TURNED_OFF,
  TURNED_ON,
  twoStepError,
  twoStepView,
} from "@/components/shell/twoStepAccount";
import { ApiError } from "@/lib/api";
import type { AdminUser, Me } from "@/lib/types";

const read = (path: string) => readFileSync(join(__dirname, "..", path), "utf8");

const me = (role: Me["user"]["role"], totpEnabled: boolean): Me => ({
  user: {
    id: "u1",
    email: "ramesh@example.com",
    display_name: "Ramesh",
    role,
    must_change_password: false,
    totp_enabled: totpEnabled,
    can_delete_forever: false,
    email_confirmed: true,
  },
  customer: { id: "c1", name: "Sharma Traders" },
  session: { scope: role === "admin" ? "admin" : "app" },
});

const dialog = (who: Me) =>
  renderToStaticMarkup(createElement(TwoStepDialog, { me: who, onClose: () => {}, onDone: () => {} }));

describe("Account, Two-step sign-in", () => {
  it("is an item of the account menu", () => {
    const menu = read("components/shell/AccountMenu.tsx");
    expect(menu).toContain("<span>Two-step sign-in</span>");
    expect(menu).toContain("<TwoStepDialog");
  });

  it("knows which panel to show", () => {
    expect(twoStepView(me("admin", true))).toBe("admin");
    expect(twoStepView(me("member", false))).toBe("off");
    expect(twoStepView(me("reviewer", true))).toBe("on");
  });

  it("tells an admin it stays on, with no Turn off", () => {
    const html = dialog(me("admin", true));
    expect(html).toContain("<strong>On</strong>");
    expect(html).toContain("Admins keep it on.");
    expect(html).not.toMatch(/turn off|turn on/i);
  });

  it("offers a member Turn on with the password first", () => {
    const html = dialog(me("member", false));
    expect(html).toContain("<strong>Off</strong>");
    expect(html).toContain("Current password");
    expect(html).toContain("Turn on");
  });

  it("asks a member for the password and a code to turn it off", () => {
    const html = dialog(me("reviewer", true));
    expect(html).toContain("<strong>On</strong>");
    expect(html).toContain("Current password");
    expect(html).toContain("6-digit code");
    expect(html).toContain("Turn off");
    expect(html).toContain(LOST_CODE);
  });

  it("uses the shared set-up step and the new routes", () => {
    const source = read("components/shell/TwoStepDialog.tsx");
    expect(source).toContain("<EnrolStep");
    expect(source).toContain("<CodeField");
    const api = read("lib/api.ts");
    expect(api).toContain('"/auth/2fa/start", { password }');
    expect(api).toContain('"/auth/2fa/off", { password, code }');
    expect(api).not.toContain("/auth/admin/2fa");
  });

  it("says what happened, and the server's own words when it says no", () => {
    expect(TURNED_ON).toBe("Two-step sign-in is on.");
    expect(TURNED_OFF).toBe("Two-step sign-in is off. Your other devices are signed out.");
    expect(twoStepError(new ApiError(400, "bad_request", "Your current password is not right."))).toBe(
      "Your current password is not right.",
    );
    expect(twoStepError(new ApiError(409, "conflict", "Two-step sign-in is already on."))).toBe(
      "Two-step sign-in is already on.",
    );
    expect(twoStepError(new TypeError("offline"))).toMatch(/internet connection/);
  });
});

describe("the admin's People list", () => {
  const NOW = Date.parse("2026-10-30T10:00:00Z");
  const user = (changes: Partial<AdminUser>): AdminUser => ({
    id: "u2",
    email: "asha@example.com",
    display_name: "Asha",
    role: "member",
    disabled: false,
    created_at: "2026-10-01T00:00:00Z",
    locked_until: null,
    totp_enabled: false,
    must_change_password: false,
    can_delete_forever: false,
    ...changes,
  });
  const texts = (u: AdminUser) => userStatus(u, NOW).map((part) => part.text);

  it("says Two-step: On or Off for every user", () => {
    expect(texts(user({ totp_enabled: true }))).toEqual(["Active", "Two-step: On"]);
    expect(texts(user({ role: "reviewer" }))).toEqual(["Active", "Two-step: Off"]);
    expect(texts(user({ role: "admin" }))).toEqual(["Active", "Two-step: Off"]);
  });

  it("offers Reset two-step code for anyone with codes on, never for yourself", () => {
    expect(userActions(user({ totp_enabled: true }), { isSelf: false, now: NOW }).reset2fa).toBe(true);
    expect(userActions(user({}), { isSelf: false, now: NOW }).reset2fa).toBe(false);
    expect(userActions(user({ role: "admin", totp_enabled: true }), { isSelf: true, now: NOW }).reset2fa).toBe(
      false,
    );
    expect(read("components/admin/UsersPanel.tsx")).toContain('label: "Reset two-step code"');
  });
});
