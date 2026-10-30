// An admin turns their own two-step sign-in off, and on again, from their own Account menu
// (Tushar, 30 Oct 2026). With it off they sign in with e-mail and password only, and it
// stays off until they turn it on. Members and reviewers have no codes: nothing shows for them.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { foreverControl, userStatus } from "@/components/admin/logic";
import { AccountMenu } from "@/components/shell/AccountMenu";
import { appGate } from "@/components/shell/gate";
import {
  TWO_STEP_ACCOUNT,
  refusal,
  showsTwoStepItem,
  statusText,
  twoStepState,
} from "@/components/shell/twoStepAccount";
import { PaletteProvider } from "@/components/ui/PaletteProvider";
import { ThemeProvider } from "@/components/ui/ThemeProvider";
import { ApiError, deleteDocumentForever } from "@/lib/api";
import type { AdminUser, Me, Role } from "@/lib/types";

const read = (path: string) => readFileSync(join(__dirname, "..", path), "utf8");

const me = (
  role: Role,
  { totp = true, turnedOff = false, scope = role === "admin" ? "admin" : "app" } = {},
): Me => ({
  user: {
    id: "u1",
    email: "asha@sharma.in",
    display_name: "Asha Patil",
    role,
    must_change_password: false,
    totp_enabled: totp,
    two_step_turned_off: turnedOff,
    can_delete_forever: false,
  },
  customer: { id: "c1", name: "Sharma Traders" },
  session: { scope: scope as Me["session"]["scope"] },
});

const OFF = { totp: false, turnedOff: true };

