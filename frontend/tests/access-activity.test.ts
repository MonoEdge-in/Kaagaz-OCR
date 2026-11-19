// PRD 7 PR 11c (#604): the access changes in words, the "Gave themselves access" chip, the
// Admin tab's About a person filter and self-given block, and Management's Access activity link and
// call.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { NO_AUDIT_FILTERS, auditChipTexts, auditFilterCount, auditParams } from "@/components/activity/auditFilters";
import { TimelineRow } from "@/components/activity/Timeline";
import { ACTIVITY_WORDS, SELF_GIVEN, SELF_GIVEN_HEADING, activitySentence } from "@/components/activity/words";
import { ActivityFilters, ActivityTable, SelfGivenBlock } from "@/components/admin/ActivityPanel";
import { navLinks } from "@/components/shell/nav";
import * as api from "@/lib/api";
import type { ActivityEntry, Role, SessionScope } from "@/lib/types";

const read = (path: string) => readFileSync(join(__dirname, "..", path), "utf8");

const entry = (action: string, more: Partial<ActivityEntry> = {}): ActivityEntry => ({
  id: 1,
  action,
  actor_id: "ravi",
  actor_name: "Ravi",
  target_type: "folder",
  target_id: "fin",
  subject_id: "priya",
  subject_name: "Priya",
  document: null,
  folder: { id: "fin", path: "Finance" },
  details: {},
  created_at: "2026-11-03T10:00:00Z",
  ...more,
});
const onFile: Partial<ActivityEntry> = {
  target_type: "document",
  target_id: "d1",
  document: { id: "d1", name: "Sale deed.pdf", version_no: 2, deleted: false },
  folder: { id: "inv", path: "Finance / Invoices" },
};
// The server's mask for Management (backend views.timeline): no id, folder, version or details but self.
const hidden: Partial<ActivityEntry> = {
  target_type: "document",
  target_id: null,
  family_id: null,
  document: { id: null, name: "a file you can't see", version_no: null, deleted: false },
  folder: null,
};
const salaries = { folder: { id: "sal", path: "Finance / Salaries" } };

describe("the access changes in words", () => {
  it("says every access action the server writes, after who made it", () => {
    const said: [string, Partial<ActivityEntry>, string][] = [
      ["access.management_on", { target_type: "user", folder: null }, "Ravi made Priya Management"],
      ["access.management_off", { target_type: "user", folder: null }, "Ravi took Management away from Priya"],
      ["access.member_add", {}, "Ravi added Priya to Finance"],
      ["access.member_add", { details: { head: true } }, "Ravi added Priya to Finance as head"],
      ["access.member_add", { details: { head: false } }, "Ravi added Priya to Finance"],
      ["access.member_remove", {}, "Ravi removed Priya from Finance"],
      ["access.head_on", {}, "Ravi made Priya head of Finance"],
      ["access.head_off", {}, "Ravi removed Priya as head of Finance"],
      ["access.department_only_on", { subject_id: null, subject_name: undefined }, "Ravi kept Finance to its people"],
      ["access.department_only_off", { subject_id: null, subject_name: undefined }, "Ravi opened Finance to everyone"],
      ["access.private_on", { subject_id: null, ...salaries }, "Ravi made Finance / Salaries private"],
      ["access.private_off", { subject_id: null, ...salaries }, "Ravi made Finance / Salaries open again"],
      ["access.named_add", salaries, "Ravi named Priya on Finance / Salaries"],
      ["access.named_remove", salaries, "Ravi took Priya off Finance / Salaries"],
      ["access.file_give", onFile, "Ravi gave Priya access to Sale deed.pdf"],
      ["access.file_take", onFile, "Ravi removed Priya's access to Sale deed.pdf"],
      [
        "access.switched_off",
        { target_type: "user", folder: null },
        "Ravi turned off Priya's account; their access was removed",
      ],
      [
        "access.switched_on",
        { target_type: "user", folder: null },
        "Ravi turned on Priya's account; their old access was cleared",
      ],
      [
        "access.switched_off",
        { target_type: "user", folder: null, subject_name: null },
        "Ravi turned off someone's account; their access was removed",
      ],
    ];
    for (const [action, more, sentence] of said) expect(activitySentence(entry(action, more)), action).toBe(sentence);
    // Every access action the backend writes has its words here: the routes (kaagaz/api/routers/
    // access.py) and 0090's users_switched trigger (turning an account off or on).
    const routes = read("../backend/kaagaz/api/routers/access.py").match(/"access\.[a-z_]+"/g) ?? [];
    const trigger = read("../backend/migrations/versions/0090_access_api.py").match(/'access\.[a-z_]+'/g) ?? [];
    const written = new Set([...routes, ...trigger].map((quoted) => quoted.slice(1, -1)));
    expect(written.size).toBe(16);
    expect(written).toContain("access.switched_off");
    expect(written).toContain("access.switched_on");
    for (const action of written) expect(Object.keys(ACTIVITY_WORDS), action).toContain(action);
  });

  it("says themselves for an Admin's own access, and someone for a person since removed", () => {
    const self = { subject_id: "ravi", subject_name: "Ravi" };
    expect(activitySentence(entry("access.management_on", self))).toBe("Ravi made themselves Management");
    expect(activitySentence(entry("access.member_add", self))).toBe("Ravi added themselves to Finance");
    expect(activitySentence(entry("access.file_take", { ...onFile, ...self }))).toBe(
      "Ravi removed their own access to Sale deed.pdf",
    );
    expect(activitySentence(entry("access.named_add", { ...salaries, subject_name: null }))).toBe(
      "Ravi named someone on Finance / Salaries",
    );
    expect(activitySentence(entry("access.file_take", { ...onFile, subject_name: null }))).toBe(
      "Ravi removed someone's access to Sale deed.pdf",
    );
  });

  it("names a file Management can't see as the server does, and a deleted one as deleted", () => {
    expect(activitySentence(entry("access.file_give", hidden))).toBe("Ravi gave Priya access to a file you can't see");
    const gone = { ...onFile, document: { id: "d1", name: null, version_no: 1, deleted: true } };
    expect(activitySentence(entry("access.file_take", gone))).toBe("Ravi removed Priya's access to a deleted document");
    expect(activitySentence(entry("access.member_add", { folder: null }))).toBe("Ravi added Priya to a folder");
  });
});

