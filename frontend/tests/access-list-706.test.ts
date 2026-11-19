// #706: Access in the sidebar for admins. The link (exactly who sees Admin), its own tab-bar word,
// and the page: every person with a one-line access summary and Access → to their Access page, the
// (you) and Disabled markers, the Departments link, the private folders by path, and the loading,
// empty and error states.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { AdminUser, Folder, Role, SessionScope } from "@/lib/types";

vi.mock("@/lib/session", () => ({ useSession: () => ({ me: { user: { id: "me" } } }) }));

const { navLinks } = await import("@/components/shell/nav");
const { AccessListView } = await import("@/components/admin/AccessList");
const { libraryFromSearch } = await import("@/components/documents/libraryQuery");

const read = (path: string) => readFileSync(join(process.cwd(), path), "utf8");

const USERS = [
  { id: "u1", display_name: "Priya", role: "member", management: false, disabled: false, departments: [{ id: "d1", name: "Finance", head: true }, { id: "d2", name: "Sales", head: false }] },
  { id: "me", display_name: "Asha", role: "admin", management: true, disabled: false, departments: [] },
  { id: "u3", display_name: "Ravi", role: "reviewer", departments: [], disabled: true },
] as AdminUser[];

const FINANCE = "3f2b6c1e-0000-4000-8000-000000000001";
const SALARIES = "3f2b6c1e-0000-4000-8000-000000000002";
const INSIDE = "3f2b6c1e-0000-4000-8000-000000000003";
const OPEN = "3f2b6c1e-0000-4000-8000-000000000004";
const FOLDERS = [
  { id: FINANCE, name: "Finance", parent_id: null, kind: "department" },
  { id: SALARIES, name: "Salaries", parent_id: FINANCE, private: true },
  { id: INSIDE, name: "2026", parent_id: SALARIES }, // inside a private folder: not one of its own
  { id: OPEN, name: "Invoices", parent_id: FINANCE, private: false },
] as Folder[];

const render = (users: AdminUser[] | null, failed: string | null = null, folders: Folder[] = FOLDERS) =>
  renderToStaticMarkup(
    createElement(AccessListView, { data: users ? { users, folders } : null, failed, myId: "me", onRetry: () => undefined }),
  );

describe("the Access link", () => {
  const hrefs = (role: Role, scope: SessionScope, management?: boolean) => navLinks(role, scope, management).map((l) => l.href);

  it("shows Access just above Admin, for an admin with an admin session only", () => {
    const links = hrefs("admin", "admin");
    expect(links.indexOf("/access")).toBe(links.indexOf("/admin") - 1);
    expect(navLinks("admin", "admin").find((l) => l.href === "/access")).toMatchObject({ label: "Access", icon: "key" });
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

  it("marks a disabled person Disabled, and no one else", () => {
    const html = render(USERS);
    expect(html.match(/>Disabled</g)).toHaveLength(1);
    expect(html.indexOf(">Disabled<")).toBeGreaterThan(html.indexOf("Ravi"));
  });

  it("links to the Departments tab, whatever the list's state", () => {
    for (const html of [render(USERS), render(null), render([]), render(null, "Kaagaz had a problem.")]) {
      expect(html).toMatch(/<a[^>]* href="\/admin\?tab=departments"[^>]*>Departments<\/a>/);
    }
  });

  it("lists each private folder by its path, opening it in Documents", () => {
    const html = render(USERS);
    expect(html).toContain(">Private folders</h2>");
    expect(html).toMatch(new RegExp(`<a[^>]* href="/documents\\?folder=${SALARIES}"[^>]*>Finance / Salaries</a>`));
    for (const other of [FINANCE, INSIDE, OPEN]) expect(html).not.toContain(`folder=${other}`);
  });

  it("opens that folder: Documents reads ?folder= as the folder to show", () => {
    expect(libraryFromSearch(new URLSearchParams(`folder=${SALARIES}`)).folderId).toBe(SALARIES);
  });

  it("says when there is no private folder yet, and how to make one", () => {
    const html = render(USERS, null, FOLDERS.filter((folder) => !folder.private));
    expect(html).toContain("No private folders yet. Open a folder in Documents and choose Make private.");
    expect(html).not.toContain("/documents?folder=");
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
    expect(html).toContain("Could not load the people and private folders.");
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