describe("the dialog's words", () => {
  it("say what on and off mean, and what each button does", () => {
    expect(statusText("on")).toEqual({
      text: "Two-step sign-in is on. You type a 6-digit code from your authenticator app after your password.",
      action: "Turn off",
    });
    expect(statusText("off")).toEqual({ text: "Two-step sign-in is off.", action: "Turn on" });
    expect(TWO_STEP_ACCOUNT.item).toBe("Two-step sign-in");
    expect(TWO_STEP_ACCOUNT.password).toBe("Password");
    expect(TWO_STEP_ACCOUNT.code).toBe("6-digit code");
    expect(TWO_STEP_ACCOUNT.offWarning).toBe("Anyone who learns your password can then sign in as you.");
    expect(TWO_STEP_ACCOUNT.offButton).toBe("Turn off two-step sign-in");
    expect(TWO_STEP_ACCOUNT.offDone).toBe(
      "Two-step sign-in is off. You'll sign in with your email and password only.",
    );
    expect(TWO_STEP_ACCOUNT.onDone).toBe("Two-step sign-in is on.");
  });

  it("are calm, in sentence case", () => {
    for (const text of Object.values(TWO_STEP_ACCOUNT)) {
      expect(text).not.toMatch(/can't|cannot|sorry|unfortunately|2FA|MFA/i);
      expect(text.slice(1)).not.toMatch(/\b(Two-Step|Sign-In|Password)\b/);
    }
  });
});

describe("on or off", () => {
  it("is on only when the server says true", () => {
    expect(twoStepState(me("admin"))).toBe("on");
    expect(twoStepState(me("admin", OFF))).toBe("off");
  });
});

describe("the Account menu item", () => {
  it("is for an admin's own session only", () => {
    expect(showsTwoStepItem(me("admin"))).toBe(true);
    expect(showsTwoStepItem(me("admin", OFF))).toBe(true);
    expect(showsTwoStepItem(me("member"))).toBe(false);
    expect(showsTwoStepItem(me("reviewer"))).toBe(false);
    expect(showsTwoStepItem(me("member", { scope: "admin" }))).toBe(false);
    expect(showsTwoStepItem(me("admin", { scope: "app" }))).toBe(false);
  });

  /** The menu as the server renders it, inside the page's theme and palette (as tests/shell-header.test.ts). */
  const menu = (who: Me) =>
    renderToStaticMarkup(
      createElement(
        ThemeProvider,
        { initial: "system" } as Parameters<typeof ThemeProvider>[0],
        createElement(
          PaletteProvider,
          { initial: "kaagaz" } as Parameters<typeof PaletteProvider>[0],
          createElement(AccountMenu, { me: who }),
        ),
      ),
    );

  it("shows in an admin's menu, and not in a member's or reviewer's", () => {
    expect(menu(me("admin"))).toContain(">Two-step sign-in</span>");
    expect(menu(me("admin", OFF))).toContain(">Two-step sign-in</span>");
    expect(menu(me("member"))).not.toMatch(/two-step/i);
    expect(menu(me("reviewer"))).not.toMatch(/two-step/i);
  });

  it("opens the dialog, which gives focus back to the avatar when it closes", () => {
    const source = read("components/shell/AccountMenu.tsx");
    expect(source).toContain("{showsTwoStepItem(me) && (");
    expect(source).toMatch(/<TwoStepDialog\s+me=\{me\}\s+onClose=\{\(\) => setTwoStep\(false\)\}\s+onClosed=\{\(\) => avatar\.current\?\.focus\(\)\}/);
  });
});

describe("the dialog", () => {
  const dialog = read("components/shell/TwoStepDialog.tsx");

  it("is the app's ActionDialog, and turns it off with the password and a code", () => {
    expect(dialog).toContain("<ActionDialog");
    expect(dialog).toContain("await turnOffTwoStep(password, normalizeCode(code))");
    expect(dialog).toContain("<PasswordField");
    expect(dialog).toContain("<CodeField");
    expect(dialog).toContain("{TWO_STEP_ACCOUNT.offWarning}");
  });

  it("turns it on again with the sign-in's own set-up step (QR code, key, code)", () => {
    expect(dialog).toContain("await startTwoStep(password)");
    expect(dialog).toContain("<EnrolStep");
    expect(dialog).not.toContain("<QrCode");
  });

  it("puts an error on its field and moves focus there, and says when it is done", () => {
    expect(dialog).toContain("document.getElementById(fieldId(first))?.focus();"); // a check before sending
    expect(dialog).toContain("document.getElementById(fieldId(field))?.focus();"); // the server said no
    expect(dialog).toMatch(/role="status"/);
    expect(dialog).toMatch(/role="alert"/);
    // The session is asked again, so the menu, Trash and the gate see the change.
    expect(dialog).toContain("refresh();");
  });
});

describe("what to say when the server says no", () => {
  it("puts a refused password or code on its field, in the server's words", () => {
    const wrongCode = new ApiError(400, "bad_request", "That code did not work. Type the newest one.");
    expect(refusal(wrongCode, true)).toEqual({
      field: "code",
      message: "That code did not work. Type the newest one.",
    });
    const wrongPassword = new ApiError(400, "bad_request", "That password is not right.");
    expect(refusal(wrongPassword, true)).toEqual({ field: "password", message: "That password is not right." });
    // Turning on asks for the password only.
    expect(refusal(wrongCode, false).field).toBe("password");
  });

  it("says already off as sent, and the rest in plain words", () => {
    const already = new ApiError(409, "conflict", "Two-step sign-in is already off.");
    expect(refusal(already, true)).toEqual({ field: null, message: "Two-step sign-in is already off." });
    expect(refusal(new ApiError(429, "rate_limited", "x"), true).message).toBe(
      "Too many attempts. Wait a minute, then try again.",
    );
    expect(refusal(new TypeError("Failed to fetch"), true).message).toBe(
      "Could not reach Kaagaz. Check your internet connection and try again.",
    );
    expect(refusal(new ApiError(500, "error", "Traceback…"), true)).toEqual({
      field: null,
      message: "That did not work. Try again in a minute.",
    });
  });
});

describe("an admin who turned two-step off", () => {
  it("uses the app, and is not sent to set it up again", () => {
    expect(appGate(me("admin", OFF))).toBe("app");
    expect(appGate(me("admin", { totp: false }))).toBe("set_two_step"); // reset, not turned off
    expect(appGate(me("admin", { totp: false, turnedOff: undefined as unknown as boolean }))).toBe("set_two_step");
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
    // Both places that delete forever show an ApiError's message as it came.
    expect(read("components/documents/DocumentsTable.tsx")).toContain(
      'return caught instanceof ApiError ? caught.message : "Check your connection, then try again.";',
    );
  });
});

describe("the People list", () => {
  const admin: AdminUser = {
    id: "u2",
    email: "ravi@sharma.in",
    display_name: "Ravi",
    role: "admin",
    disabled: false,
    created_at: "2026-09-01T10:00:00+00:00",
    locked_until: null,
    totp_enabled: false,
    two_step_turned_off: true,
    must_change_password: false,
    can_delete_forever: false,
  };

  it("says Two-step off for an admin who turned it off", () => {
    expect(userStatus(admin, Date.parse("2026-10-30T10:00:00Z")).map((part) => part.text)).toEqual([
      "Active",
      "Two-step off",
    ]);
  });

  it("does not say they set it up at their next sign-in", () => {
    expect(read("components/admin/UsersPanel.tsx")).toContain(
      'users?.some((user) => user.role === "admin" && !user.totp_enabled && !user.two_step_turned_off)',
    );
  });

  it("says where to turn it on before allowing Delete forever", () => {
    const member = { ...admin, role: "member" as const, two_step_turned_off: false };
    expect(foreverControl(member, { isSelf: false, adminTwoStep: false })).toEqual({
      kind: "allow",
      disabledReason: "Turn on two-step sign-in in Account to allow this.",
    });
  });
});
