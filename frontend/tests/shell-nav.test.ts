import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ICON_NAMES } from "@/components/ui/icons";
import {
  endedPath,
  pageAfterSessionChange,
  sessionScope,
  signedOutMessage,
  signedOutPath,
  signInPath,
} from "@/components/shell/gate";
import { currentPageName, isCurrent, navLinks, reviewCountText, roleName } from "@/components/shell/nav";
import { ShellMenu } from "@/components/shell/ShellMenu";
import type { Me, Role, SessionScope } from "@/lib/types";

describe("navLinks", () => {
  const labels = (role: Role, scope: SessionScope) => navLinks(role, scope).map((l) => l.label);

  // Changed on purpose (PRD 1, screens): File management D1, Home (the dashboard) comes first for everyone.
  // Changed on purpose (#707): and My access, each person's own.
  it("gives a member Home, Documents, Ask, My words and My access only", () => {
    expect(labels("member", "app")).toEqual(["Home", "Documents", "Ask", "My words", "My access"]);
  });

  it("gives a member no Review or Admin link, by label or by address", () => {
    const links = navLinks("member", "app");
    // Changed on purpose (#707): My access.
    expect(links.map((l) => l.href)).toEqual(["/dashboard", "/documents", "/chat", "/words", "/my-access"]);
    for (const link of links) {
      expect(link.href).not.toMatch(/^\/(review|admin)(\/|$)/);
      expect(link.label).not.toMatch(/review|admin/i);
    }
  });

  // Changed on purpose (#707): My access.
  it("adds Review for a reviewer", () => {
    expect(labels("reviewer", "app")).toEqual(["Home", "Documents", "Ask", "My words", "My access", "Review"]);
  });

  // PRD A1: the same admin account signed in at /login sees no Admin menu.
  // Changed on purpose (#707): My access.
  it("gives an admin with an app session Review but no Admin", () => {
    expect(labels("admin", "app")).toEqual(["Home", "Documents", "Ask", "My words", "My access", "Review"]);
    expect(navLinks("admin", "app").map((l) => l.href)).not.toContain("/admin");
  });

  it("adds Admin only for an admin with an admin session", () => {
    // Changed on purpose (#706): an admin session also has Access, just above Admin.
    expect(labels("admin", "admin")).toEqual(["Home", "Documents", "Ask", "My words", "Review", "Access", "Admin"]);
  });

  it("never gives Admin to a non-admin, whatever the scope says", () => {
    expect(labels("member", "admin")).toEqual(["Home", "Documents", "Ask", "My words"]);
    expect(labels("reviewer", "admin")).toEqual(["Home", "Documents", "Ask", "My words", "Review"]);
  });

  // #707: My access for every role, but an Admin in an admin session (who has the Admin tab's Access).
  it("gives My access to everyone but an Admin in an admin session", () => {
    for (const role of ["member", "reviewer", "admin"] as const) {
      const link = navLinks(role, "app").find((l) => l.label === "My access");
      expect(link?.href).toBe("/my-access");
      expect(link?.short).toBeUndefined(); // the phone tab bar says "My access" too, as "My words"
    }
    expect(labels("admin", "admin")).not.toContain("My access");
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
      totp_enabled: true, // every admin has two-step sign-in (16 Oct 2026; tests/admin-two-step.test.ts)
      two_step_turned_off: false,
      can_delete_forever: false,
      email_confirmed: true, // #457
    },
    customer: { id: "c1", name: "Sharma Traders" },
    session: { scope },
    providers: [], // #457 review: the sign-in providers switched on
  });

  it("reads the scope", () => {
    expect(sessionScope(me({ scope: "admin" }))).toBe("admin");
    expect(sessionScope(me({ scope: "app" }))).toBe("app");
  });

  it("is applied by the session provider, which can also reload who is signed in", () => {
    const session = readFileSync(join(__dirname, "..", "lib", "session.tsx"), "utf8");
    expect(session).not.toContain("appGate(");  // 21 Nov 2026: no temporary-password or set-up gate any more
    expect(session).not.toContain("SetPasswordScreen");
    expect(session).toMatch(/refresh\(\): void/);
  });
});

