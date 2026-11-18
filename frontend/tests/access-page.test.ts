// PRD 7 PR 11a (#604): a person's Access page. Its three lists and their empty words, the
// turned-off banner that takes every Give away, the hint on your own page, the 404, no Locked
// files (PR 12), and no primary button on the page itself.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { AdminUser, PersonAccess } from "@/lib/types";

vi.mock("@/lib/session", () => ({ useSession: () => ({ me: { user: { id: "me" } } }), useOptionalSession: () => null }));

const { AccessPageView, accessLoadFailure } = await import("@/components/admin/AccessPage");
const { ApiError } = await import("@/lib/api");
const { SERVER_PROBLEM } = await import("@/lib/errorWords");

const USERS = [
  { id: "u1", display_name: "Priya", email: "priya@example.com" },
  { id: "ravi", display_name: "Ravi", email: "ravi@example.com" },
] as AdminUser[];

function access(changes: Partial<PersonAccess["user"]> = {}, lists: Partial<PersonAccess> = {}): PersonAccess {
  return {
    user: { id: "u1", name: "Priya", role: "member", management: false, disabled: false, ...changes },
    departments: [],
    private_folders: [],
    files: [],
    ...lists,
  };
}

const FULL: Partial<PersonAccess> = {
  departments: [{ id: "d1", name: "Finance", head: true, added_at: "2026-11-03T10:00:00Z", added_by: "ravi" }],
  private_folders: [{ id: "f1", name: "Salaries", path: "Finance / Salaries" }],
  files: [{ id: "doc1", name: "Sale deed.pdf", path: "Sales / 2026", given_at: "2026-11-03T10:00:00Z", given_by: null }],
};

const render = (data: { access: PersonAccess; users: AdminUser[] } | null, failed: string | null = null, myId = "me") =>
  renderToStaticMarkup(
    createElement(AccessPageView, { data, failed, myId, onRetry: () => undefined, onChanged: () => undefined }),
  );

describe("the Access page", () => {
  it("names the person, their role and email, under Back to Users", () => {
    const html = render({ access: access(), users: USERS });
    expect(html).toContain('href="/admin?tab=users"');
    expect(html).toContain("Back to Users");
    expect(html).toContain("<h1>Access for Priya</h1>");
    expect(html).toContain("Member · priya@example.com");
    expect(html).toContain("Sees all files (Management): No");
    expect(html).toContain(">Make Management</button>");
  });

  it("lists departments with the Head tick, private folders by path and files as plain text", () => {
    const html = render({ access: access({ management: true }, FULL), users: USERS });
    expect(html).toContain("Sees all files (Management): Yes");
    expect(html).toContain(">Stop Management</button>");
    expect(html).toMatch(/<input type="checkbox" checked=""\/>Head<span class="visually-hidden"> of Finance<\/span>/);
    expect(html).toMatch(/Added \d{1,2} Nov 2026 by Ravi/);
    expect(html).toContain("Finance / Salaries");
    expect(html).toContain("<span>Sale deed.pdf</span>");
    expect(html).not.toContain("/documents/doc1"); // never a link: the admin may not open it
    expect(html).toMatch(/Given \d{1,2} Nov 2026</);
    expect(html.match(/>Remove<span/g)).toHaveLength(3);
  });

  it("says each list is empty in its own words, and shows no Locked files", () => {
    const html = render({ access: access(), users: USERS });
    expect(html).toContain("Priya isn&#x27;t in any department yet.");
    expect(html).toContain("Priya isn&#x27;t named on any private folder.");
    expect(html).toContain("No files have been given to Priya.");
    expect(html).toContain("Files given to Priya");
    expect(html).not.toMatch(/lock/i);
    expect(html.match(/>Give access<span/g)).toHaveLength(3);
  });

  it("has no primary button of its own: each Give access opens a small form with one", () => {
    expect(render({ access: access({}, FULL), users: USERS })).not.toContain("btn-primary");
  });

  it("gives a turned-off person nothing: the banner, no Give access, no Management, no Head ticks", () => {
    const html = render({ access: access({ disabled: true }, FULL), users: USERS });
    expect(html).toContain("This account is disabled. Enable it from the Users tab to give access again.");
    expect(html).not.toContain("Give access");
    expect(html).not.toContain("Make Management");
    expect(html).toMatch(/<input type="checkbox" disabled="" checked=""\/>Head/);
    expect(html.match(/>Remove<span/g)).toHaveLength(3); // taking access away still works
  });

  it("tells you on your own page that giving yourself access asks for your password", () => {
    const hint = "Giving yourself access asks for your password. Management and the other admins get an email.";
    expect(render({ access: access(), users: USERS }, null, "u1")).toContain(hint);
    expect(render({ access: access(), users: USERS })).not.toContain(hint);
  });

  it("says a person who isn't there is not found, with Back to Users", () => {
    const html = render(null, "missing");
    expect(html).toContain("We couldn&#x27;t find this person.");
    expect(html).toContain('href="/admin?tab=users"');
  });

  it("says it is loading, and a failed load with Try again", () => {
    expect(render(null)).toContain("Loading this person&#x27;s access…");
    const html = render(null, "Kaagaz had a problem. Try again in a minute.");
    expect(html).toContain("Could not load this person&#x27;s access.");
    expect(html).toContain("Try again");
  });

  it("keeps the lists after a failed reload, with the banner and Try again above them", () => {
    const html = render({ access: access({}, FULL), users: USERS }, "Kaagaz had a problem. Try again in a minute.");
    expect(html).toContain("Could not load this person&#x27;s access.");
    expect(html.indexOf("Try again")).toBeLessThan(html.indexOf(">Departments<"));
    expect(html).toContain("Finance / Salaries");
  });

  it("says a person who isn't there, or an id that can't be one, as not found", () => {
    expect(accessLoadFailure(new ApiError(404, "not_found", "x"))).toBe("missing");
    expect(accessLoadFailure(new ApiError(422, "bad_request", "x"))).toBe("missing");
    expect(accessLoadFailure(new ApiError(500, "error", "x"))).toBe(SERVER_PROBLEM);
  });

  it("works without the users list: no email, and no 'by' for who gave what", () => {
    const html = render({ access: access({}, FULL), users: [] });
    expect(html).toContain("<p>Member</p>");
    expect(html).toMatch(/Added \d{1,2} Nov 2026</);
  });

  it("asks before taking the Head tick away", () => {
    const page = readFileSync(join(__dirname, "..", "components/admin/AccessPage.tsx"), "utf8");
    expect(page).toContain("ask(removeHeadQuestion(user.name, department.name), REMOVE_AS_HEAD, () => void setHead(department, false))");
  });

  it("is a route with a fixed tab title that never names the person", () => {
    const route = readFileSync(join(__dirname, "..", "app/(app)/admin/people/[id]/page.tsx"), "utf8");
    expect(route).not.toMatch(/^"use client"/);
    expect(route).toContain('export const metadata = { title: "Admin" };');
    expect(route).toContain("<AccessPage key={id} userId={id} />");
  });
});
