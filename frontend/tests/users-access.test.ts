// PRD 7 PR 11a (#604): the Users tab's Access column and its menu items. The column is read only;
// Access opens the person's Access page; Make or Stop Management asks first, asks for your own
// password when you make yourself Management, and is hidden for a turned-off account.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AccessCell, managementItem } from "@/components/admin/UsersPanel";
import type { AdminUser } from "@/lib/types";

const panel = readFileSync(join(__dirname, "..", "components/admin/UsersPanel.tsx"), "utf8");
const items = panel.slice(panel.indexOf("const items: RowMenuItem[]"), panel.indexOf("return (", panel.indexOf("const items: RowMenuItem[]")));

const user = (changes: Partial<AdminUser> = {}) =>
  ({ id: "u1", display_name: "Priya", disabled: false, management: false, departments: [], ...changes }) as AdminUser;
const cell = (changes: Partial<AdminUser>) => renderToStaticMarkup(createElement(AccessCell, { user: user(changes) }));

describe("the Access column", () => {
  it("shows Management first, then the departments, heads marked", () => {
    const html = cell({
      management: true,
      departments: [
        { id: "d1", name: "Finance", head: true },
        { id: "d2", name: "Sales", head: false },
      ],
    });
    expect(html).toContain('title="Sees all files">Management<span class="visually-hidden">: sees all files</span></span>');
    expect(html.indexOf("Management")).toBeLessThan(html.indexOf("Finance (head), Sales"));
  });

  it("says No department, and no chip, for a person in none", () => {
    const html = cell({});
    expect(html).toContain("No department");
    expect(html).not.toContain("status-chip");
  });

  it("sits between Role and Status", () => {
    const headers = ["Role", "Access", "Status"].map((name) => panel.indexOf(`        ${name}\n                  </th>`));
    expect(headers.every((at) => at > 0)).toBe(true);
    expect(headers).toEqual([...headers].sort((a, b) => a - b));
  });
});

describe("the menu", () => {
  it("opens the Access page first", () => {
    expect(items.indexOf('label: "Access"')).toBeLessThan(items.indexOf("can.unlock"));
    expect(items).toContain("router.push(`/admin/people/${user.id}`)");
  });

  it("offers Make or Stop Management, never for a turned-off account", () => {
    expect(managementItem(user())).toBe("make");
    expect(managementItem(user({ management: true }))).toBe("stop");
    expect(managementItem(user({ disabled: true }))).toBeNull();
    expect(managementItem(user({ disabled: true, management: true }))).toBeNull();
  });

  it("asks first, and asks for your password only when you make yourself Management", () => {
    expect(items).toMatch(/label: "Make Management"[^}]*onSelect: \(\) => setConfirming\("makeManagement"\)/);
    expect(items).toMatch(/label: "Stop Management"[^}]*onSelect: \(\) => setConfirming\("stopManagement"\)/);
    // Which way asks first is asksFirst's (tests/access-password-step.test.ts), with these values.
    expect(panel).toContain("asksFirst({ myId, targetId: user.id, on })");
    expect(panel).toContain("makeManagement: makeManagementQuestion,");
    expect(panel).toContain('stopManagement: "Stop Management",');
    // Stop takes something away: the danger style; Make is the plain action.
    expect(panel).toContain('confirming === "makeManagement" ? "btn-primary" : "btn-danger"');
  });
});
