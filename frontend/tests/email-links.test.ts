// Email (#457 phase 0): the link token read from the address's fragment, the words each
// email screen says, and what the sign-in, sign-up and admin screens say about email.

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";
import { ORG_PENDING, signInErrorMessage } from "@/app/login/signIn";
import { checkEmail, signUpConfirming } from "@/app/signup/signUp";
import { addedNotice } from "@/components/admin/logic";
import { EmailBanner } from "@/components/shell/EmailBanner";
import { orgStatus, waitingOnly } from "@/components/operator/logic";
import { ApiError } from "@/lib/api";
import {
  EMAIL_OFF,
  FORGOT_SENT,
  LINK_GONE,
  LINK_MISSING,
  SEND_FAILED,
  confirmBanner,
  confirmedText,
  linkError,
  linkGone,
  tokenFromHash,
} from "@/lib/emailLinks";

const read = (path: string) => readFileSync(join(__dirname, "..", path), "utf8");

describe("the token in the fragment", () => {
  it("is read from #t= and nowhere else", () => {
    expect(tokenFromHash("#t=abc_DEF-123")).toBe("abc_DEF-123");
    expect(tokenFromHash("t=abc")).toBe("abc");
    expect(tokenFromHash("#x=1&t=abc")).toBe("abc");
  });

  it("is null when missing, empty or not a token", () => {
    expect(tokenFromHash("")).toBeNull();
    expect(tokenFromHash("#")).toBeNull();
    expect(tokenFromHash("#t=")).toBeNull();
    expect(tokenFromHash("#t=<script>")).toBeNull();
    expect(tokenFromHash(`#t=${"a".repeat(129)}`)).toBeNull();
  });
});

describe("what the email screens say", () => {
  it("says a used, expired or wrong link plainly", () => {
    expect(LINK_GONE).toBe("That link has expired or was already used.");
    expect(linkError(new ApiError(400, "link_expired", "That link has expired or was already used."))).toBe(
      LINK_GONE,
    );
    expect(linkError(new ApiError(400, "bad_request", "Use at least 12 characters."))).toBe(
      "Use at least 12 characters.",
    );
    expect(linkError(new TypeError("offline"))).toMatch(/internet connection/);
    expect(LINK_MISSING).toMatch(/link/);
  });

  it("says what to do next about a gone link, per page (#457 review)", () => {
    const gone = new ApiError(400, "link_expired", "That link has expired or was already used.");
    expect(linkGone("reset")).toBe("That link has expired or was already used. Ask for a new one from Forgot password.");
    expect(linkGone("confirm")).toBe("That link has expired or was already used. Sign in to get a new link.");
    expect(linkError(gone, "reset")).toBe(linkGone("reset"));
    expect(linkError(gone, "confirm")).toBe(linkGone("confirm"));
    const form = read("app/reset-password/LinkPasswordForm.tsx");
    expect(form).toContain("linkError(caught, kind)");
    expect(form).toContain('<Link href="/forgot-password">Forgot password</Link>');
  });

  it("says email is off on a 404, and logs an unexpected failure (#457 review)", () => {
    // Changed on purpose (#457 review): a 404 is email switched off, not a gone link.
    expect(EMAIL_OFF).toBe("Email isn't switched on for Kaagaz yet.");
    expect(linkError(new ApiError(404, "not_found", "Not found."))).toBe(EMAIL_OFF);
    expect(linkError(new ApiError(404, "not_found", "Not found."), "reset")).toBe(EMAIL_OFF);
    const logged = vi.spyOn(console, "error").mockImplementation(() => undefined);
    try {
      expect(linkError(new ApiError(500, "internal_error", "boom"))).toBe(
        "This is not working right now. Try again in a minute.",
      );
      expect(linkError(new Error("odd"))).toBe("This is not working right now. Try again in a minute.");
      expect(logged).toHaveBeenCalledTimes(2);
      linkError(new ApiError(429, "rate_limited", "x"));
      linkError(new TypeError("offline"));
      expect(logged).toHaveBeenCalledTimes(2);
    } finally {
      logged.mockRestore();
    }
  });

  it("says the email is confirmed, and the wait only for a waiting sign-up (#457 review)", () => {
    expect(confirmedText(false)).toBe("Your email is confirmed.");
    expect(confirmedText(true)).toBe(
      "Your email is confirmed. Your organisation is waiting for approval from the Kaagaz team.",
    );
    const page = read("app/email/confirm/ConfirmEmail.tsx");
    expect(page).toContain("confirmedText(reply.pending)");
    // Changed on purpose (issue 468): confirmError, linkError's words with "open the email link again".
    expect(page).toContain("confirmError(caught)");
  });

  it("uses the spec's words", () => {
    expect(FORGOT_SENT).toBe("If that email has an account, we've sent a link.");
    // Changed on purpose (#457 follow-up): people made before email had no link sent yet, so
    // the banner says "We sent a link" only once this browser sent one.
    // Changed on purpose (#457 review): "Confirm your email, {email}." before a link is sent.
    expect(confirmBanner("ramesh@firm.in", false)).toEqual({
      text: "Confirm your email, ramesh@firm.in.",
      button: "Send link",
    });
    expect(confirmBanner("ramesh@firm.in", true)).toEqual({
      text: "Confirm your email. We sent a link to ramesh@firm.in.",
      button: "Send again",
    });
    // Changed on purpose (#457 review): the screen names the address and how long the link
    // works, and says when the link could not be sent.
    expect(checkEmail("asha@sharma.example", true)).toEqual({
      title: "Check your email",
      text: "We sent a link to asha@sharma.example to confirm your address. The link works for 24 hours. Your organisation will then wait for approval.",
    });
    expect(checkEmail("asha@sharma.example", false)).toEqual({
      title: "Confirm your email",
      text: "We couldn't send the confirm link to asha@sharma.example. Try sending it again.",
    });
    // Changed on purpose (#737): the server's own result line; a setup email that did not go is a problem.
    const sent = "Added. We emailed ramesh@firm.in a link to set their password and authenticator. It works for 12 hours.";
    expect(addedNotice({ setup_email_sent: true, message: sent })).toEqual({ text: sent });
    const failed = "Added, but the setup email could not be sent. Use Resend setup email.";
    expect(addedNotice({ setup_email_sent: false, message: failed })).toEqual({ problem: true, text: failed });
  });

  it("knows a sign-up that waits for its email", () => {
    expect(signUpConfirming({ status: "confirm_email", sent: true })).toBe(true);
    expect(signUpConfirming({ enrol_token: "t", secret: "s", otpauth_uri: "u" })).toBe(false);
  });

  it("tells a waiting organisation why it cannot sign in", () => {
    // Changed on purpose (#457 review): who approves it, and the confirm link.
    expect(ORG_PENDING).toBe(
      "Your organisation is waiting for approval from the Kaagaz team. If you haven't confirmed your email yet, use the link we sent you.",
    );
    expect(signInErrorMessage(new ApiError(403, "org_pending", "anything"))).toBe(ORG_PENDING);
    expect(read("app/login/ResendConfirm.tsx")).toContain("Send the confirm link again");
  });

  it("offers the confirm link again on sign-up (#457 review)", () => {
    const form = read("app/signup/SignUpForm.tsx");
    expect(form).toContain("checkEmail(signUpBody(input).email, confirming.sent)");
    expect(form).toContain("<ResendConfirm");
  });
});

