import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  EMAIL_INVALID,
  EMAIL_MISSING,
  emailError,
  firstInvalid,
  NETWORK_FAILURE,
  normalizeEmail,
  PASSWORD_MISSING,
  passwordError,
  SIGN_IN_DOWN,
  signInErrorMessage,
  TOO_MANY_ATTEMPTS,
  validateSignIn,
  WRONG_DETAILS_ADVICE,
} from "@/app/login/signIn";
import { ApiError } from "@/lib/api";

describe("e-mail", () => {
  it("asks for an e-mail when empty or only spaces", () => {
    expect(EMAIL_MISSING).toBe("Enter your e-mail address.");
    expect(emailError("")).toBe(EMAIL_MISSING);
    expect(emailError("   ")).toBe(EMAIL_MISSING);
  });

  it("asks for an @ when the shape is wrong", () => {
    expect(EMAIL_INVALID).toBe("Enter an e-mail address with an @, like the one you use for work.");
    expect(emailError("owner.example.in")).toBe(EMAIL_INVALID);
    expect(emailError("owner@")).toBe(EMAIL_INVALID);
    expect(emailError("@example.in")).toBe(EMAIL_INVALID);
    expect(emailError("own er@example.in")).toBe(EMAIL_INVALID);
  });

  it("accepts a normal address, with stray spaces around it", () => {
    expect(emailError("owner@example.in")).toBeNull();
    expect(emailError("  owner@example.in ")).toBeNull();
    expect(normalizeEmail("  owner@example.in ")).toBe("owner@example.in");
  });
});

describe("password", () => {
  it("asks for a password only when it is empty", () => {
    expect(PASSWORD_MISSING).toBe("Enter your password.");
    expect(passwordError("")).toBe(PASSWORD_MISSING);
  });

  it("accepts any characters, spaces included, as typed", () => {
    expect(passwordError(" ")).toBeNull();
    expect(passwordError("####")).toBeNull();
    expect(passwordError(String.raw`#$%&'"\ x`)).toBeNull();
  });
});

describe("validateSignIn and firstInvalid", () => {
  it("sends focus to the first field that needs fixing, in form order", () => {
    expect(firstInvalid(validateSignIn("", ""))).toBe("email");
    expect(firstInvalid(validateSignIn("owner@example.in", ""))).toBe("password");
    expect(firstInvalid(validateSignIn("nope", "secret"))).toBe("email");
    expect(firstInvalid(validateSignIn("owner@example.in", "secret"))).toBeNull();
  });
});

describe("signInErrorMessage", () => {
  it("shows the backend's 401 message, then what to do", () => {
    const wrong = new ApiError(401, "invalid_credentials", "Wrong e-mail or password.");
    expect(signInErrorMessage(wrong)).toBe(
      "Wrong e-mail or password. Check your e-mail and password, then try again.",
    );
    expect(signInErrorMessage(new ApiError(401, "x", "  "))).toBe(WRONG_DETAILS_ADVICE);
  });

  it("says to wait after too many attempts", () => {
    expect(signInErrorMessage(new ApiError(429, "rate_limited", "Slow down"))).toBe(
      TOO_MANY_ATTEMPTS,
    );
    expect(TOO_MANY_ATTEMPTS).toBe("Too many attempts. Wait a minute, then try again.");
  });

  it("names a network failure", () => {
    expect(signInErrorMessage(new TypeError("Failed to fetch"))).toBe(NETWORK_FAILURE);
    expect(signInErrorMessage(new ApiError(0, "network", "x"))).toBe(NETWORK_FAILURE);
    expect(NETWORK_FAILURE).toBe(
      "Could not reach Kaagaz. Check your internet connection and try again.",
    );
  });

  it("says sign-in is down for anything else, never the raw error", () => {
    expect(signInErrorMessage(new ApiError(500, "error", "Traceback ..."))).toBe(SIGN_IN_DOWN);
    expect(signInErrorMessage(new ApiError(502, "error", "Bad gateway"))).toBe(SIGN_IN_DOWN);
    expect(signInErrorMessage(new Error("boom"))).toBe(SIGN_IN_DOWN);
    expect(signInErrorMessage("??")).toBe(SIGN_IN_DOWN);
    expect(SIGN_IN_DOWN).toBe("Sign-in is not working right now. Try again in a minute.");
  });
});

// The form's security details live in markup, so they are checked in the source.
describe("sign-in form markup", () => {
  const read = (path: string) => readFileSync(join(__dirname, "..", path), "utf8");
  const form = read("app/login/SignInForm.tsx");
  const field = read("components/ui/PasswordField.tsx");

  it("posts (never GET) and does its own validation", () => {
    expect(form).toMatch(/<form\s[^>]*method="post"/);
    expect(form).toMatch(/<form\s[^>]*noValidate/);
  });

  it("gives no field a name, so nothing can be submitted without the app", () => {
    // <Icon name="..."> picks a drawing; it is not a form field.
    const withoutIcons = (text: string) => text.replace(/<Icon\b[^>]*>/g, "");
    expect(withoutIcons(form)).not.toMatch(/\sname=/);
    expect(withoutIcons(field)).not.toMatch(/\sname=/);
  });

  it("keeps the button disabled only until hydrated, then only while busy", () => {
    expect(form).toContain("disabled={!hydrated || busy}");
    expect(read("components/ui/useHydrated.ts")).toContain("useSyncExternalStore");
  });

  it("masks the password again as soon as the form is submitted", () => {
    const submit = form.slice(form.indexOf("async function submit"));
    const masks = submit.indexOf("setMasked(true)");
    expect(masks).toBeGreaterThan(-1);
    expect(masks).toBeLessThan(submit.indexOf("validateSignIn("));
  });

  it("turns off spellcheck and autocorrect and never caps or trims the password", () => {
    expect(field).toContain("spellCheck={false}");
    expect(field).toContain('autoCorrect="off"');
    expect(field).toContain('autoCapitalize="none"');
    expect(field).not.toMatch(/maxLength/);
    expect(field).not.toMatch(/\.trim\(/);
    expect(form).toContain("login(normalizeEmail(email), password)");
  });

  it("uses a plain Show/Hide button, not a pressed toggle", () => {
    expect(field).not.toMatch(/aria-pressed/);
    expect(field).toContain("aria-controls={id}");
    expect(field).toContain('type="button"');
  });
});