describe("the Gave themselves access chip", () => {
  const mine = entry("access.member_add", { subject_id: "ravi", details: { self: true, head: false } });
  const chip = `<span class="status-chip" data-tone="neutral">${SELF_GIVEN}</span>`;

  it("names no role: 10a audits a Head naming themselves on a private folder as self too", () => {
    expect(SELF_GIVEN).toBe("Gave themselves access");
    expect(SELF_GIVEN_HEADING).toBe("People who gave themselves access");
    const head = entry("access.named_add", {
      actor_id: "asha",
      actor_name: "Asha",
      subject_id: "asha",
      subject_name: "Asha",
      folder: { id: "sal", path: "Finance / Salaries" },
      details: { self: true },
    });
    const html = renderToStaticMarkup(createElement(ActivityTable, { items: [head] }));
    expect(html).toContain("named themselves on Finance / Salaries");
    expect(html).toContain(chip);
    expect(html).not.toMatch(/>[^<]*admin[^<]*</i); // no text names a role
    expect(renderToStaticMarkup(createElement(SelfGivenBlock, { items: [head] }))).not.toMatch(/>[^<]*admin[^<]*</i);
  });

  it("marks only the entries in which someone gave themselves access, in the table and the timeline", () => {
    expect(renderToStaticMarkup(createElement(ActivityTable, { items: [mine] }))).toContain(chip);
    expect(renderToStaticMarkup(createElement(ActivityTable, { items: [entry("access.member_add")] }))).not.toContain(SELF_GIVEN);
    const row = (item: ActivityEntry) => renderToStaticMarkup(createElement(TimelineRow, { entry: item, showDocument: false }));
    expect(row(mine)).toContain(chip);
    expect(row(entry("access.member_add", { details: { self: false } }))).not.toContain(SELF_GIVEN);
    // The mask keeps `self`, so Management sees the chip on a file they can't see.
    expect(row(entry("access.file_give", { ...hidden, details: { self: true } }))).toContain(chip);
  });

  it("puts the self-given entries first under their own heading, as a table", () => {
    const html = renderToStaticMarkup(createElement(SelfGivenBlock, { items: [mine] }));
    expect(html).toMatch(/^<div class="stack"><h3[^>]*>People who gave themselves access<\/h3>/);
    expect(html).toContain('<caption class="visually-hidden">People who gave themselves access, newest first</caption>');
    expect(html).toContain("added themselves to Finance");
  });
});

