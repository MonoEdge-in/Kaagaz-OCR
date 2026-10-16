import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { NETWORK_FAILURE, TOO_MANY_ATTEMPTS } from "@/app/login/signIn";
import { describeAction, whoText } from "@/components/admin/logic";
import {
  PASSWORD_MISSING,
  PASSWORD_STEP,
  SESSION_ENDED,
  TWO_STEP_FAILED,
  TWO_STEP_TEXT,
  currentPasswordError,
  twoStepError,
  twoStepState,
} from "@/components/admin/twoStep";
import { twoStepConfirm } from "@/components/operator/confirms";
import { actionWords } from "@/components/operator/platform";
import { ApiError } from "@/lib/api";
import type { Me, Organisation } from "@/lib/types";

const me = (totpEnabled: boolean): Me => ({
  user: {
    id: "u1",
    email: "asha@sharma.in",
    display_name: "Asha",
    role: "admin",
    must_change_password: false,
    totp_enabled: totpEnabled,
    can_delete_forever: false,
    two_step_allowed: true,
  },
  customer: { id: "c1", name: "Sharma Traders" },
  session: { scope: "admin" },
});

describe("the admin's own two-step sign-in", () => {
  it("is On only when the server says so, and says what sign-in will ask for", () => {
    expect(twoStepState(me(true))).toBe("on");
    expect(twoStepState(me(false))).toBe("off");
    expect(TWO_STEP_TEXT.on.label).toBe("On");
    expect(TWO_STEP_TEXT.on.meaning).toMatch(/password and a 6-digit code/);
    expect(TWO_STEP_TEXT.off.label).toBe("Off");
    expect(TWO_STEP_TEXT.off.meaning).toMatch(/password only/);
  });

  it("asks for the current password to turn it on or off, and never trims it", () => {
    expect(currentPasswordError("")).toBe(PASSWORD_MISSING);
    expect(currentPasswordError(" ")).toBeNull();
    expect(currentPasswordError("a long password")).toBeNull();
    expect(PASSWORD_STEP.on.question).toMatch(/current password.*key/);
    expect(PASSWORD_STEP.on.button).toBe("Show my key");
    expect(PASSWORD_STEP.off.question).toMatch(/^Turn off two-step sign-in\?/);
    expect(PASSWORD_STEP.off.button).toBe("Yes, turn off");
  });
});

describe("twoStepError", () => {
  it("shows the server's plain words for a wrong password, a lock, and already on or off", () => {
    for (const [status, said] of [
      [400, "Your current password is not right."],
      [400, "Too many wrong passwords. Try again in 15 minutes."],
      [409, "Two-step sign-in is already on."],
      [409, "Two-step sign-in is already off."],
    ] as const) {
      expect(twoStepError(new ApiError(status, "x", said))).toBe(said);
    }
    expect(twoStepError(new ApiError(400, "bad_request", "  "))).toBe(TWO_STEP_FAILED);
  });

  it("says the admin session has ended on 401 or 403", () => {
    expect(twoStepError(new ApiError(401, "not_signed_in", "Please sign in."))).toBe(SESSION_ENDED);
    expect(twoStepError(new ApiError(403, "admin_session_required", "x"))).toBe(SESSION_ENDED);
  });

  it("uses the sign-in page's words for waiting and network trouble, never a raw error", () => {
    expect(twoStepError(new ApiError(429, "rate_limited", "x"))).toBe(TOO_MANY_ATTEMPTS);
    expect(twoStepError(new ApiError(0, "network", "x"))).toBe(NETWORK_FAILURE);
    expect(twoStepError(new TypeError("Failed to fetch"))).toBe(NETWORK_FAILURE);
    expect(twoStepError(new ApiError(500, "error", "Traceback ..."))).toBe(TWO_STEP_FAILED);
    expect(twoStepError(new Error("boom"))).toBe(TWO_STEP_FAILED);
  });
});

// Two-step sign-in by grant (Tushar, 16 Oct 2026): hidden until the super admin allows it.
describe("two-step sign-in, allowed per organisation by the super admin", () => {
  afterEach(() => vi.unstubAllGlobals());

  // The dialog reads only these.
  const org = { id: "org-1", name: "Sharma Traders", two_step_granted: false } as Organisation;

  it("asks the super admin first, without a fresh code, and says what changes for the admins", async () => {
    const allow = twoStepConfirm(org, true);
    const stop = twoStepConfirm({ ...org, two_step_granted: true }, false);
    expect(allow.title).toBe("Let Sharma Traders's admins use two-step sign-in?");
    expect(stop.title).toBe("Stop two-step sign-in for Sharma Traders?");
    expect(stop.effect).toBe(
      "Its admins then sign in with their e-mail and password only. Codes already set up are kept for later.",
    );
    expect([allow.stepUp, stop.stepUp, allow.danger, stop.danger]).toEqual([false, false, false, true]);
    const sent: { method: string; url: string; body: unknown }[] = [];
    vi.stubGlobal("fetch", async (url: string, init: RequestInit = {}) => {
      sent.push({ method: init.method ?? "GET", url, body: JSON.parse(String(init.body)) });
      return new Response(JSON.stringify({}), { status: 200 });
    });
    await allow.run("", "");
    await stop.run("", "");
    expect(sent).toEqual([
      { method: "PUT", url: "/api/operator/orgs/org-1/two-step", body: { granted: true } },
      { method: "PUT", url: "/api/operator/orgs/org-1/two-step", body: { granted: false } },
    ]);
  });

  it("names the change in both activity logs, done by Kaagaz for the organisation", () => {
    expect(actionWords("org.two_step_grant")).toBe("Allowed two-step sign-in");
    expect(actionWords("org.two_step_revoke")).toBe("Stopped two-step sign-in");
    expect(describeAction("org.two_step_grant")).toBe("Two-step sign-in allowed");
    expect(describeAction("org.two_step_revoke")).toBe("Two-step sign-in stopped");
    expect(whoText({ action: "org.two_step_grant", actor_name: null, details: {} })).toBe("Kaagaz");
  });

  it("shows the Two-step panel only when allowed, and passes the grant to the users list", () => {
    const view = readFileSync(join(__dirname, "..", "components/admin/AdminView.tsx"), "utf8");
    expect(view).toContain("{me.user.two_step_allowed === true && twoStep}");
    expect(view).toContain("twoStepAllowed={me.user.two_step_allowed === true}");
  });
});
