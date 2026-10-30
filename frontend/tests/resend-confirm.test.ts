// "Send the link again" without signing in (#457 phase 0 follow-up): a sign-up's admin cannot
// sign in while the organisation waits, so the sign-up's "Check your email" screen and the
// sign-in page's "waiting for approval" answer offer a new link by e-mail address.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ResendConfirm } from "@/app/login/ResendConfirm";
import { orgPending } from "@/app/login/signIn";
import { ApiError } from "@/lib/api";
import { RESEND_SENT } from "@/lib/emailLinks";

const read = (path: string) => readFileSync(join(__dirname, "..", path), "utf8");

describe("Send the link again", () => {
  it("says the server's same words whoever the email belongs to", () => {
    expect(RESEND_SENT).toBe("If that email is waiting to be confirmed, we've sent a new link.");
    expect(read("lib/api.ts")).toContain('"/auth/email/resend-confirm", { email }');
  });

  it("is one button", () => {
    const html = renderToStaticMarkup(createElement(ResendConfirm, { email: "ramesh@example.com" }));
    expect(html).toContain('type="button"');
    expect(html).toContain("Send the link again");
  });

  it("shows on the sign-up's Check your email screen", () => {
    const form = read("app/signup/SignUpForm.tsx");
    expect(form).toContain("<ResendConfirm email={signUpBody(input).email} />");
  });

  it("shows on the sign-in page only when the organisation is waiting", () => {
    expect(orgPending(new ApiError(403, "org_pending", "x"))).toBe(true);
    expect(orgPending(new ApiError(401, "bad_credentials", "x"))).toBe(false);
    expect(orgPending(new TypeError("offline"))).toBe(false);
    const form = read("app/login/SignInForm.tsx");
    expect(form).toContain("setPending(orgPending(caught));");
    expect(form).toContain("{pending && <ResendConfirm email={normalizeEmail(email)} />}");
  });
});