describe("the Admin tab's About a person filter", () => {
  it("asks for the entries about one person, counts as a filter, and says whom", () => {
    const filters = { ...NO_AUDIT_FILTERS, subjectId: "priya" };
    expect(auditParams(filters).subject_id).toBe("priya");
    expect(auditParams(NO_AUDIT_FILTERS).subject_id).toBeUndefined();
    expect(auditFilterCount(filters)).toBe(1);
    expect(auditChipTexts(filters, { person: null, about: "Priya", folder: null }).about).toBe("About: Priya");
    expect(auditChipTexts(NO_AUDIT_FILTERS, { person: null, about: null, folder: null }).about).toBe("About a person");
    expect(api.auditExportUrl(auditParams(filters))).toBe("/api/admin/audit/export?subject_id=priya");
  });

  it("is a chip beside Person, removable once set", () => {
    const people = [{ id: "priya", name: "Priya" }];
    const props = { onChange: () => undefined, people, folders: [] };
    const off = renderToStaticMarkup(createElement(ActivityFilters, { filters: NO_AUDIT_FILTERS, ...props }));
    expect(off.indexOf(">Person</button>")).toBeLessThan(off.indexOf(">About a person</button>"));
    expect(off.indexOf(">About a person</button>")).toBeLessThan(off.indexOf(">Folder</button>"));
    const on = renderToStaticMarkup(createElement(ActivityFilters, { filters: { ...NO_AUDIT_FILTERS, subjectId: "priya" }, ...props }));
    expect(on).toContain(">About: Priya</button>");
    expect(on).toContain('aria-label="Remove filter: About: Priya"');
    expect(on).toMatch(/Clear all <span class="tabular">\(1\)<\/span>/);
  });
});

describe("Management's Access activity link and call", () => {
  const labels = (role: Role, scope: SessionScope, management?: boolean) =>
    navLinks(role, scope, management).map((link) => link.label);

  it("shows the link to Management only, never to an Admin (the Admin tab has it all)", () => {
    // Changed on purpose (#707): My access, everyone's.
    expect(labels("member", "app", true)).toEqual(["Home", "Documents", "Ask", "My words", "My access", "Access activity"]);
    expect(labels("reviewer", "app", true)).toContain("Access activity");
    // Changed on purpose (#706): an admin session also has Access, just above Admin.
    expect(labels("admin", "admin", true)).toEqual(["Home", "Documents", "Ask", "My words", "Review", "Access", "Admin"]);
    expect(labels("admin", "app", true)).not.toContain("Access activity");
    expect(labels("member", "app", false)).not.toContain("Access activity");
    expect(labels("admin", "admin")).not.toContain("Access activity");
    const link = navLinks("member", "app", true).find((each) => each.label === "Access activity");
    expect(link?.href).toBe("/access-activity");
    // Changed on purpose (#706): the phone tab bar says "Activity", not "Access" (the admins' Access link);
    // the drawer and the page keep "Access activity".
    expect(link?.short).toBe("Activity");
    expect(read("components/shell/AppShell.tsx")).toContain("<span>{link.short ?? link.label}</span>");
    expect(read("components/shell/ShellMenu.tsx")).toContain("<span>{link.label}</span>");
    expect(read("components/shell/AppShell.tsx")).toContain(
      "navLinks(me.user.role, sessionScope(me), me.access?.management === true)",
    );
  });

  afterEach(() => vi.unstubAllGlobals());

  it("reads GET /access/activity, then by the reply's cursor", async () => {
    const calls: string[] = [];
    vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
      calls.push(`${init.method} ${url}`);
      return new Response('{"items":[],"next_cursor":null}', { status: 200 });
    });
    await api.listAccessActivity();
    await api.listAccessActivity("1762164000000000-42");
    expect(calls).toEqual(["GET /api/access/activity", "GET /api/access/activity?cursor=1762164000000000-42"]);
  });

  it("is a page of its own, with no export", () => {
    expect(read("app/(app)/access-activity/page.tsx")).toContain('export const metadata = { title: "Access activity" };');
    expect(read("components/activity/AccessActivity.tsx")).not.toMatch(/ExportUrl|Export to Excel|download/);
  });
});
