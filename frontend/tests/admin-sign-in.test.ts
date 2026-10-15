import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  ADMIN_SIGN_IN_ADVICE,
  ADMIN_SIGN_IN_FAILED,
  CHANGE_PASSWORD_FIRST,
  CODE_INVALID,
  CODE_MISSING,
  ENROL_FAILED,
  ENROL_ISSUER,
  ENROL_STEPS,
  adminSignInError,
  codeError,
  enrolDetails,
  enrolErrorMessage,
  firstInvalidAdmin,
  groupSecret,
  normalizeCode,
  secretForCopy,
  validateAdminSignIn,
} from "@/app/admin/login/adminSignIn";
import { NETWORK_FAILURE, SIGN_IN_DOWN, TOO_MANY_ATTEMPTS } from "@/app/login/signIn";
import {
  AGAIN_MISMATCH,
  CURRENT_MISSING,
  CURRENT_WRONG,
  NEW_REFUSED,
  NEXT_SAME,
  NEXT_TOO_SHORT,
  SESSION_ENDED,
  firstInvalidNewPassword,
  setPasswordError,
  validateNewPassword,
} from "@/components/shell/setPassword";
import { ApiError } from "@/lib/api";

describe("the 6-digit code", () => {
  it("accepts 6 ASCII digits", () => {
    expect(codeError("123456")).toBeNull();
    expect(codeError("000000")).toBeNull();
  });

  it("allows the spaces authenticator apps show inside the code", () => {
    expect(normalizeCode(" 123 456 ")).toBe("123456");
    expect(codeError("123 456")).toBeNull();
  });

  it("asks for the code when it is empty", () => {
    expect(codeError("")).toBe(CODE_MISSING);
    expect(codeError("   ")).toBe(CODE_MISSING);
  });

  it("refuses the wrong length", () => {
    expect(codeError("12345")).toBe(CODE_INVALID);
    expect(codeError("1234567")).toBe(CODE_INVALID);
  });

  it("refuses Devanagari digits", () => {
    expect(codeError("१२३४५६")).toBe(CODE_INVALID);
    expect(codeError("12३456")).toBe(CODE_INVALID);
  });

  it("refuses fullwidth digits", () => {
    expect(codeError("１２３４５６")).toBe(CODE_INVALID);
  });

  it("refuses other scripts' digits, letters and signs", () => {
    expect(codeError("٠١٢٣٤٥")).toBe(CODE_INVALID);
    expect(codeError("১২৩৪৫৬")).toBe(CODE_INVALID);
    expect(codeError("12345a")).toBe(CODE_INVALID);
    expect(codeError("+12345")).toBe(CODE_INVALID);
    expect(codeError("123-456")).toBe(CODE_INVALID);
  });

  it("says what to type, in plain words", () => {
    expect(CODE_MISSING).toBe("Enter the 6-digit code from your authenticator app.");
    expect(CODE_INVALID).toBe("The code is 6 digits, each 0 to 9. Type it again from the app.");
  });
});

describe("validateAdminSignIn and firstInvalidAdmin", () => {
  it("lets the code be empty (an admin who has not turned on two-step sign-in)", () => {
    expect(validateAdminSignIn("asha@sharma.in", "a long password", "")).toEqual({
      email: null,
      password: null,
      code: null,
    });
  });

  it("checks a code once one is typed", () => {
    expect(validateAdminSignIn("asha@sharma.in", "pw", "१२३४५६").code).toBe(CODE_INVALID);
  });

  it("sends focus to the first field that needs fixing, in form order", () => {
    expect(firstInvalidAdmin(validateAdminSignIn("", "", "12"))).toBe("email");
    expect(firstInvalidAdmin(validateAdminSignIn("asha@sharma.in", "", "12"))).toBe("password");
    expect(firstInvalidAdmin(validateAdminSignIn("asha@sharma.in", "pw", "12"))).toBe("code");
    expect(firstInvalidAdmin(validateAdminSignIn("asha@sharma.in", "pw", "123456"))).toBeNull();
  });
});

