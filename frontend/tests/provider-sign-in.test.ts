// Ways to sign in (#457 phase 1) on the sign-in page: "Continue with Google" only while the
// server says Google is on, the divider, the code step after Google for anyone with codes on,
// and every ?oidc_error= in plain words. Never on the operator's sign-in page.

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { getPathMatch } from "next/dist/shared/lib/router/utils/path-match";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  CANCELLED,
  EXPIRED,
  LOCKED,
  PASSWORD_FIRST,
  REFUSED,
  UNKNOWN,
  oidcErrorMessage,
  providerButtons,
} from "@/app/login/providerMessages";
import { oidcCode, oidcNotice } from "@/lib/api";
import nextConfig from "@/next.config";

const root = join(__dirname, "..");
const source = (path: string) => readFileSync(join(root, path), "utf8");

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("oidcErrorMessage", () => {
  it("says each refusal in the spec's words", () => {
    expect(oidcErrorMessage("cancelled")).toBe("Google sign-in was cancelled.");
    expect(CANCELLED).toBe("Google sign-in was cancelled.");
    expect(oidcErrorMessage("expired")).toBe("That sign-in expired. Try again.");
    expect(EXPIRED).toBe("That sign-in expired. Try again.");
    expect(oidcErrorMessage("not_connected", "ramesh.k@gmail.com")).toBe(
      "ramesh.k@gmail.com isn't connected to Kaagaz. Sign in with your email and password, then connect it under Ways to sign in.",
    );
    expect(oidcErrorMessage("password_first")).toBe(
      "Sign in with your email and password first to set up your 6-digit code.",
    );
    expect(PASSWORD_FIRST).toBe(oidcErrorMessage("password_first"));
    expect(oidcErrorMessage("refused")).toBe(REFUSED);
    expect(oidcErrorMessage("locked")).toBe(LOCKED);
    expect(oidcErrorMessage("unavailable")).toBe("Google sign-in didn't work. Try again.");
    expect(oidcErrorMessage("anything-else")).toBe(UNKNOWN);
  });

  it("still says what to do when the email is not known", () => {
    expect(oidcErrorMessage("not_connected")).toBe(
      "That Google account isn't connected to Kaagaz. Sign in with your email and password, then connect it under Ways to sign in.",
    );
  });
});

describe("providerButtons", () => {
  it("is empty while no provider is on, so no button shows", () => {
    expect(providerButtons([], "/dashboard")).toEqual([]);
    expect(providerButtons(["apple"], "/dashboard")).toEqual([]);
  });

  it("starts Google's sign-in at the API, keeping where to go after", () => {
    expect(providerButtons(["google"], "/admin")).toEqual([
      { provider: "google", label: "Continue with Google", href: "/api/auth/oidc/google/start?next=%2Fadmin" },
    ]);
  });
});

describe("the pages", () => {
  it("shows the buttons and the divider above the sign-in form", () => {
    const form = source("app/login/SignInForm.tsx");
    expect(form).toContain("<ProviderButtons");
    expect(source("app/login/ProviderButtons.tsx")).toContain("or use your email");
  });

  it("never offers a provider on the operator's sign-in page", () => {
    for (const file of readdirSync(join(root, "app/operator/login"))) {
      const text = source(`app/operator/login/${file}`);
      expect(text, file).not.toContain("ProviderButtons");
      expect(text, file).not.toContain("/auth/oidc");
    }
  });

  it("draws the Google logo inline: no provider script loads", () => {
    const buttons = source("app/login/ProviderButtons.tsx");
    expect(buttons).toContain("<svg");
    expect(buttons).not.toMatch(/<script|googleapis|gstatic/);
  });
});

describe("the API calls", () => {
  it("sends the code after Google, and reads the not-connected email once", async () => {
    const asked: { url: string; body: unknown }[] = [];
    vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
      asked.push({ url, body: init.body ? JSON.parse(String(init.body)) : undefined });
      const reply = url.endsWith("/notice") ? { email: "ramesh.k@gmail.com" } : { user: { id: "u" } };
      return new Response(JSON.stringify(reply), { status: 200, headers: { "Content-Type": "application/json" } });
    });
    await oidcCode("123 456");
    expect(await oidcNotice()).toEqual({ email: "ramesh.k@gmail.com" });
    expect(asked).toEqual([
      { url: "/api/auth/oidc/code", body: { code: "123 456" } },
      { url: "/api/auth/oidc/notice", body: undefined },
    ]);
  });
});

describe("headers", () => {
  it("send no referrer from the provider sign-in's routes", async () => {
    const out: Record<string, string> = {};
    for (const rule of (await nextConfig.headers?.()) ?? []) {
      const match = getPathMatch(rule.source, { strict: true, removeUnnamedParams: true });
      if (match("/api/auth/oidc/google/callback") === false) continue;
      for (const { key, value } of rule.headers) out[key] = value;
    }
    expect(out["Referrer-Policy"]).toBe("no-referrer");
  });
});
