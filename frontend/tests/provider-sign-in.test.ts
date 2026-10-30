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
import { signInOptions } from "@/app/login/signInOptions";
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
    // Changed on purpose (#457 review): what took too long, and what to do.
    expect(oidcErrorMessage("expired")).toBe("That sign-in took too long. Go back and sign in again.");
    expect(EXPIRED).toBe("That sign-in took too long. Go back and sign in again.");
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
    expect(oidcErrorMessage("busy")).toBe("Too many people are signing in right now. Try again in a few minutes.");
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

describe("a cancelled Google sign-in (#457 review)", () => {
  it("is a calm notice, not a red alert", () => {
    const buttons = source("app/login/ProviderButtons.tsx");
    expect(buttons).toContain('error === "cancelled"');
    expect(buttons).toMatch(/<p role="status" className=\{styles\.notice\}>/);
  });
});

describe("no layout jump on /login (#457 review)", () => {
  const json = (body: unknown, status = 200) =>
    new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

  it("asks the backend for the providers and the email switch before the page is drawn", async () => {
    const asked: { url: string; cache: RequestCache | undefined }[] = [];
    const get = (async (url: string, init?: RequestInit) => {
      asked.push({ url, cache: init?.cache });
      return url.endsWith("/auth/session") ? json({ signed_in: false, providers: ["google"] }) : json({ enabled: true });
    }) as typeof fetch;
    expect(await signInOptions(get)).toEqual({ providers: ["google"], emailOn: true });
    expect(asked.map((a) => a.url.replace(/^https?:\/\/[^/]+/, "")).sort()).toEqual(["/api/auth/email", "/api/auth/session"]);
    expect(asked.every((a) => a.cache === "no-store")).toBe(true);
  });

  it("shows neither while email is off (404), nothing is on, or the backend does not answer", async () => {
    const off = (async (url: string) =>
      url.endsWith("/auth/session") ? json({ signed_in: false }) : json({ error: { code: "not_found" } }, 404)) as typeof fetch;
    expect(await signInOptions(off)).toEqual({ providers: [], emailOn: false });
    const down = (async () => {
      throw new TypeError("fetch failed");
    }) as typeof fetch;
    expect(await signInOptions(down)).toEqual({ providers: [], emailOn: false });
  });

  it("passes them to the form as props: no request from the browser for them", () => {
    const page = source("app/login/page.tsx");
    expect(page).toContain("await signInOptions()");
    expect(page).toContain("providers={options.providers}");
    expect(page).toContain("emailOn={options.emailOn}");
    const form = source("app/login/SignInForm.tsx");
    expect(form).toContain("<ProviderButtons next={next} error={oidcError} providers={providers} />");
    expect(form).toContain("<ForgotPasswordLink open={emailOn} />");
    expect(source("app/login/ProviderButtons.tsx")).not.toContain("currentSession");
    const forgot = source("app/login/ForgotPasswordLink.tsx");
    expect(forgot).not.toContain("useEffect");
    expect(forgot).not.toContain("emailOpen");
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
