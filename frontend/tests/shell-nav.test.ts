import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { ICON_NAMES } from "@/components/ui/icons";
import {
  appGate,
  endedPath,
  mustChangePassword,
  pageAfterSessionChange,
  sessionScope,
  signedOutMessage,
  signedOutPath,
  signInPath,
} from "@/components/shell/gate";
import { currentPageName, isCurrent, navLinks, roleName } from "@/components/shell/nav";
import type { Me, Role, SessionScope } from "@/lib/types";

describe("navLinks", () => {
  const labels = (role: Role, scope: SessionScope) => navLinks(role, scope).map((l) => l.label);

  it("gives a member Documents, Ask and My words only", () => {
    expect(labels("member", "app")).toEqual(["Documents", "Ask", "My words"]);
  });

  it("gives a member no Review or Admin link, by label or by address", () => {
    const links = navLinks("member", "app");
    expect(links.map((l) => l.href)).toEqual(["/documents", "/chat", "/words"]);
    for (const link of links) {
      expect(link.href).not.toMatch(/^\/(review|admin)(\/|$)/);
      expect(link.label).not.toMatch(/review|admin/i);
    }
  });

  it("adds Review for a reviewer", () => {
    expect(labels("reviewer", "app")).toEqual(["Documents", "Ask", "My words", "Review"]);
  });

  // PRD A1: the same admin account signed in at /login sees no Admin menu.
  it("gives an admin with an app session Review but no Admin", () => {
    expect(labels("admin", "app")).toEqual(["Documents", "Ask", "My words", "Review"]);
    expect(navLinks("admin", "app").map((l) => l.href)).not.toContain("/admin");
  });

  it("adds Admin only for an admin with an admin session", () => {
    expect(labels("admin", "admin")).toEqual(["Documents", "Ask", "My words", "Review", "Admin"]);
  });

  it("never gives Admin to a non-admin, whatever the scope says", () => {
    expect(labels("member", "admin")).toEqual(["Documents", "Ask", "My words"]);
    expect(labels("reviewer", "admin")).toEqual(["Documents", "Ask", "My words", "Review"]);
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
    user: {
      id: "u1",
      email: "asha@sharma.in",
      display_name: "Asha",
      role: "admin",
      must_change_password,
      totp_enabled: false,
      can_delete_forever: false,
    },
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

describe("where to sign in", () => {
  it("sends the admin screens to the admin sign-in", () => {
    expect(signInPath("/admin")).toBe("/admin/login");
    expect(signInPath("/admin/users")).toBe("/admin/login");
  });

  it("sends every other page to the normal sign-in", () => {
    expect(signInPath("/documents")).toBe("/login");
    expect(signInPath("/documents/d-1")).toBe("/login");
    expect(signInPath("/chat")).toBe("/login");
    expect(signInPath("/")).toBe("/login");
    expect(signInPath("/administration")).toBe("/login");
  });

  it("is used by the session provider, with no session and when one ends", () => {
    const session = readFileSync(join(__dirname, "..", "lib", "session.tsx"), "utf8");
    expect(session).toContain("router.replace(signInPath(pathname))");
    expect(session).toContain("onSessionEnded(");
    expect(session).not.toContain('router.replace("/login")');
  });

  // Critical #5: once a session existed, the sign-in page is a full page load,
  // so nothing of that person stays in the tab's memory.
  it("adds why, for the sign-in page's note", () => {
    expect(endedPath("/documents/d-1")).toBe("/login?ended=1");
    expect(endedPath("/admin")).toBe("/admin/login?ended=1");
    expect(signedOutPath("/documents")).toBe("/login?signed_out=1");
    expect(signedOutPath("/admin")).toBe("/admin/login?signed_out=1");
    expect(signedOutMessage({ signed_out: "1" })).toBe("You signed out.");
    expect(signedOutMessage({ ended: "1" })).toBe("Your session ended. Sign in again to continue.");
    expect(signedOutMessage({})).toBeNull();
    expect(signedOutMessage({ ended: "yes", other: "1" })).toBeNull();
  });

  it("loads a fresh page when a session existed, and keeps a quick redirect on a first visit", () => {
    const session = readFileSync(join(__dirname, "..", "lib", "session.tsx"), "utf8");
    expect(session).toContain("leavePage(endedPath(pathname))");
    expect(session).toMatch(/if \(event\.persisted\) window\.location\.reload\(\)/);
    // Put away for the back button: blank, so it never shows before the reload.
    expect(session).toMatch(/if \(event\.persisted\) document\.documentElement\.style\.visibility = "hidden"/);
    // Admins sign out to the admin sign-in.
    for (const file of [
      ["components", "shell", "AppShell.tsx"],
      ["components", "shell", "SetPasswordScreen.tsx"],
    ]) {
      const text = readFileSync(join(__dirname, "..", ...file), "utf8");
      expect(text, file.join("/")).toContain("announceSession(null);");
      expect(text, file.join("/")).toContain("leavePage(signedOutPath(pathname))");
    }
  });

  // A tab still loading (or showing an error) when another tab signs in or
  // out asks the server again: the answer on its way may be from before.
  it("hears the other tabs from the start, and asks again while it does not know yet", () => {
    const session = readFileSync(join(__dirname, "..", "lib", "session.tsx"), "utf8");
    expect(session).toMatch(/watchSession\(\(now\) => \{[\s\S]*?setAttempt\(\(n\) => n \+ 1\)[\s\S]*?\[state\],/);
    expect(session).not.toContain("[userId]");
  });

  // The note sits under the heading. Its box is in the server's HTML, so the
  // form does not move; the words come once the page runs, so screen readers
  // announce them.
  it("puts the sign-in note under the heading, in a box that is there from the start", () => {
    for (const file of [
      ["app", "login", "SignInForm.tsx"],
      ["app", "admin", "login", "AdminSignInForm.tsx"],
    ]) {
      const form = readFileSync(join(__dirname, "..", ...file), "utf8");
      const note = form.indexOf("<SignedOutNotice message={note} />");
      expect(note, file.join("/")).toBeGreaterThan(form.indexOf("</h1>"));
      expect(form.indexOf("</h1>"), file.join("/")).toBeGreaterThan(0);
    }
    for (const file of [
      ["app", "login", "page.tsx"],
      ["app", "admin", "login", "page.tsx"],
    ]) {
      expect(readFileSync(join(__dirname, "..", ...file), "utf8"), file.join("/")).not.toContain("<SignedOutNotice");
    }
    const notice = readFileSync(join(__dirname, "..", "app", "login", "SignedOutNotice.tsx"), "utf8");
    expect(notice).toContain('<p role="status" className={styles.notice}>');
    const css = readFileSync(join(__dirname, "..", "app", "login", "login.module.css"), "utf8");
    expect(css).toMatch(/\.notice \{[^}]*min-height:/);
  });
});

describe("another tab signs out or in", () => {
  const here = { pathname: "/documents/d-1", href: "http://localhost:3000/documents/d-1" };

  it("stays when the same person is still signed in", () => {
    expect(pageAfterSessionChange("u1", "u1", here)).toBeNull();
  });

  it("goes to sign in when nobody is signed in any more", () => {
    expect(pageAfterSessionChange("u1", null, here)).toBe("/login?signed_out=1");
    expect(pageAfterSessionChange("u1", null, { pathname: "/admin", href: "/admin" })).toBe(
      "/admin/login?signed_out=1",
    );
  });

  it("loads the page again when someone else signed in", () => {
    expect(pageAfterSessionChange("u1", "u2", here)).toBe(here.href);
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
