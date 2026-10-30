// Account, Ways to sign in (#457 phase 1): each row's words, the notice after coming back
// from Google, the Connect dialog's code for anyone with codes on, the disconnect question,
// and the app's "Google connected on 30 Oct." banner. The spec's copy, exactly.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ConnectedBanner } from "@/components/account/ConnectedBanner";
import {
  TITLE,
  introText,
  providersText,
  bannerText,
  disconnectQuestion,
  needsCode,
  pageNotice,
  passwordRow,
  rowText,
} from "@/components/account/signInWays";
import { AccountMenu } from "@/components/shell/AccountMenu";
import { PASSWORD_NOT_RIGHT, proofField } from "@/components/shell/twoStepAccount";
import { PaletteProvider } from "@/components/ui/PaletteProvider";
import { ThemeProvider } from "@/components/ui/ThemeProvider";
import { ApiError, removeIdentity } from "@/lib/api";
import type { ThemeChoice } from "@/lib/theme";
import type { ConnectedIdentity, Me } from "@/lib/types";

afterEach(() => {
  vi.unstubAllGlobals();
});

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
      two_step_turned_off: false,
      can_delete_forever: false,
      email_confirmed: true,
    },
    customer: { id: "c", name: "Firm" },
    session: { scope: role === "admin" ? "admin" : "app" },
    providers: [], // #457 review: the sign-in providers switched on
  };
}

describe("the page", () => {
  it("is titled Ways to sign in with the spec's line", () => {
    expect(TITLE).toBe("Ways to sign in");
    // Changed on purpose (#457 review): the line names only the providers that are on.
    expect(providersText(["google"])).toBe("Google");
    expect(introText(["google"])).toBe("Sign in with Google as well as your password.");
    const page = source("app/(app)/settings/sign-in/page.tsx");
    expect(page).toContain("<WaysToSignIn");
    expect(page).toContain("<WaysIntro />");
    expect(source("components/account/WaysToSignIn.tsx")).toContain("introText(me.providers)");
  });

  it("says what happened on the way back from Google only once the list has loaded (#457 review)", () => {
    expect(source("components/account/WaysToSignIn.tsx")).toMatch(
      /const notice = said\s*\? \{ kind: "done" as const, text: said \}\s*: ways !== null \|\| failure !== null\s*\? pageNotice\(params, connected\?\.email\)\s*: null;/,
    );
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
    expect(pageNotice({ error: "busy" })?.text).toBe(
      "Too many people are signing in right now. Try again in a few minutes.",
    );
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

  it("asks Not you? with a link to Ways to sign in (#457 review)", () => {
    const html = renderToStaticMarkup(
      createElement(ConnectedBanner, { items: [{ provider: "google", linked_at: "2026-10-30T06:00:00Z" }] }),
    );
    expect(html).toContain(
      'Google connected on 30 Oct. Not you? Disconnect it under <a href="/settings/sign-in">Ways to sign in</a>.',
    );
  });
});

describe("the account menu (#457 review)", () => {
  const menu = (providers: string[]) =>
    renderToStaticMarkup(
      createElement(
        ThemeProvider,
        { initial: "light" as ThemeChoice } as Parameters<typeof ThemeProvider>[0],
        createElement(
          PaletteProvider,
          { initial: "kaagaz" } as Parameters<typeof PaletteProvider>[0],
          createElement(AccountMenu, { me: { ...me("member", false), providers }, onChanged: () => undefined }),
        ),
      ),
    );

  it("hides Ways to sign in while no provider is on", () => {
    expect(menu([])).not.toContain("Ways to sign in");
    expect(menu(["google"])).toContain("Ways to sign in");
    expect(menu([])).toContain("Change password");
  });

  it("gives each sign-in item its own icon, from the existing set", () => {
    const text = source("components/shell/AccountMenu.tsx");
    expect(text).toContain('import { Icon as KIcon } from "@/components/ui/Icon/Icon";');
    expect(text).toMatch(/<KIcon name="link" \/>\s*<span>Ways to sign in<\/span>/);
    expect(text).toMatch(/<KIcon name="key" \/>\s*<span>Change password<\/span>/);
    expect(text).toMatch(/<KIcon name="lock" \/>\s*<span>Two-step sign-in<\/span>/);
  });

  it("gives focus back to the avatar when each dialog closes", () => {
    const text = source("components/shell/AccountMenu.tsx");
    expect(text.match(/onClosed=\{\(\) => avatar\.current\?\.focus\(\)\}/g)).toHaveLength(2);
    for (const path of ["components/shell/ChangePasswordDialog.tsx", "components/shell/TwoStepDialog.tsx"]) {
      expect(source(path), path).toContain("onClosed={onClosed}");
    }
  });
});

describe("errors on the field (#457 review)", () => {
  it("knows the server's wrong password and wrong or missing code", () => {
    expect(PASSWORD_NOT_RIGHT).toBe("Your current password is not right.");
    expect(proofField(new ApiError(400, "bad_request", "Your current password is not right."))).toBe("password");
    expect(proofField(new ApiError(400, "bad_request", "That code is not right. Wait for the next code and try again."))).toBe(
      "code",
    );
    expect(proofField(new ApiError(400, "bad_request", "Enter the 6-digit code from your authenticator app."))).toBe("code");
    expect(proofField(new ApiError(400, "bad_request", "Too many wrong passwords were tried on this account."))).toBeNull();
    expect(proofField(new ApiError(429, "rate_limited", "Your current password is not right."))).toBeNull();
    expect(proofField(new TypeError("offline"))).toBeNull();
  });

  it("puts it on its field and focuses it, in Ways to sign in and Two-step sign-in", () => {
    for (const path of ["components/account/WaysToSignIn.tsx", "components/shell/TwoStepDialog.tsx"]) {
      const text = source(path);
      expect(text, path).toContain("proofField(caught)");
      expect(text, path).toContain("flushSync(");
      expect(text, path).toMatch(/error=\{problems\.password\}/);
      expect(text, path).toMatch(/error=\{problems\.code\}/);
    }
    expect(source("components/account/WaysToSignIn.tsx")).not.toContain('setFailure("Enter your password.")');
  });
});

describe("cancelling a waiting request (#457 review)", () => {
  it("sends no password, and the dialog asks for none", async () => {
    const bodies: unknown[] = [];
    vi.stubGlobal("fetch", async (_url: string, init: RequestInit) => {
      bodies.push(JSON.parse(String(init.body)));
      return new Response(JSON.stringify({ providers: ["google"], identities: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    await removeIdentity("i-1");
    await removeIdentity("i-1", "secret");
    expect(bodies).toEqual([{}, { password: "secret" }]);
    const text = source("components/account/WaysToSignIn.tsx");
    expect(text).toContain('const withPassword = !(asking.kind === "remove" && asking.identity.status === "pending");');
    expect(text).toContain("{withPassword && (");
  });
});
