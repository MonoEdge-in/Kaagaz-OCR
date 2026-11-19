// #706: Access in the sidebar for admins. The link (exactly who sees Admin), its own tab-bar word,
// and the page: every person with a one-line access summary and Access → to their Access page, the
// (you) marker, the Departments and private folders links, and the loading, empty and error states.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { AdminUser, Role, SessionScope } from "@/lib/types";

vi.mock("@/lib/session", () => ({ useSession: () => ({ me: { user: { id: "me" } } }) }));

const { navLinks } = await import("@/components/shell/nav");
const { AccessListView } = await import("@/components/admin/AccessList");

const read = (path: string) => readFileSync(join(process.cwd(), path), "utf8");

const USERS = [
  { id: "u1", display_name: "Priya", role: "member", management: false, departments: [{ id: "d1", name: "Finance", head: true }, { id: "d2", name: "Sales", head: false }] },
  { id: "me", display_name: "Asha", role: "admin", management: true, departments: [] },
  { id: "u3", display_name: "Ravi", role: "reviewer", departments: [] },
] as AdminUser[];

const render = (users: AdminUser[] | null, failed: string | null = null) =>
  renderToStaticMarkup(createElement(AccessListView, { users, failed, myId: "me", onRetry: () => undefined }));

describe("the Access link", () => {
  const hrefs = (role: Role, scope: SessionScope, management?: boolean) => navLinks(role, scope, management).map((l) => l.href);

  it("shows Access just above Admin, for an admin with an admin session only", () => {
    const links = hrefs("admin", "admin");
    expect(links.indexOf("/access")).toBe(links.indexOf("/admin") - 1);
    expect(navLinks("admin", "admin").find((l) => l.href === "/access")?.label).toBe("Access");
    for (const role of ["member", "reviewer", "admin"] as Role[]) {
      for (const scope of ["app", "admin"] as SessionScope[]) {
        for (const management of [false, true]) {
          expect(hrefs(role, scope, management).includes("/access")).toBe(role === "admin" && scope === "admin");
        }
      }
    }
  });

  it("never shares its phone tab-bar word with Access activity", () => {
    const word = (href: string, role: Role, scope: SessionScope, management: boolean) => {
      const link = navLinks(role, scope, management).find((l) => l.href === href);
      return link && (link.short ?? link.label);
    };
    expect(word("/access", "admin", "admin", false)).toBe("Access");
    expect(word("/access-activity", "member", "app", true)).not.toBe("Access");
  });
});

describe("the Access page", () => {
  it("is /access, behind the admin gate, with the title Access", () => {
    expect(read("app/(app)/access/page.tsx")).toContain('export const metadata = { title: "Access" };');
    expect(read("components/admin/AccessList.tsx")).toContain("<AdminGate>");
    const html = render(USERS);
    expect(html).toContain("<h1>Access</h1>");
    expect(html).toContain("Who can see what. Open a person to change their departments, private folders and files given.");
  });

  it("lists every person with their role and departments, and Access for each", () => {
    const html = render(USERS);
    expect(html).toContain("Member");
    expect(html).toContain("Finance (head), Sales");
    expect(html).toContain("Reviewer");
    expect(html.match(/No department/g)).toHaveLength(2);
    expect(html).toContain("Management");
    for (const [id, name] of [["u1", "Priya"], ["me", "Asha"], ["u3", "Ravi"]]) {
      expect(html).toMatch(
        new RegExp(`<a[^>]* href="/admin/people/${id}"[^>]*>Access<span class="visually-hidden"> for ${name}</span><span aria-hidden="true"> →</span></a>`),
      );
    }
  });

  it("marks the signed-in person (you), and no one else", () => {
    const html = render(USERS);
    expect(html.match(/\(you\)/g)).toHaveLength(1);
    expect(html).toMatch(/Asha<span class="muted"> \(you\)<\/span>/);
  });

  it("links to the Departments tab and to Documents, where private folders are made", () => {
    for (const html of [render(USERS), render(null), render([])]) {
      expect(html).toMatch(/<a[^>]* href="\/admin\?tab=departments"[^>]*>Departments<\/a>/);
      expect(html).toMatch(/<a[^>]* href="\/documents"[^>]*>Private folders<\/a>/);
    }
  });

  it("says it is loading, with no list yet", () => {
    const html = render(null);
    expect(html).toContain('role="status"');
    expect(html).toContain("Loading the people…");
    expect(html).not.toContain("/admin/people/");
  });

  it("says a failed load plainly, with Try again", () => {
    const html = render(null, "Kaagaz had a problem. Try again in a minute.");
    expect(html).toContain('role="alert"');
    expect(html).toContain("Could not load the people.");
    expect(html).toContain("Kaagaz had a problem. Try again in a minute.");
    expect(html).toContain("Try again</button>");
    expect(html).not.toContain("Loading the people…");
  });

  it("says when there is no one yet, with the way to add people", () => {
    const html = render([]);
    expect(html).toContain("No one is here yet.");
    expect(html).toMatch(/<a[^>]* href="\/admin\?tab=users"[^>]*>Add people on the Users tab<\/a>/);
  });
});
