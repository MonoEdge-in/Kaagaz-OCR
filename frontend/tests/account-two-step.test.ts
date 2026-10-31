// Two-step codes for everyone (#457 phase 3): Account, Two-step sign-in. Anyone turns it on
// (password, the QR code or key, a first code) and off (password and a code). Changed on
// purpose (#457 with #460, Tushar, 30 Oct 2026): an admin turns theirs off too, and it stays
// off until they turn it on (Me's two_step_turned_off); they then sign in with the password
// alone and use the app, but Delete forever still needs it on. The admin's People list says
// Two-step: On or Off for each user, and offers "Reset two-step code" for anyone with codes
// on, never for yourself.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { LOST_PHONE } from "@/app/(app)/admin/TwoStepPanel";
import { foreverControl, userActions, userStatus } from "@/components/admin/logic";
import { appGate } from "@/components/shell/gate";
import { TwoStepDialog } from "@/components/shell/TwoStepDialog";
import {
  LOST_CODE,
  OFF_WARNING,
  TURNED_OFF,
  TURNED_ON,
  twoStepError,
  twoStepView,
} from "@/components/shell/twoStepAccount";
import { ApiError, deleteDocumentForever } from "@/lib/api";
import type { AdminUser, Me } from "@/lib/types";

const read = (path: string) => readFileSync(join(__dirname, "..", path), "utf8");

const me = (role: Me["user"]["role"], totpEnabled: boolean, turnedOff = false): Me => ({
  user: {
    id: "u1",
    email: "ramesh@example.com",
    display_name: "Ramesh",
    role,
    must_change_password: false,
    totp_enabled: totpEnabled,
    two_step_turned_off: turnedOff,
    can_delete_forever: false,
    email_confirmed: true,
  },
  customer: { id: "c1", name: "Sharma Traders" },
  session: { scope: role === "admin" ? "admin" : "app" },
  providers: [], // #457 review: the sign-in providers switched on
});

const dialog = (who: Me) =>
  renderToStaticMarkup(createElement(TwoStepDialog, { me: who, onClose: () => {}, onDone: () => {} }));

describe("Account, Two-step sign-in", () => {
  it("is an item of the account menu", () => {
    const menu = read("components/shell/AccountMenu.tsx");
    expect(menu).toContain("<span>Two-step sign-in</span>");
    expect(menu).toContain("<TwoStepDialog");
  });

  it("knows which panel to show, the same for every role", () => {
    expect(twoStepView(me("admin", true))).toBe("on");
    expect(twoStepView(me("admin", false, true))).toBe("off");
    expect(twoStepView(me("member", false))).toBe("off");
    expect(twoStepView(me("reviewer", true))).toBe("on");
  });

  it("asks an admin with it on for the password and a code to turn it off, and warns", () => {
    const html = dialog(me("admin", true));
    expect(html).toContain("<strong>On</strong>");
    expect(html).toContain("Current password");
    expect(html).toContain("6-digit code");
    expect(html).toContain("Turn off");
    expect(html).toContain(OFF_WARNING);
    expect(html).toContain(LOST_PHONE); // another admin resets an admin's
    expect(html).not.toContain(LOST_CODE);
  });

  it("offers an admin who turned it off Turn on, with the password first", () => {
    const html = dialog(me("admin", false, true));
    expect(html).toContain("<strong>Off</strong>");
    expect(html).toContain("Current password");
    expect(html).toContain("Turn on");
    expect(html).not.toContain('id="two-step-code"');
    expect(html).not.toContain(OFF_WARNING);
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
    expect(html).toContain(OFF_WARNING);
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
    expect(OFF_WARNING).toBe("Anyone who learns your password can then sign in as you.");
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

describe("an admin who turned two-step off", () => {
  it("uses the app, and is not sent to set it up again", () => {
    expect(appGate(me("admin", false, true))).toBe("app");
    expect(appGate(me("admin", false))).toBe("set_two_step"); // reset or new, not turned off
  });

  it("is told where to turn it on before deleting forever", () => {
    expect(read("components/documents/DocumentsView.tsx")).toContain(
      "To delete forever, turn on two-step sign-in in Account.",
    );
  });

  it("keeps the server's own words when Delete forever is refused", async () => {
    const words = "Turn on two-step sign-in in Account to do this.";
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ error: { code: "two_step_required", message: words } }), { status: 403 })),
    );
    await expect(deleteDocumentForever("d1")).rejects.toMatchObject({ status: 403, message: words });
    vi.unstubAllGlobals();
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
    two_step_turned_off: false,
    must_change_password: false,
    can_delete_forever: false,
    ...changes,
  });
  const texts = (u: AdminUser) => userStatus(u, NOW).map((part) => part.text);

  it("says Two-step: On or Off for every user", () => {
    expect(texts(user({ totp_enabled: true }))).toEqual(["Active", "Two-step: On"]);
    expect(texts(user({ role: "reviewer" }))).toEqual(["Active", "Two-step: Off"]);
    expect(texts(user({ role: "admin" }))).toEqual(["Active", "Two-step: Off"]);
    expect(texts(user({ role: "admin", two_step_turned_off: true }))).toEqual(["Active", "Two-step: Off"]);
  });

  it("does not say an admin who turned it off sets it up at the next sign-in", () => {
    expect(read("components/admin/UsersPanel.tsx")).toContain(
      'users?.some((user) => user.role === "admin" && !user.totp_enabled && !user.two_step_turned_off)',
    );
  });

  it("says where to turn it on before allowing Delete forever", () => {
    expect(foreverControl(user({}), { isSelf: false, adminTwoStep: false })).toEqual({
      kind: "allow",
      disabledReason: "Turn on two-step sign-in in Account to allow this.",
    });
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