describe("the setup key", () => {
  it("shows the key in groups of 4", () => {
    expect(groupSecret("JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP")).toBe(
      "JBSW Y3DP EHPK 3PXP JBSW Y3DP EHPK 3PXP",
    );
  });

  it("gives the same groups when the server already grouped it", () => {
    expect(groupSecret("JBSW Y3DP EHPK 3PXP")).toBe("JBSW Y3DP EHPK 3PXP");
    expect(groupSecret(" jbsw-y3dp ehpk3pxp ")).toBe("JBSW Y3DP EHPK 3PXP");
  });

  it("keeps a short last group", () => {
    expect(groupSecret("ABCDEFGHIJ")).toBe("ABCD EFGH IJ");
    expect(groupSecret("")).toBe("");
  });

  it("copies the key without spaces", () => {
    expect(secretForCopy("JBSW Y3DP EHPK 3PXP")).toBe("JBSWY3DPEHPK3PXP");
  });

  it("names the issuer Kaagaz and the account by the e-mail", () => {
    expect(ENROL_ISSUER).toBe("Kaagaz");
    expect(enrolDetails(" asha@sharma.in ")).toEqual([
      { label: "Issuer", value: "Kaagaz" },
      { label: "Account", value: "asha@sharma.in" },
      { label: "Type", value: "Time based" },
    ]);
  });

  it("explains the set-up in 3 short steps, scanning the QR code first", () => {
    expect(ENROL_STEPS).toHaveLength(3);
    for (const step of ENROL_STEPS) expect(step.length).toBeLessThan(110);
    expect(ENROL_STEPS[1]).toMatch(/^Scan the QR code/);
  });
});

describe("adminSignInError", () => {
  it("shows the server's one refusal text, with its lock time, then the advice", () => {
    const said = "E-mail or password is wrong, or the account is locked. Check them, or wait 7 minutes, then try again.";
    expect(adminSignInError(new ApiError(401, "bad_credentials", said))).toEqual({
      message: `${said} ${ADMIN_SIGN_IN_ADVICE}`,
      changePasswordFirst: false,
    });
  });

  it("says the same thing for every other refusal, whatever the server said", () => {
    const refusals = [
      new ApiError(401, "bad_credentials", "  "),
      new ApiError(401, "not_signed_in", "Please sign in."),
      new ApiError(403, "forbidden", "You don't have permission to do this."),
      new ApiError(403, "admin_session_required", "Sign in on the admin screen to use this."),
      new ApiError(400, "bad_request", "The request is not valid."),
    ];
    const results = refusals.map((caught) => adminSignInError(caught));
    expect(new Set(results.map((result) => result.message))).toEqual(
      new Set([ADMIN_SIGN_IN_FAILED]),
    );
    expect(results.every((result) => !result.changePasswordFirst)).toBe(true);
  });

  it("uses the PRD wording, names no lock time of its own and never says which part was wrong", () => {
    // Changed on purpose (UI quality council, 15 Oct 2026): spelt "E-mail", like the field.
    expect(ADMIN_SIGN_IN_FAILED).toMatch(/^E-mail or password is wrong, or the account is locked\. /);
    expect(ADMIN_SIGN_IN_FAILED).toContain(ADMIN_SIGN_IN_ADVICE);
    expect(ADMIN_SIGN_IN_FAILED).not.toMatch(/[0-9]/);
    expect(ADMIN_SIGN_IN_FAILED).not.toMatch(/code is wrong|unknown|no account|not an admin/i);
  });

  it("sends an admin with a temporary password to set a new one first", () => {
    const caught = new ApiError(403, "password_change_required", "Set a new password to continue.");
    expect(adminSignInError(caught)).toEqual({
      message: CHANGE_PASSWORD_FIRST,
      changePasswordFirst: true,
    });
  });

  it("says to wait after too many requests, and names network trouble", () => {
    expect(adminSignInError(new ApiError(429, "rate_limited", "x")).message).toBe(TOO_MANY_ATTEMPTS);
    expect(adminSignInError(new ApiError(0, "network", "x")).message).toBe(NETWORK_FAILURE);
    expect(adminSignInError(new TypeError("Failed to fetch")).message).toBe(NETWORK_FAILURE);
  });

  it("never shows a raw server error", () => {
    expect(adminSignInError(new ApiError(500, "error", "Traceback ...")).message).toBe(SIGN_IN_DOWN);
    expect(adminSignInError(new Error("boom")).message).toBe(SIGN_IN_DOWN);
  });
});

