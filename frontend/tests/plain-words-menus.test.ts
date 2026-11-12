// Plain words in action menus (25 Oct 2026; the product owner: people "will not be able to
// understand what these features are"). Every item in the super admin's organisation menu,
// the admin's user menu and a document's menu has one plain line under its label saying
// what happens and to whom; whatever takes something away asks first; the confirm button
// says the item's own verb.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { RowMenu } from "@/components/admin/RowMenu";
import { USER_MENU_HELP } from "@/components/admin/UsersPanel";
import { ACTION_HELP } from "@/components/documents/library";
import { orgConfirm } from "@/components/operator/confirms";
import { MENU_HELP } from "@/components/operator/OrganisationsView";
import type { Organisation } from "@/lib/types";

const read = (path: string) => readFileSync(join(__dirname, "..", path), "utf8");

/** One sentence or two, in plain words, ending with a full stop. */
function plainLine(text: string) {
  expect(text, text).toMatch(/^[A-Z].{10,110}\.$/);
  expect(text, text).not.toMatch(/\b(OCR|RPM|token|pipeline|extraction|flagged)\b/i);
}

describe("the super admin's organisation menu", () => {
  const view = read("components/operator/OrganisationsView.tsx");
  const row = view.slice(view.indexOf("function OrgRow("));
  const changes = view.slice(view.indexOf("const CHANGES"), view.indexOf("function OrgRow("));

  it("gives every item a plain line saying what it does", () => {
    for (const text of Object.values(MENU_HELP)) plainLine(text);
    const labels = (source: string) => source.match(/\blabel: "/g)?.length ?? 0;
    const lines = (source: string) => source.match(/\bdescription: MENU_HELP\.\w+/g)?.length ?? 0;
    expect(labels(row)).toBeGreaterThanOrEqual(7);
    expect(lines(row)).toBe(labels(row));
    expect(lines(changes)).toBe(labels(changes));
    expect(MENU_HELP.enable).toBe("Lets this organisation's people sign in again.");
  });

  it("makes the description required, shows it under the label and reads it as the item's description", () => {
    const menu = read("components/operator/ManageMenu.tsx");
    expect(menu).toMatch(/\n {2}description: string;/);
    expect(menu).toContain("aria-describedby={hintId(item)}");
    expect(menu).toContain('<span id={hintId(item)} className={styles.menuHint}>');
  });

  it("asks first, with the consequence, before Disable and Delete, and keeps the verb in the toast", () => {
    const org = { id: "o1", name: "Sharma Traders" } as Organisation;
    for (const change of ["disable", "delete"] as const) {
      const confirm = orgConfirm(org, change, Date.UTC(2026, 9, 25));
      expect(confirm.danger).toBe(true);
      expect(confirm.effect.length).toBeGreaterThan(20);
    }
    expect(changes).toMatch(/change: "disable"[^}]*danger: true/);
    expect(changes).toMatch(/change: "delete"[^}]*danger: true/);
    expect(row).toContain("onSelect: () => onChange(change)");
    const enable = orgConfirm(org, "enable", 0);
    expect([enable.action, enable.done]).toEqual(["Enable organisation", "Enabled Sharma Traders."]);
  });
});

describe("the admin's user menu", () => {
  const panel = read("components/admin/UsersPanel.tsx");
  const items = panel.slice(panel.indexOf("const items: RowMenuItem[]"), panel.indexOf("return (", panel.indexOf("const items: RowMenuItem[]")));

  it("gives every item a plain line saying what it does", () => {
    for (const text of Object.values(USER_MENU_HELP)) plainLine(text);
    const labels = items.match(/\blabel: "/g)?.length ?? 0;
    expect(labels).toBe(8); // Changed on purpose (#457 phase 1): Remove connected accounts
    expect(items.match(/\bdescription: USER_MENU_HELP\.\w+/g)).toHaveLength(labels);
    // Reset password makes a one-time password the admin gives them (POST .../reset-password).
    expect(USER_MENU_HELP.password).toMatch(/one-time password for you to give them/);
  });

  it("asks first before every item that takes something away", () => {
    // Changed on purpose (#457 phase 3): the spec's "Reset two-step code".
    for (const label of ["Allow Delete forever", "Reset password", "Reset two-step code", "Disable"]) {
      expect(items, label).toMatch(new RegExp(`label: "${label}"[^}]*onSelect: \\(\\) => setConfirming\\(`));
    }
    expect(items).toMatch(/label: "Disable"[^}]*destructive: true/);
  });

  it("says the item's own verb on the confirm button", () => {
    expect(panel).toMatch(/disable: "Disable",\s*password: "Reset password",\s*"2fa": "Reset two-step code",\s*forever: "Allow Delete forever",/);
    expect(panel).not.toMatch(/Yes, (disable|reset|allow)|2FA/);
  });

  it("shows the line under the label, read as the item's description", () => {
    const html = renderToStaticMarkup(
      createElement(RowMenu, {
        name: "Ravi",
        items: [{ label: "Enable", description: USER_MENU_HELP.enable, onSelect: () => undefined }],
      }),
    );
    const described = /aria-label="Enable" aria-describedby="([^"]+)"/.exec(html)?.[1];
    expect(described).toBeTruthy();
    expect(html).toContain(`<span id="${described}" class="`);
    expect(html).toContain(">Lets them sign in again.</span>");
  });
});

describe("a document's menu", () => {
  it("gives every action a plain line, and says Delete forever can't be undone", () => {
    for (const text of Object.values(ACTION_HELP)) plainLine(text);
    expect(ACTION_HELP.forever).toMatch(/can't be recovered/);
    const actions = read("components/documents/DocumentActions.tsx");
    expect(actions).toContain("{ACTION_HELP[entry.action]}");
    expect(actions).toContain("{ACTION_HELP.download}");
  });
});