describe("the link and result screens (#457 review)", () => {
  it("move focus to the new heading when a form is replaced by its result", () => {
    for (const path of ["app/forgot-password/ForgotForm.tsx", "app/reset-password/LinkPasswordForm.tsx", "app/signup/SignUpForm.tsx"]) {
      const form = read(path);
      expect(form, path).toContain("const focusOnMount = useCallback((element: HTMLHeadingElement | null) => element?.focus(), []);");
      expect(form, path).toMatch(/<h1 ref=\{focusOnMount\} tabIndex=\{-1\}/);
    }
  });

  it("say a missing token as soon as the password page loads", () => {
    const form = read("app/reset-password/LinkPasswordForm.tsx");
    expect(form).toMatch(/token\.current = takeToken\(\);\s*if \(token\.current === null\) setFailure\(LINK_MISSING\);/);
  });

  it("check the new password with Change password's rules", () => {
    const form = read("app/reset-password/LinkPasswordForm.tsx");
    expect(form).toContain("validateNewPassword(");
    expect(form).not.toContain("MIN_PASSWORD_LENGTH ? NEXT_TOO_SHORT");
  });
});

describe("one spelling (#457 review)", () => {
  it("says email, never e-mail, on the sign-in, sign-up, forgot password and link pages", () => {
    const pages = [
      "app/login/SignInForm.tsx",
      "app/login/ProviderCodeStep.tsx",
      "app/login/signIn.ts",
      "app/forgot-password/ForgotForm.tsx",
      "app/reset-password/LinkPasswordForm.tsx",
      "app/email/confirm/ConfirmEmail.tsx",
      "app/signup/SignUpForm.tsx",
      "app/signup/signUp.ts",
    ];
    for (const path of pages) {
      const code = read(path)
        .split("\n")
        .filter((line) => !/^\s*(\/\/|\*|\/\*)/.test(line));
      expect(code.filter((line) => /e-mail/i.test(line)), path).toEqual([]);
    }
    expect(read("app/login/SignInForm.tsx")).toContain('"Email (temporary legacy sign-in)"');
  });
});

describe("the confirm banner", () => {
  it("offers Send link before any link was sent", () => {
    const html = renderToStaticMarkup(createElement(EmailBanner, { email: "ramesh@firm.in" }));
    expect(html).toContain("Confirm your email, ramesh@firm.in.");
    expect(html).toContain("Send link");
    expect(html).not.toContain("We sent a link");
  });

  it("says when the link could not be sent, and no extra sentence once it went (#457 review)", () => {
    expect(SEND_FAILED).toBe("We couldn't send the email. Try again in a minute.");
    const banner = read("components/shell/EmailBanner.tsx");
    expect(banner).toContain("reply.sent");
    expect(banner).toContain("SEND_FAILED");
    expect(banner).not.toContain("SENT_AGAIN");
  });
});

describe("the operator's waiting organisations", () => {
  const org = (status: "active" | "pending", confirmed: boolean) => ({
    name: status,
    status,
    delete_after: null,
    admin_email_confirmed: confirmed,
  });

  it("names the wait and whether the email is confirmed", () => {
    expect(orgStatus(org("pending", false))).toEqual({
      text: "Waiting for approval",
      tone: "working",
      note: "Email not confirmed yet",
    });
    expect(orgStatus(org("pending", true)).note).toBe("Email confirmed");
  });

  it("filters to the ones waiting", () => {
    const all = [org("active", true), org("pending", false), org("pending", true)];
    expect(waitingOnly(all, true)).toEqual([all[1], all[2]]);
    expect(waitingOnly(all, false)).toEqual(all);
  });
});
