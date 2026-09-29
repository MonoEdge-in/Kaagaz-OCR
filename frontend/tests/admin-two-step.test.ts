import { describe, expect, it } from "vitest";
import { NETWORK_FAILURE, TOO_MANY_ATTEMPTS } from "@/app/login/signIn";
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
import { ApiError } from "@/lib/api";
import type { Me } from "@/lib/types";

const me = (totpEnabled: boolean): Me => ({
  user: {
    id: "u1",
    email: "asha@sharma.in",
    display_name: "Asha",
    role: "admin",
    must_change_password: false,
    totp_enabled: totpEnabled,
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
