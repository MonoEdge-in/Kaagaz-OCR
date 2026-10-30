// Account, Ways to sign in (#457 phase 1): each row's words, the notice after coming back
// from Google, the Connect dialog's code for anyone with codes on, the disconnect question,
// and the app's "Google connected on 30 Oct." banner. The spec's copy, exactly.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  INTRO,
  TITLE,
  bannerText,
  disconnectQuestion,
  needsCode,
  pageNotice,
  passwordRow,
  rowText,
} from "@/components/account/signInWays";
import type { ConnectedIdentity, Me } from "@/lib/types";

const root = join(__dirname, "..");
const source = (path: string) => readFileSync(join(root, path), "utf8");

function identity(changes: Partial<ConnectedIdentity> = {}): ConnectedIdentity {
  return {
    id: "i-1",
    provider: "google",
    email: "ramesh.k@gmail.com",
    status: "approved",
    linked_at: "2026-10-30T06:00:00Z",
    last_used_at: null,
    ...changes,
  };
}

function me(role: Me["user"]["role"], totp: boolean): Me {
  return {
    user: {
      id: "u",
      email: "ramesh@firm.in",
      display_name: "Ramesh",
      role,
      must_change_password: false,
      totp_enabled: totp,
      can_delete_forever: false,
      email_confirmed: true,
    },
    customer: { id: "c", name: "Firm" },
    session: { scope: role === "admin" ? "admin" : "app" },
  };
}

describe("the page", () => {
  it("is titled Ways to sign in with the spec's line", () => {
    expect(TITLE).toBe("Ways to sign in");
    expect(INTRO).toBe("Sign in with Google or Microsoft as well as your password.");
    expect(source("app/(app)/settings/sign-in/page.tsx")).toContain("<WaysToSignIn");
  });

  it("is in the account menu", () => {
    const menu = source("components/shell/AccountMenu.tsx");
    expect(menu).toContain('href="/settings/sign-in"');
    expect(menu).toContain("<span>Ways to sign in</span>");
  });
});

describe("rowText", () => {
  it("says each row as the spec writes it", () => {
    expect(passwordRow("ramesh@firm.in")).toBe("Email and password: ramesh@firm.in");
    expect(rowText("google", null)).toBe("Google: Not connected");
    expect(rowText("google", identity())).toBe("Google: ramesh.k@gmail.com, connected 30 Oct 2026");
    expect(rowText("google", identity({ status: "pending" }))).toBe(
      "Google: ramesh.k@gmail.com, Waiting for your admin's approval",
    );
  });
});

describe("pageNotice", () => {
  it("says what happened after coming back from Google", () => {
    expect(pageNotice({ connected: "google" }, "ramesh.k@gmail.com")).toEqual({
      kind: "done",
      text: "Google connected. You can now sign in with ramesh.k@gmail.com.",
    });
    expect(pageNotice({ waiting: "google" })).toEqual({ kind: "done", text: "Sent to your admin for approval." });
    expect(pageNotice({ error: "taken" })).toEqual({
      kind: "error",
      text: "This Google account already belongs to a Kaagaz user.",
    });
    expect(pageNotice({ error: "signed_out" })?.text).toBe("Sign in again, then connect Google.");
    expect(pageNotice({ error: "has_provider" })?.kind).toBe("error");
    expect(pageNotice({ error: "cancelled" })?.text).toBe("Google sign-in was cancelled.");
    expect(pageNotice({ error: "something" })?.text).toBe("Connecting Google didn't work. Try again.");
    expect(pageNotice({})).toBeNull();
  });
});

describe("the Connect dialog", () => {
  it("asks for the code from anyone with codes on, always an admin", () => {
    expect(needsCode(me("admin", true))).toBe(true);
    expect(needsCode(me("member", true))).toBe(true);
    expect(needsCode(me("member", false))).toBe(false);
    expect(needsCode(me("reviewer", false))).toBe(false);
  });

  it("is introduced in the spec's words", () => {
    expect(source("components/account/WaysToSignIn.tsx")).toContain("Type your Kaagaz password to continue");
  });
});

describe("disconnectQuestion", () => {
  it("asks first, and says the password keeps working", () => {
    expect(disconnectQuestion("google", identity())).toEqual({
      title: "Disconnect Google?",
      text: "You won't be able to sign in with ramesh.k@gmail.com. You can connect it again later.",
      button: "Disconnect",
    });
    expect(disconnectQuestion("google", identity({ status: "pending" })).button).toBe("Cancel request");
  });
});

describe("the app's banner", () => {
  it("says which provider was connected and when", () => {
    expect(bannerText({ provider: "google", linked_at: "2026-10-30T06:00:00Z" })).toBe("Google connected on 30 Oct.");
    expect(source("components/shell/AppShell.tsx")).toContain("<ConnectedBanner");
  });
});