describe("enrolErrorMessage", () => {
  it("says the same thing for a wrong code and an expired set-up", () => {
    expect(enrolErrorMessage(new ApiError(401, "bad_credentials", "a"))).toBe(ENROL_FAILED);
    expect(enrolErrorMessage(new ApiError(401, "not_signed_in", "b"))).toBe(ENROL_FAILED);
    expect(enrolErrorMessage(new ApiError(400, "bad_request", "c"))).toBe(ENROL_FAILED);
    expect(enrolErrorMessage(new ApiError(403, "forbidden", "d"))).toBe(ENROL_FAILED);
  });

  it("names network trouble and never shows a raw server error", () => {
    expect(enrolErrorMessage(new TypeError("Failed to fetch"))).toBe(NETWORK_FAILURE);
    expect(enrolErrorMessage(new ApiError(429, "rate_limited", "x"))).toBe(TOO_MANY_ATTEMPTS);
    expect(enrolErrorMessage(new ApiError(502, "error", "Bad gateway"))).toBe(SIGN_IN_DOWN);
  });
});

describe("set a new password", () => {
  const good = { current: "Temp-Pass-1234", next: "my own new password", again: "my own new password" };

  it("accepts a new password of 12 or more characters, typed twice", () => {
    expect(validateNewPassword(good)).toEqual({});
    expect(validateNewPassword({ ...good, next: "twelvechars!", again: "twelvechars!" })).toEqual({});
  });

  it("names each problem against its field", () => {
    expect(validateNewPassword({ current: "", next: "short", again: "other" })).toEqual({
      current: CURRENT_MISSING,
      next: NEXT_TOO_SHORT,
      again: AGAIN_MISMATCH,
    });
    expect(NEXT_TOO_SHORT).toBe("Use at least 12 characters.");
  });

  it("refuses keeping the password you signed in with", () => {
    expect(
      validateNewPassword({ current: "Temp-Pass-1234", next: "Temp-Pass-1234", again: "Temp-Pass-1234" })
        .next,
    ).toBe(NEXT_SAME);
  });

  it("compares exactly as typed: no trimming", () => {
    expect(validateNewPassword({ ...good, again: `${good.next} ` }).again).toBe(AGAIN_MISMATCH);
  });

  it("sends focus to the first field that needs fixing", () => {
    expect(firstInvalidNewPassword({ again: "x", current: "y" })).toBe("current");
    expect(firstInvalidNewPassword({ again: "x", next: "y" })).toBe("next");
    expect(firstInvalidNewPassword({ again: "x" })).toBe("again");
    expect(firstInvalidNewPassword({})).toBeNull();
  });

  it("maps what the server says", () => {
    expect(setPasswordError(new ApiError(401, "bad_credentials", "x"))).toEqual({
      message: CURRENT_WRONG,
      signedOut: false,
    });
    expect(setPasswordError(new ApiError(401, "not_signed_in", "x"))).toEqual({
      message: SESSION_ENDED,
      signedOut: true,
    });
    // The route's 400s name the problem in plain words (a wrong current
    // password, a lock, the same password again); they are shown as sent.
    expect(
      setPasswordError(new ApiError(400, "bad_request", "Your current password is not right.")),
    ).toEqual({ message: "Your current password is not right.", signedOut: false });
    expect(setPasswordError(new ApiError(400, "bad_request", "  ")).message).toBe(NEW_REFUSED);
    expect(setPasswordError(new ApiError(429, "rate_limited", "x")).message).toBe(TOO_MANY_ATTEMPTS);
    expect(setPasswordError(new TypeError("Failed to fetch")).message).toBe(NETWORK_FAILURE);
    expect(setPasswordError(new ApiError(500, "error", "Traceback")).message).toBe(SIGN_IN_DOWN);
  });
});