describe("where to sign in", () => {
  // Changed on purpose (one sign-in page, 16 Oct 2026): /login, back to /admin after.
  it("sends the admin screens to the one sign-in, which comes back to /admin", () => {
    expect(signInPath("/admin")).toBe("/login?next=/admin");
    expect(signInPath("/admin/users")).toBe("/login?next=/admin");
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
    // Changed on purpose (issue 468): a session's end comes back to the screen it was on.
    expect(endedPath("/documents/d-1")).toBe("/login?next=%2Fdocuments%2Fd-1&ended=1");
    expect(endedPath("/admin")).toBe("/login?next=%2Fadmin&ended=1");
    expect(signedOutPath("/documents")).toBe("/login?signed_out=1");
    expect(signedOutPath("/admin")).toBe("/login?next=/admin&signed_out=1");
    expect(signedOutMessage({ signed_out: "1" })).toBe("You signed out.");
    expect(signedOutMessage({ ended: "1" })).toBe("Your session ended. Sign in again to continue.");
    expect(signedOutMessage({})).toBeNull();
    expect(signedOutMessage({ ended: "yes", other: "1" })).toBeNull();
  });

  it("loads a fresh page when a session existed, and keeps a quick redirect on a first visit", () => {
    const session = readFileSync(join(__dirname, "..", "lib", "session.tsx"), "utf8");
    // Changed on purpose (#468 review, SEC1): the path only, never its query.
    expect(session).toContain("leavePage(endedPath(window.location.pathname))");
    expect(session).toMatch(/if \(event\.persisted\) window\.location\.reload\(\)/);
    // Put away for the back button: blank, so it never shows before the reload.
    expect(session).toMatch(/if \(event\.persisted\) document\.documentElement\.style\.visibility = "hidden"/);
    // Admins sign out to the admin sign-in.
    // Changed on purpose (UI audit G6, 16 Oct 2026): the sign-outs moved to the account menu.
    for (const file of [["components", "shell", "AccountMenu.tsx"]]) {
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
    for (const file of [["app", "login", "SignInPanel.tsx"]]) {
      const form = readFileSync(join(__dirname, "..", ...file), "utf8");
      const note = form.indexOf("<SignedOutNotice message={note} />");
      expect(note, file.join("/")).toBeGreaterThan(form.indexOf("</h1>"));
      expect(form.indexOf("</h1>"), file.join("/")).toBeGreaterThan(0);
    }
    for (const file of [["app", "login", "page.tsx"]]) {
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
      "/login?next=/admin&signed_out=1",
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
    // Changed on purpose (#507 review): the sidebar shows it only outside the limited session.
    expect(shell.match(/\{(chrome && )?menu\}/g)).toHaveLength(2);
    expect(shell).toMatch(/className=\{styles\.sidebar\}[\s\S]*\{chrome && menu\}[\s\S]*<Drawer[\s\S]*\{menu\}/);
  });

  it("hard-codes no Review or Admin link in any shell component", () => {
    for (const { name, text } of sources) {
      expect(text, name).not.toMatch(/["'`]\/(review|admin)\b/);
    }
  });

  it("passes the session scope to navLinks()", () => {
    // Changed on purpose (PRD 7 PR 11c, #604): and Management, for the Access activity link.
    expect(shell).toContain("navLinks(me.user.role, sessionScope(me), me.access?.management === true)");
  });

  // PRD 7: "Sign out everywhere" in the account menu.
  // Changed on purpose (UI audit G6, 16 Oct 2026): it moved from ShellMenu (the sidebar) to the
  // header's account menu, under Sign out in its own row, styled destructive, and asks first in a
  // dialog instead of window.confirm.
  it("offers Sign out everywhere in the account menu, under Sign out, destructive and asked first", () => {
    const account = sources.find((s) => s.name === "AccountMenu.tsx")?.text ?? "";
    const menu = sources.find((s) => s.name === "ShellMenu.tsx")?.text ?? "";
    expect(menu).not.toMatch(/Sign out|ThemeToggle/);
    expect(account.indexOf("<span>Sign out everywhere</span>")).toBeGreaterThan(account.indexOf('endSession("here")'));
    // Changed on purpose (issue 373, 27 Oct 2026): a quiet item in the danger colour, not a red button.
    expect(account).toMatch(/className=\{`btn btn-quiet btn-block \$\{styles\.signOut\} \$\{styles\.signOutEverywhere\}`\}\s*onClick=\{\(\) => setConfirming\(true\)\}/);
    expect(account).toMatch(/<ConfirmDialog[\s\S]*open=\{confirming\}[\s\S]*onConfirm=\{\(\) => void endSession\("everywhere"\)\}/);
    expect(account).toContain("logoutEverywhere()");
    expect(account).not.toContain("window.confirm");
    expect(shell).toContain("<AccountMenu me={me} />");
  });
});

// UI mockups board 5 (16 Oct 2026): the Review link counts the values waiting to be checked.
// Changed on purpose (Tushar, 16 Oct 2026): the Review link carries no count; a big
// number there made people uneasy. The Review page itself has the list.
// Changed on purpose (D-7, Vrushit, 21 Oct 2026): the handoff's count is back for a reviewer and an
// admin; a member has no Review link and the shell never asks for a count for one.
describe("the Review link", () => {
  const menu = (role: Role, reviewCount: string | null) =>
    renderToStaticMarkup(
      createElement(ShellMenu, { links: navLinks(role, "admin"), pathname: "/documents", reviewCount }),
    );

  it("shows the values waiting on the Review link, said with its words", () => {
    const html = menu("admin", "23");
    expect(html).toMatch(/href="\/review"[^>]*>.*<span>Review<\/span><span class="_count_\w+">23<span class="visually-hidden"> to review<\/span><\/span><\/a>/);
    expect(menu("admin", null)).not.toContain("to review");
  });

  it("counts in words a link can hold, none at 0", () => {
    expect(reviewCountText(null)).toBeNull();
    expect(reviewCountText(0)).toBeNull();
    expect(reviewCountText(23)).toBe("23");
    expect(reviewCountText(99)).toBe("99");
    expect(reviewCountText(1200)).toBe("99+");
  });

  it("asks the server only for a reviewer or an admin", () => {
    const shell = readFileSync(join(__dirname, "..", "components", "shell", "AppShell.tsx"), "utf8");
    // Changed on purpose (#507 review): never in the limited session, which the server refuses.
    expect(shell).toContain("const reviewer = chrome && canReview(me.user.role);");
    expect(shell).toMatch(/if \(!reviewer\) return;\s*let cancelled = false;\s*listReview\(\{ limit: 1 \}\)/);
    expect(shell).toContain("const reviewCount = reviewer ? reviewCountText(reviewTotal) : null;");
    expect(menu("member", "23")).not.toContain("review");
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
