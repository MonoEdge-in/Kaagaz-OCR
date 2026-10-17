import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { NETWORK_FAILURE } from "@/app/login/signIn";
import {
  ACCOUNT_EXISTS,
  EMAIL_INVALID,
  EMAIL_MISSING,
  NAME_MISSING,
  ORGANISATION_MISSING,
  PASSWORD_HINT,
  PASSWORD_TOO_SHORT,
  SIGN_UP_DOWN,
  SIGN_UP_OFF,
  SIGN_UP_REFUSED,
  TOO_MANY_SIGN_UPS,
  firstInvalidSignUp,
  signUpBody,
  signUpError,
  validateSignUp,
} from "@/app/signup/signUp";
import { ApiError, signUp, signUpOpen } from "@/lib/api";

const read = (path: string) => readFileSync(join(__dirname, "..", path), "utf8");

const GOOD = {
  organisation: "Sharma Traders",
  name: "Asha Sharma",
  email: "asha@sharma.example",
  password: "correct horse battery",
};

describe("sign-up form rules", () => {
  it("accepts a filled-in form", () => {
    const errors = validateSignUp(GOOD);
    expect(errors).toEqual({ organisation: null, name: null, email: null, password: null });
    expect(firstInvalidSignUp(errors)).toBeNull();
  });

  it("asks for every missing field, and focus goes to the first in screen order", () => {
    const errors = validateSignUp({ organisation: "  ", name: "", email: " ", password: "" });
    expect(errors).toEqual({
      organisation: ORGANISATION_MISSING,
      name: NAME_MISSING,
      email: EMAIL_MISSING,
      password: PASSWORD_TOO_SHORT,
    });
    expect(firstInvalidSignUp(errors)).toBe("organisation");
    expect(firstInvalidSignUp(validateSignUp({ ...GOOD, email: "x" }))).toBe("email");
  });

  it("checks the e-mail as the server does: an @ and a dot after it", () => {
    expect(validateSignUp({ ...GOOD, email: "asha@sharma" }).email).toBe(EMAIL_INVALID);
    expect(validateSignUp({ ...GOOD, email: "as ha@sharma.example" }).email).toBe(EMAIL_INVALID);
    expect(validateSignUp({ ...GOOD, email: "  asha@sharma.example " }).email).toBeNull();
  });

  it("needs a password of at least 12 characters, counted as typed, with the same hint", () => {
    expect(PASSWORD_TOO_SHORT).toBe("Use at least 12 characters.");
    expect(PASSWORD_HINT).toBe("At least 12 characters.");
    expect(validateSignUp({ ...GOOD, password: "eleven char" }).password).toBe(PASSWORD_TOO_SHORT);
    expect(validateSignUp({ ...GOOD, password: "  ten  ch  " }).password).toBe(PASSWORD_TOO_SHORT);
    expect(validateSignUp({ ...GOOD, password: "twelve chars" }).password).toBeNull();
  });

  it("trims the names and e-mail it sends, never the password", () => {
    expect(
      signUpBody({ organisation: " Sharma Traders ", name: " Asha ", email: " a@b.example ", password: " pass word  " }),
    ).toEqual({ organisation_name: "Sharma Traders", name: "Asha", email: "a@b.example", password: " pass word  " });
  });
});

