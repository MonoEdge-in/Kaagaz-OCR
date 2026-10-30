// Two-step sign-in is required for every admin (Tushar, 16 Oct 2026; migration 0035),
// on the one sign-in page. This replaces the morning's per-organisation grant by the
// super admin (its tests went with it, on the owner's decision): no toggle in the
// console, no "Turn off" for an admin, and the set-up step on /login and after sign-up.
// Changed on purpose (Tushar, 30 Oct 2026): an admin can turn their own two-step off, and on
// again, from their Account menu (tests/two-step-account.test.ts); Settings says which it is.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { CHANGE_IN_ACCOUNT, LOST_PHONE, TWO_STEP_OFF, TWO_STEP_ON, TwoStepPanel } from "@/app/(app)/admin/TwoStepPanel";
import { ENROL_REQUIRED, enrolOffer } from "@/app/login/twoStep";
import { appGate, pathAfterSignIn, signedOutMessage, twoStepPath } from "@/components/shell/gate";
import { ApiError } from "@/lib/api";
import type { Me } from "@/lib/types";

// The panel reads who is signed in; each test sets it.
const session = vi.hoisted(() => ({ me: null as unknown }));
vi.mock("@/lib/session", () => ({ useSession: () => ({ me: session.me, refresh: () => undefined }) }));

const read = (path: string) => readFileSync(join(__dirname, "..", path), "utf8");

const me = (role: Me["user"]["role"], totpEnabled: boolean, mustChange = false): Me => ({
  user: {
    id: "u1",
    email: "asha@sharma.in",
    display_name: "Asha",
    role,
    must_change_password: mustChange,
    totp_enabled: totpEnabled,
    two_step_turned_off: false,
    can_delete_forever: false,
  },
  customer: { id: "c1", name: "Sharma Traders" },
  session: { scope: role === "admin" ? "admin" : "app" },
});

const OFFER = { enrol_token: "t.k", secret: "JBSW Y3DP", otpauth_uri: "otpauth://totp/x" };

describe("the set-up offer after a right password", () => {
  it("is read only from 403 mfa_enrol_required with the three strings", () => {
    expect(enrolOffer(new ApiError(403, "mfa_enrol_required", "x", OFFER))).toEqual(OFFER);
    expect(enrolOffer(new ApiError(401, "mfa_enrol_required", "x", OFFER))).toBeNull();
    expect(enrolOffer(new ApiError(403, "forbidden", "x", OFFER))).toBeNull();
    expect(enrolOffer(new ApiError(403, "mfa_enrol_required", "x"))).toBeNull();
    expect(enrolOffer(new ApiError(403, "mfa_enrol_required", "x", { ...OFFER, secret: 7 }))).toBeNull();
    expect(enrolOffer(new TypeError("Failed to fetch"))).toBeNull();
  });

  it("says why in calm words", () => {
    expect(ENROL_REQUIRED).toBe(
      "Set up two-step sign-in to continue. Admins need it to keep your organisation safe.",
    );
    for (const text of [ENROL_REQUIRED, TWO_STEP_ON, TWO_STEP_OFF, CHANGE_IN_ACCOUNT, LOST_PHONE]) {
      expect(text).not.toMatch(/can't|cannot|sorry|unfortunately/i);
    }
  });
});

describe("the sign-in page shows the set-up step", () => {
  const form = read("app/login/SignInForm.tsx");
  const enrol = read("app/login/EnrolStep.tsx");

  it("in place of the form when the server offers it, with the QR code and the code field", () => {
    expect(form).toContain("const offer = enrolOffer(caught);");
    expect(form).toMatch(/if \(enrol\) \{\s*return <RequiredEnrol enrol=\{enrol\}/);
    expect(enrol).toContain("export function RequiredEnrol(");
    expect(enrol).toContain("{ENROL_REQUIRED}");
    expect(enrol).toContain("<EnrolStep");
    expect(enrol).toContain("<QrCode value={enrol.otpauth_uri}");
    expect(enrol).toContain("<CodeField");
  });

  it("and after sign-up, where the reply is the set-up and not a session", () => {
    const signUp = read("app/signup/SignUpForm.tsx");
    expect(signUp).toContain("setEnrol(await signUp(signUpBody(input)));");
    expect(signUp).toContain("<RequiredEnrol");
    expect(signUp).not.toContain("announceSession(signedIn");
  });
});

describe("the admin's own Two-step panel", () => {
  it("says On or Off, and that the Account menu changes it", () => {
    session.me = me("admin", true);
    const on = renderToStaticMarkup(createElement(TwoStepPanel));
    expect(on).toContain("<strong>On</strong>");
    expect(on).toContain(TWO_STEP_ON);
    expect(on).toContain(LOST_PHONE);
    session.me = { ...me("admin", false), user: { ...me("admin", false).user, two_step_turned_off: true } };
    const off = renderToStaticMarkup(createElement(TwoStepPanel));
    expect(off).toContain("<strong>Off</strong>");
    expect(off).toContain("You sign in with your email and password only.");
    for (const html of [on, off]) {
      expect(html).toContain("Turn it off or on from your Account menu.");
      expect(html).not.toContain("<button");
    }
  });
});

describe("the super admin console", () => {
  it("has no two-step toggle any more", () => {
    for (const file of [
      "components/operator/OrganisationsView.tsx",
      "components/operator/confirms.ts",
      "components/operator/platform.ts",
      "lib/types.ts",
    ]) {
      const text = read(file);
      expect(text, file).not.toMatch(/two_step_granted|twoStepConfirm|Allow two-step|\/two-step/);
    }
  });
});

describe("an admin session without two-step sign-in", () => {
  it("is sent to set it up, after a new password first", () => {
    expect(appGate(me("admin", false))).toBe("set_two_step");
    expect(appGate(me("admin", false, true))).toBe("set_password");
    expect(appGate(me("admin", true))).toBe("app");
    expect(appGate(me("member", false))).toBe("app"); // optional for everyone else
    expect(twoStepPath("/admin")).toBe("/login?next=/admin&two_step=1");
    expect(twoStepPath("/documents")).toBe("/login?two_step=1");
    expect(signedOutMessage({ two_step: "1" })).toMatch(/^Set up two-step sign-in to continue\./);
    const session = read("lib/session.tsx");
    expect(session).toContain('if (gate !== "set_two_step") return;');
    expect(session).toContain("leavePage(twoStepPath(pathname));");
  });

  // Changed on purpose (PRD 1, screens): File management D1, the dashboard is the home screen.
  it("comes back only to /admin or the dashboard after signing in", () => {
    expect(pathAfterSignIn({ next: "/admin" })).toBe("/admin");
    expect(pathAfterSignIn({})).toBe("/dashboard");
    expect(pathAfterSignIn({ next: "https://example.com" })).toBe("/dashboard");
    expect(pathAfterSignIn({ next: "//example.com" })).toBe("/dashboard");
  });
});
