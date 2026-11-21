import { afterEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { ApiError, saveAdminWhatsApp } from "@/lib/api";
import { needsProof } from "@/components/account/WhatsAppConnect";
import {
  beginKeycloakReauth,
  isKeycloakAction,
  isKeycloakManaged,
  keycloakAuthorizationUrl,
  keycloakCallbackError,
  keycloakLoginHref,
  keycloakNextPath,
  keycloakReauthNotice,
  keycloakReauthReturnPath,
  keycloakStartFailure,
} from "@/lib/keycloak";
import type { Me } from "@/lib/types";

afterEach(() => vi.unstubAllGlobals());

const me = (authority: "legacy" | "keycloak"): Me =>
  ({ user: { auth_authority: authority } } as Me);
const source = (path: string) => readFileSync(join(__dirname, "..", path), "utf8");

describe("company sign-in routing", () => {
  it("shows the entry only when the server enables it and keeps next same-origin", () => {
    expect(keycloakLoginHref(false, "/admin")).toBeNull();
    expect(keycloakLoginHref(true, "/admin?tab=people")).toBe(
      "/api/auth/keycloak/start?next=%2Fadmin%3Ftab%3Dpeople",
    );
    expect(keycloakLoginHref(true, "https://outside.example/steal")).toBe(
      "/api/auth/keycloak/start?next=%2Fdashboard",
    );
    expect(keycloakNextPath("//outside.example")).toBe("/dashboard");
    expect(keycloakNextPath("/%2f%2foutside.example")).toBe("/dashboard");
    expect(keycloakNextPath("/settings/sign-in")).toBe("/settings/sign-in");
  });

  it("marks a supported action on a safe local return path", () => {
    expect(keycloakReauthReturnPath("/admin?tab=people", "access")).toBe("/admin?tab=people&reauth=access");
    expect(keycloakReauthReturnPath("https://outside.example", "locks")).toBe("/dashboard?reauth=locks");
    expect(isKeycloakAction("locked_open")).toBe(true);
    expect(isKeycloakAction("password")).toBe(false);
  });

  it("distinguishes mapped users from legacy users", () => {
    expect(isKeycloakManaged(me("keycloak"))).toBe(true);
    expect(isKeycloakManaged(me("legacy"))).toBe(false);
    expect(isKeycloakManaged(null)).toBe(false);
  });
});

describe("company verification messages", () => {
  it("uses fixed callback errors and never reflects callback input", () => {
    expect(keycloakCallbackError("cancelled")).toContain("No change was made");
    expect(keycloakCallbackError("refused")).toBe("Your company account could not verify this action. No change was made.");
    expect(keycloakCallbackError("mfa_verify_again")).toBe(
      "Your authenticator setup is complete. Repeat company sign-in or account linking to finish.",
    );
    expect(keycloakCallbackError("password=secret")).not.toContain("secret");
    expect(keycloakCallbackError(null)).toBeNull();
    expect(keycloakStartFailure(new ApiError(500, "failure", "password=secret"))).not.toContain("secret");
  });

  it("tells the person to repeat and confirm each action after verification", () => {
    for (const action of ["locked_open", "whatsapp", "access", "locks"] as const) {
      expect(keycloakReauthNotice(action)).toMatch(/No |nothing|Nothing|Return|Reopen|Review/);
    }
    expect(keycloakReauthNotice("locks")).toContain("reselect files");
  });

  it("accepts only HTTPS IdP URLs (or local development HTTP)", () => {
    vi.stubGlobal("window", { location: { origin: "https://kaagaz.example" } });
    expect(keycloakAuthorizationUrl("https://idp.example/authorize")).toBe("https://idp.example/authorize");
    expect(keycloakAuthorizationUrl("http://localhost:8080/authorize")).toBe("http://localhost:8080/authorize");
    expect(() => keycloakAuthorizationUrl("javascript:alert(1)")).toThrow("Invalid sign-in destination");
    expect(() => keycloakAuthorizationUrl("http://idp.example/authorize")).toThrow("Invalid sign-in destination");
  });

  it("starts only the requested action and navigates to the backend URL", async () => {
    const calls: { url: string; body: unknown }[] = [];
    const assign = vi.fn();
    vi.stubGlobal("window", { location: { origin: "https://kaagaz.example", assign } });
    vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
      calls.push({ url, body: JSON.parse(String(init.body)) });
      return new Response(JSON.stringify({ url: "https://idp.example/authorize" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    await beginKeycloakReauth("locks", "/trash?folder=private");
    expect(calls).toEqual([
      {
        url: "/api/auth/keycloak/reauth/locks/start",
        body: { next: "/trash?folder=private&reauth=locks" },
      },
    ]);
    expect(assign).toHaveBeenCalledWith("https://idp.example/authorize");
  });
});

describe("WhatsApp's lost proof", () => {
  it("treats 403 proof_needed like keycloak_reauth_required, and nothing else", () => {
    expect(needsProof(new ApiError(403, "proof_needed", "x"))).toBe(true);
    expect(needsProof(new ApiError(403, "keycloak_reauth_required", "x"))).toBe(true);
    expect(needsProof(new ApiError(400, "proof_needed", "x"))).toBe(false);
    expect(needsProof(new ApiError(403, "forbidden", "x"))).toBe(false);
    expect(needsProof(new TypeError("offline"))).toBe(false);
  });
});

describe("mapped proof requests", () => {
  it("omits Kaagaz passwords from the WhatsApp settings proof", async () => {
    const bodies: unknown[] = [];
    vi.stubGlobal("fetch", async (_url: string, init: RequestInit) => {
      bodies.push(JSON.parse(String(init.body)));
      return new Response(null, { status: 204 });
    });
    await saveAdminWhatsApp(
      { enabled: true, roles: ["member"], management: false, voice: true, uploads: false },
      true,
    );
    expect(bodies).toEqual([
      { enabled: true, roles: ["member"], management: false, voice: true, uploads: false, rules_confirmed: true },
    ]);
  });

  it("wires fresh verification into the mapped account, admin and document flows", () => {
    const whatsapp = source("components/account/WhatsAppConnect.tsx");
    expect(whatsapp).toContain('params.reauth === "whatsapp" && !params.reauth_error');
    expect(whatsapp).not.toContain("if (!reauthAvailable)"); // Disconnect asks for no sign-in
    expect(whatsapp).toContain('beginKeycloakReauth("whatsapp"');
    expect(whatsapp).toContain("if (reauthAvailable)");
    expect(whatsapp).not.toContain("proveForWhatsApp");
    const adminWhatsApp = source("components/admin/WhatsAppPanel.tsx");
    expect(adminWhatsApp).toContain('beginKeycloakReauth("whatsapp"');
    expect(adminWhatsApp).toContain("isKeycloakReauthRequired(caught)");
    expect(adminWhatsApp).not.toContain("PasswordField");
    expect(source("components/admin/UsersPanel.tsx")).toContain('reauth !== "whatsapp"');
    expect(source("components/access/proofStep.ts")).toContain('beginKeycloakReauth("access"');
    expect(source("components/documents/LockDialog.tsx")).toContain('beginKeycloakReauth("locks"');
    expect(source("components/viewer/LockedOpenPrompt.tsx")).toContain('beginKeycloakReauth("locked_open"');
    expect(source("components/viewer/LockedOpenPrompt.tsx")).not.toContain("PasswordField");
    // Changed on purpose (#744, 22 Nov 2026): Delete forever asks for no fresh sign-in.
    for (const file of ["components/documents/DocumentsTable.tsx", "components/documents/TrashFolderRow.tsx"]) {
      expect(source(file)).not.toContain("beginKeycloakReauth");
    }
  });
});