describe("what a failed sign-up says", () => {
  it("says an existing account plainly and offers sign-in", () => {
    expect(signUpError(new ApiError(409, "conflict", "That e-mail is already in use."))).toEqual({
      message: ACCOUNT_EXISTS,
      signInInstead: true,
    });
    expect(ACCOUNT_EXISTS).toBe("An account with this e-mail already exists. Sign in instead.");
  });

  it("names the hourly limit, the switch, bad details and the network", () => {
    expect(signUpError(new ApiError(429, "rate_limited", "x")).message).toBe(TOO_MANY_SIGN_UPS);
    expect(TOO_MANY_SIGN_UPS).toBe("Too many sign-ups from here. Try again in an hour.");
    expect(signUpError(new ApiError(404, "not_found", "x")).message).toBe(SIGN_UP_OFF);
    expect(signUpError(new ApiError(400, "bad_request", "x")).message).toBe(SIGN_UP_REFUSED);
    expect(signUpError(new ApiError(0, "error", "x")).message).toBe(NETWORK_FAILURE);
    expect(signUpError(new TypeError("Failed to fetch")).message).toBe(NETWORK_FAILURE);
    expect(signUpError(new ApiError(503, "backend_unavailable", "x")).message).toBe(SIGN_UP_DOWN);
    expect(signUpError(new Error("?")).signInInstead).toBe(false);
  });

  it("never mentions money", () => {
    const words = [ACCOUNT_EXISTS, TOO_MANY_SIGN_UPS, SIGN_UP_OFF, SIGN_UP_REFUSED, SIGN_UP_DOWN].join(" ");
    expect(words).not.toMatch(/cost|price|budget|money|pay|plan/i);
  });
});

describe("the sign-up calls", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("posts the body with the CSRF header and reads whether sign-up is on", async () => {
    const fetch = vi.fn(async () => new Response(JSON.stringify({ enabled: false }), { status: 200 }));
    vi.stubGlobal("fetch", fetch);
    expect(await signUpOpen()).toEqual({ enabled: false });
    await signUp(signUpBody(GOOD));
    const calls = fetch.mock.calls as unknown as [string, RequestInit][];
    expect(calls[0]?.[0]).toBe("/api/auth/signup");
    expect(calls[0]?.[1].method).toBe("GET");
    expect(calls[1]?.[1].method).toBe("POST");
    expect(calls[1]?.[1].headers).toMatchObject({ "X-Requested-With": "kaagaz" });
    expect(JSON.parse(String(calls[1]?.[1].body))).toEqual({
      organisation_name: "Sharma Traders",
      name: "Asha Sharma",
      email: "asha@sharma.example",
      password: "correct horse battery",
    });
  });

  it("turns a 409 reply into the account-exists banner", async () => {
    const reply = { error: { code: "conflict", message: "That e-mail is already in use." } };
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(reply), { status: 409 })));
    const caught = await signUp(signUpBody(GOOD)).catch((error: unknown) => error);
    expect(signUpError(caught)).toEqual({ message: ACCOUNT_EXISTS, signInInstead: true });
  });
});

describe("the pages", () => {
  it("links the sign-in to /signup only while the server says it is on", () => {
    expect(read("app/login/page.tsx")).toContain("<SignUpLink />");
    const link = read("app/login/SignUpLink.tsx");
    expect(link).toContain('href="/signup"');
    expect(link).toContain("setOpen(reply.enabled)");
    expect(link).toContain("useState(false)"); // hidden until the server answers
  });

  // Changed on purpose (MFA required, 16 Oct 2026): two-step set-up first, then /admin.
  it("sets up two-step sign-in once signed up, then goes to the admin area", () => {
    const form = read("app/signup/SignUpForm.tsx");
    expect(form).toContain('next="/admin"');
    expect(form).toContain('autoComplete="new-password"');
    expect(form).toContain("hint={PASSWORD_HINT}");
  });
});

// N3 (QA 17 Oct): /signup showed the form while sign-up was switched off.
describe("the sign-up page while sign-up is off", () => {
  it("asks the server first, and shows the off words with a way back to sign in instead of the form", () => {
    const form = read("app/signup/SignUpForm.tsx");
    expect(form).toMatch(/signUpOpen\(\)\s*\.then\(\(reply\) => \{\s*if \(live\) setOff\(!reply\.enabled\);/);
    // CodeRabbit (17 Oct): null while asking, so the form never flashes before an "off" reply.
    expect(form).toContain("useState<boolean | null>(null)");
    const off = form.slice(form.indexOf("if (off !== false) {"), form.indexOf("function textField("));
    expect(off).toContain("{off && <p className={login.lead}>{SIGN_UP_OFF}</p>}");
    expect(off).toContain('<Link href="/login">Sign in</Link>');
    expect(off).not.toContain("<form");
  });
});