// The forms' security details live in markup, so they are checked in the source.
describe("admin sign-in markup", () => {
  const read = (path: string) => readFileSync(join(__dirname, "..", path), "utf8");
  const withoutIcons = (text: string) => text.replace(/<Icon\b[^>]*>/g, "");
  const form = read("app/admin/login/AdminSignInForm.tsx");
  const enrol = read("app/admin/login/EnrolStep.tsx");
  const code = read("app/admin/login/CodeField.tsx");
  const panel = read("app/(app)/admin/TwoStepPanel.tsx");
  const setPassword = read("components/shell/SetPasswordScreen.tsx");

  it("posts (never GET), does its own validation and names no field", () => {
    for (const source of [form, enrol, panel, setPassword]) {
      expect(source).toMatch(/<form\s[^>]*method="post"/);
      expect(source).toMatch(/<form\s[^>]*noValidate/);
    }
    for (const source of [form, enrol, code, panel, setPassword]) {
      expect(withoutIcons(source)).not.toMatch(/\sname=/);
    }
  });

  it("has no forced set-up step at sign-in, and the code is optional", () => {
    expect(form).not.toContain("EnrolStep");
    expect(form).toContain('label="6-digit code, if you turned on two-step sign-in"');
  });

  it("shows the set-up link as a QR code above the typed key, never as text", () => {
    expect(enrol).toContain("<QrCode value={enrol.otpauth_uri}");
    expect(enrol.indexOf("<QrCode")).toBeLessThan(enrol.indexOf("groupSecret(enrol.secret)"));
    // The link holds the secret: its one use is as the QR value, never as text.
    expect(enrol.split("enrol.otpauth_uri")).toHaveLength(2);
  });

  it("asks for the current password, masked again on submit, to turn 2FA on or off", () => {
    expect(panel).toContain("<EnrolStep");
    expect(panel).toContain("startTwoStep(password)");
    expect(panel).toContain("turnOffTwoStep(password)");
    expect(panel.match(/<PasswordField\b/g)).toHaveLength(1);
    const submit = panel.slice(panel.indexOf("async function submit"));
    expect(submit.indexOf("setMasked(true)")).toBeGreaterThan(-1);
    expect(submit.indexOf("setMasked(true)")).toBeLessThan(submit.indexOf("currentPasswordError("));
    expect(panel).toContain("disabled={!hydrated || busy}");
  });

  it("asks for the code with the number keyboard and one-time-code autofill", () => {
    expect(code).toContain('inputMode="numeric"');
    expect(code).toContain('autoComplete="one-time-code"');
    expect(code).toContain("spellCheck={false}");
    expect(form).toContain("<CodeField");
    expect(enrol).toContain("<CodeField");
  });

  it("reuses PasswordField and masks it again on submit", () => {
    expect(form).toContain("<PasswordField");
    const submit = form.slice(form.indexOf("async function submit"));
    expect(submit.indexOf("setMasked(true)")).toBeGreaterThan(-1);
    expect(submit.indexOf("setMasked(true)")).toBeLessThan(submit.indexOf("validateAdminSignIn("));
    expect(setPassword.match(/<PasswordField\b/g)).toHaveLength(3);
  });

  it("keeps the buttons disabled only until hydrated, then only while busy", () => {
    expect(form).toContain("disabled={!hydrated || busy}");
    expect(enrol).toContain("disabled={!hydrated || busy}");
  });

  it("links from the normal sign-in page to admin sign-in", () => {
    const login = read("app/login/page.tsx");
    expect(login).toMatch(/href="\/admin\/login"/);
    expect(login).toContain("Admin sign-in");
  });
});
