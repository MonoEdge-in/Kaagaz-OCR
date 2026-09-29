import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { ICON_NAMES } from "@/components/ui/icons";
import { appGate, mustChangePassword, sessionScope } from "@/components/shell/gate";
import { currentPageName, isCurrent, navLinks, roleName } from "@/components/shell/nav";
import type { Me, Role, SessionScope } from "@/lib/types";

describe("navLinks", () => {
  const labels = (role: Role, scope: SessionScope) => navLinks(role, scope).map((l) => l.label);

  it("gives a member Documents and Ask only", () => {
    expect(labels("member", "app")).toEqual(["Documents", "Ask"]);
  });

  it("gives a member no Review or Admin link, by label or by address", () => {
    const links = navLinks("member", "app");
    expect(links.map((l) => l.href)).toEqual(["/documents", "/chat"]);
    for (const link of links) {
      expect(link.href).not.toMatch(/^\/(review|admin)(\/|$)/);
      expect(link.label).not.toMatch(/review|admin/i);
    }
  });

  it("adds Review for a reviewer", () => {
    expect(labels("reviewer", "app")).toEqual(["Documents", "Ask", "Review"]);
  });

  // PRD A1: the same admin account signed in at /login sees no Admin menu.
  it("gives an admin with an app session Review but no Admin", () => {
    expect(labels("admin", "app")).toEqual(["Documents", "Ask", "Review"]);
    expect(navLinks("admin", "app").map((l) => l.href)).not.toContain("/admin");
  });

  it("adds Admin only for an admin with an admin session", () => {
    expect(labels("admin", "admin")).toEqual(["Documents", "Ask", "Review", "Admin"]);
  });

  it("never gives Admin to a non-admin, whatever the scope says", () => {
    expect(labels("member", "admin")).toEqual(["Documents", "Ask"]);
    expect(labels("reviewer", "admin")).toEqual(["Documents", "Ask", "Review"]);
  });

  it("points Ask at /chat", () => {
    expect(navLinks("member", "app").find((l) => l.label === "Ask")?.href).toBe("/chat");
  });

  it("gives every link a known icon", () => {
    for (const link of navLinks("admin", "admin")) {
      expect(ICON_NAMES).toContain(link.icon);
    }
  });
});

describe("the session gate", () => {
  const me = ({
    must_change_password = false,
    scope = "app",
  }: { must_change_password?: boolean; scope?: SessionScope } = {}): Me => ({
    user: { id: "u1", email: "asha@sharma.in", display_name: "Asha", role: "admin", must_change_password },
    customer: { id: "c1", name: "Sharma Traders" },
    session: { scope },
  });

  it("reads the scope", () => {
    expect(sessionScope(me({ scope: "admin" }))).toBe("admin");
    expect(sessionScope(me({ scope: "app" }))).toBe("app");
  });

  it("shows the set-new-password screen whenever the flag is set, in either scope", () => {
    expect(appGate(me({ must_change_password: true, scope: "app" }))).toBe("set_password");
    expect(appGate(me({ must_change_password: true, scope: "admin" }))).toBe("set_password");
    expect(mustChangePassword(me({ must_change_password: true }))).toBe(true);
  });

  it("opens the app when no new password is needed", () => {
    expect(appGate(me({ must_change_password: false, scope: "app" }))).toBe("app");
    expect(appGate(me())).toBe("app");
    expect(mustChangePassword(me())).toBe(false);
  });

  it("is applied by the session provider, which can also reload who is signed in", () => {
    const session = readFileSync(join(__dirname, "..", "lib", "session.tsx"), "utf8");
    expect(session).toContain("appGate(");
    expect(session).toContain("<SetPasswordScreen");
    expect(session).toMatch(/refresh\(\): void/);
  });
});

// The sidebar and the drawer must show the same links. AppShell gets them from
// navLinks() once and renders one ShellMenu element in both places; no shell
// file may hard-code a link to a role-only page beside it.
describe("one nav list for the sidebar and the drawer", () => {
  const dir = join(__dirname, "..", "components", "shell");
  const sources = readdirSync(dir)
    .filter((name) => name.endsWith(".tsx"))
    .map((name) => ({ name, text: readFileSync(join(dir, name), "utf8") }));
  const shell = sources.find((s) => s.name === "AppShell.tsx")?.text ?? "";

  it("calls navLinks() exactly once, in AppShell", () => {
    const calls = sources.flatMap(({ name, text }) =>
      Array.from(text.matchAll(/\bnavLinks\(/g), () => name),
    );
    expect(calls).toEqual(["AppShell.tsx"]);
  });

  it("renders the one ShellMenu element in both the sidebar and the drawer", () => {
    expect(shell.match(/<ShellMenu\b/g)).toHaveLength(1);
    expect(shell.match(/\{menu\}/g)).toHaveLength(2);
    expect(shell).toMatch(/className=\{styles\.sidebar\}[\s\S]*\{menu\}[\s\S]*<Drawer[\s\S]*\{menu\}/);
  });

  it("hard-codes no Review or Admin link in any shell component", () => {
    for (const { name, text } of sources) {
      expect(text, name).not.toMatch(/["'`]\/(review|admin)\b/);
    }
  });

  it("passes the session scope to navLinks()", () => {
    expect(shell).toContain("navLinks(me.user.role, sessionScope(me))");
  });

  // PRD 7: "Sign out everywhere" in the account menu. It sits in ShellMenu, so
  // the sidebar and the drawer both have it.
  it("offers Sign out everywhere in the one menu, next to Sign out", () => {
    const menu = sources.find((s) => s.name === "ShellMenu.tsx")?.text ?? "";
    expect(menu).toContain("Sign out everywhere");
    expect(menu).toContain("onClick={onSignOutEverywhere}");
    expect(menu.indexOf("Sign out everywhere")).toBeGreaterThan(menu.indexOf("onClick={onSignOut}"));
    expect(shell).toContain("logoutEverywhere()");
    expect(shell).toContain("onSignOutEverywhere={signOutEverywhere}");
  });
});

describe("isCurrent", () => {
  it("matches the path and paths below it, not look-alikes", () => {
    expect(isCurrent("/documents", "/documents")).toBe(true);
    expect(isCurrent("/documents", "/documents/abc")).toBe(true);
    expect(isCurrent("/documents", "/documentsx")).toBe(false);
    expect(isCurrent("/admin", "/documents")).toBe(false);
  });
});

describe("currentPageName", () => {
  it("names the page for the top bar, including pages below a link", () => {
    const links = navLinks("admin", "admin");
    expect(currentPageName(links, "/documents/abc")).toBe("Documents");
    expect(currentPageName(links, "/chat")).toBe("Ask");
    expect(currentPageName(links, "/admin")).toBe("Admin");
  });

  it("names no page a member has no link for", () => {
    expect(currentPageName(navLinks("member", "app"), "/review")).toBeNull();
    expect(currentPageName(navLinks("member", "app"), "/somewhere")).toBeNull();
  });
});

describe("roleName", () => {
  it("names each role", () => {
    expect(roleName("member")).toBe("Member");
    expect(roleName("reviewer")).toBe("Reviewer");
    expect(roleName("admin")).toBe("Admin");
  });
});
