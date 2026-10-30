// Email (#457 phase 0): the link token read from the address's fragment, the words each
// email screen says, and what the sign-in, sign-up and admin screens say about email.

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ORG_PENDING, signInErrorMessage } from "@/app/login/signIn";
import { CHECK_EMAIL, signUpConfirming } from "@/app/signup/signUp";
import { addedNotice } from "@/components/admin/logic";
import { EmailBanner } from "@/components/shell/EmailBanner";
import { orgStatus, waitingOnly } from "@/components/operator/logic";
import { ApiError } from "@/lib/api";
import {
  FORGOT_SENT,
  LINK_GONE,
  LINK_MISSING,
  confirmBanner,
  linkError,
  tokenFromHash,
} from "@/lib/emailLinks";

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
    expect(linkError(new ApiError(404, "not_found", "Not found."))).toBe(LINK_GONE);
    expect(linkError(new ApiError(400, "bad_request", "Use at least 12 characters."))).toBe(
      "Use at least 12 characters.",
    );
    expect(linkError(new TypeError("offline"))).toMatch(/internet connection/);
    expect(LINK_MISSING).toMatch(/link/);
  });

  it("uses the spec's words", () => {
    expect(FORGOT_SENT).toBe("If that email has an account, we've sent a link.");
    // Changed on purpose (#457 follow-up): people made before email had no link sent yet, so
    // the banner says "We sent a link" only once this browser sent one.
    expect(confirmBanner("ramesh@firm.in", false)).toEqual({
      text: "Confirm your email address, ramesh@firm.in.",
      button: "Send link",
    });
    expect(confirmBanner("ramesh@firm.in", true)).toEqual({
      text: "Confirm your email. We sent a link to ramesh@firm.in.",
      button: "Send again",
    });
    expect(CHECK_EMAIL).toBe(
      "Check your email to confirm your address. Your organisation will then wait for approval.",
    );
    expect(addedNotice({ display_name: "Ramesh", email: "ramesh@firm.in", invited: true })).toBe(
      "Invite sent to ramesh@firm.in",
    );
    expect(addedNotice({ display_name: "Ramesh", email: "ramesh@firm.in" })).toBe(
      "Added Ramesh. They choose their own password when they first sign in.",
    );
  });

  it("knows a sign-up that waits for its email", () => {
    expect(signUpConfirming({ status: "confirm_email" })).toBe(true);
    expect(signUpConfirming({ enrol_token: "t", secret: "s", otpauth_uri: "u" })).toBe(false);
  });

  it("tells a waiting organisation why it cannot sign in", () => {
    expect(ORG_PENDING).toBe("Your organisation is waiting for approval.");
    expect(signInErrorMessage(new ApiError(403, "org_pending", "anything"))).toBe(ORG_PENDING);
  });
});

describe("the confirm banner", () => {
  it("offers Send link before any link was sent", () => {
    const html = renderToStaticMarkup(createElement(EmailBanner, { email: "ramesh@firm.in" }));
    expect(html).toContain("Confirm your email address, ramesh@firm.in.");
    expect(html).toContain("Send link");
    expect(html).not.toContain("We sent a link");
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
