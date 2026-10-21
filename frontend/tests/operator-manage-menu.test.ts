// The super admin's Organisations table (owner, 16 Oct 2026): one Manage menu per row in
// place of five stacked buttons that ran past the table's edge, the status changes apart
// at the bottom, "33 minutes ago" on one line, long names cut with an ellipsis.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ManageMenu, menuPlace } from "@/components/operator/ManageMenu";

const read = (path: string) => readFileSync(join(__dirname, "..", path), "utf8");
const view = read("components/operator/OrganisationsView.tsx");
const menu = read("components/operator/ManageMenu.tsx");
const css = read("components/operator/Operator.module.css");
const rule = (selector: string) => new RegExp(`\\n${selector.replace(".", "\\.")} \\{([^}]*)\\}`).exec(css)?.[1] ?? "";
const row = view.slice(view.indexOf("function OrgRow("));

describe("one Manage menu per organisation", () => {
  it("is the row's only action: no stacked buttons", () => {
    expect(row.match(/<ManageMenu\b/g)).toHaveLength(1);
    expect(row).not.toContain("<button");
    expect(view).not.toMatch(/rowActions|actionGroups/);
  });

  it("names the organisation and says it opens a menu", () => {
    const html = renderToStaticMarkup(
      createElement(ManageMenu, { name: "Sharma Traders", safe: [], changes: [], buttonId: "org-ai-1" }),
    );
    expect(html).toContain('aria-label="Manage Sharma Traders"');
    expect(html).toContain('aria-haspopup="menu"');
    expect(html).toContain('aria-expanded="false"');
    expect(html).toContain('id="org-ai-1"'); // AI settings gives focus back here
    expect(html.match(/<button/g)).toHaveLength(1); // the menu itself is drawn only when open
    // Changed on purpose (Tushar, 16 Oct 2026, one control height / centred icons): Manage is a
    // plain .btn, one control tall like the rest of the row, no 32px rule of its own.
    expect(html).toContain('class="btn"');
    expect(rule(".manage")).toBe("");
  });

  it("lists Admins, AI settings and auto-filing, then the status changes after a separator", () => {
    expect(row).toMatch(/safe=\{\[\s*\{ label: "Admins"/);
    expect(row).toContain('{ label: "AI settings", onSelect: onModels }');
    expect(row).toContain('{ label: "Request limits", onSelect: onModelRates }');
    expect(row).toContain('org.autofile_granted ? "Stop auto-filing" : "Allow auto-filing"');
    expect(row).toContain("changes={CHANGES[org.status].map(");
    expect(menu).toMatch(/\{safe\.map\(entry\)\}\s*\{safe\.length > 0 && changes\.length > 0 && <div role="separator"/);
    expect(menu.indexOf('role="separator"')).toBeLessThan(menu.indexOf("{changes.map(entry)}"));
    expect(row).not.toMatch(/two-step|twoStep/i);
  });

  it("is a portal menu with a 1px border and no shadow, keyboard and outside-click safe", () => {
    expect(menu).toContain("createPortal(");
    expect(menu).toContain("document.body");
    for (const key of ['"ArrowDown"', '"ArrowUp"', '"Home"', '"End"', '"Escape"']) expect(menu).toContain(key);
    expect(menu).toContain('document.addEventListener("click", swallow, { capture: true, once: true });');
    expect(rule(".menu")).toContain("border: 1px solid var(--border);");
    expect(rule(".menu")).not.toContain("box-shadow");
  });
});

describe("where the menu goes", () => {
  const window = { width: 1024, height: 768 };
  const menuSize = { width: 192, height: 200 };

  it("opens below the button, right edges lined up, so its first item is not under the pointer", () => {
    const button = { top: 100, bottom: 136, left: 800, right: 900 };
    expect(menuPlace(button, menuSize, window)).toEqual({ top: 140, left: 708 });
  });

  it("flips upward near the bottom edge", () => {
    const button = { top: 700, bottom: 736, left: 800, right: 900 };
    expect(menuPlace(button, menuSize, window)).toEqual({ top: 496, left: 708 });
  });

  it("stays inside the window on a narrow screen", () => {
    const button = { top: 100, bottom: 136, left: 0, right: 90 };
    expect(menuPlace(button, menuSize, { width: 320, height: 568 }).left).toBe(8);
    const place = menuPlace({ top: 100, bottom: 136, left: 300, right: 400 }, menuSize, { width: 320, height: 568 });
    expect(place.left + menuSize.width).toBeLessThanOrEqual(320 - 8);
  });
});

describe("the cells", () => {
  it("keeps Last activity on one line and cuts a long name with its title", () => {
    expect(row).toContain("<span className={styles.when}>{lastActivityText(org.last_activity_at, now)}</span>");
    expect(rule(".when")).toContain("white-space: nowrap;");
    expect(row).toContain("className={styles.orgNameText} title={org.name}");
    expect(rule(".orgNameText")).toMatch(/white-space: nowrap;[\s\S]*text-overflow: ellipsis;/);
  });

  it("sits in a wrapper that alone scrolls sideways", () => {
    expect(view).toContain("<div className={`${table.reflowWrap} ${admin.tableScroll}`}>");
  });
});
