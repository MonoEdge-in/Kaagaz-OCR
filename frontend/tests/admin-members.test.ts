// Each member's numbers on the admin's Numbers tab (#386): a real table (caption, column
// headers, a sort button in each header and aria-sort on the sorted one), a card per person on
// a phone (data-label on every figure), plain words, and never money or tokens. The sort is
// pure and tested apart: the same header turns it round, a figure starts with the most, a
// never-active person always goes last.

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { MEMBER_PERIODS, nextSort, sortMembers } from "@/components/admin/logic";
import { MembersTableView } from "@/components/admin/MembersTable";
import type { MembersState } from "@/components/admin/MembersTable";
import { NumbersPanel } from "@/components/admin/NumbersPanel";
import type { MemberNumbers, MembersReply } from "@/lib/types";

const noop = () => undefined;

const member = (name: string, changes: Partial<MemberNumbers> = {}): MemberNumbers => ({
  user_id: `id-${name}`,
  display_name: name,
  email: `${name.toLowerCase()}@example.test`,
  role: "member",
  disabled: false,
  documents_uploaded: 1,
  pages_read: 1,
  questions_asked: 1,
  values_verified: 1,
  last_active_at: "2026-10-20T09:00:00Z",
  ...changes,
});

const reply = (members: MemberNumbers[]): MembersReply => ({ period: "month", from: "2026-10-01", to: "2026-10-27", members });

const view = (state: MembersState) =>
  renderToStaticMarkup(createElement(MembersTableView, { period: "month", onPeriod: noop, state, onRetry: noop }));

const PEOPLE = [
  member("Meera", { documents_uploaded: 12, pages_read: 340, questions_asked: 9, values_verified: 4 }),
  member("Arjun", { role: "reviewer", pages_read: 25, values_verified: 30, last_active_at: null }),
  member("Zoya", { role: "admin", disabled: true, pages_read: 25 }),
];

describe("each member's numbers", () => {
  const html = view({ kind: "ready", reply: reply(PEOPLE) });

  it("is a table with a caption, column headers and a sort button in each", () => {
    expect(html).toContain('<table class="table');
    expect(html).toContain('role="table"');
    expect(html).toMatch(/<caption class="visually-hidden">Each member(&#x27;|')s numbers, this month, member ascending<\/caption>/);
    const headers = [...html.matchAll(/<th scope="col" role="columnheader"[^>]*><button type="button"[^>]*>([^<]*)</g)].map((m) => m[1]);
    expect(headers).toEqual(["Member", "Files uploaded", "Pages read", "Questions asked", "Values verified", "Last active"]);
    expect(html.match(/aria-sort=/g)).toHaveLength(1);
    expect(html).toMatch(/aria-sort="ascending"><button[^>]*>Member<span[^>]*aria-hidden="true">↑<\/span>/);
  });

  it("puts the people A to Z first, each figure with its label for the phone's card", () => {
    expect([...html.matchAll(/<tr role="row"><td role="cell">([^<]*)</g)].map((m) => m[1])).toEqual(["Arjun", "Meera", "Zoya"]);
    for (const label of ["Files uploaded", "Pages read", "Questions asked", "Values verified", "Last active"]) {
      expect(html.match(new RegExp(`data-label="${label}"`, "g"))).toHaveLength(3);
    }
    expect(html).toContain('data-label="Pages read" class="num">340<');
    expect(html).toContain("Reviewer");
    expect(html).toContain("Admin, turned off");
  });

  it("says Never for someone who never signed in, and nothing of money or tokens", () => {
    expect(html).toContain(">Never<");
    expect(html).not.toMatch(/\$|token|cost|spend|OCR|model/i);
  });

  it("offers this month, the last 30 days and all time", () => {
    expect(MEMBER_PERIODS.map((option) => option.label)).toEqual(["This month", "Last 30 days", "All time"]);
    expect(html).toContain('<label class="label" for="members-period">Period</label>');
  });

  it("says when it is loading and offers Try again when it could not load", () => {
    expect(view({ kind: "loading" })).toContain('role="status"');
    const failed = view({ kind: "error", message: "The server is busy." });
    expect(failed).toContain('role="alert"');
    expect(failed).toContain("The server is busy.");
    expect(failed).toContain("Try again");
  });

  it("is on the Numbers tab, under the organisation's numbers", () => {
    expect(renderToStaticMarkup(createElement(NumbersPanel))).toContain("Each member");
  });
});

describe("sorting the members", () => {
  it("turns the same column round, and starts a figure with the most and a name A to Z", () => {
    const byName = { column: "name", dir: "ascending" } as const;
    expect(nextSort(byName, "name", "name")).toEqual({ column: "name", dir: "descending" });
    expect(nextSort(byName, "pages_read", "name")).toEqual({ column: "pages_read", dir: "descending" });
    expect(nextSort({ column: "pages_read", dir: "descending" }, "pages_read", "name")).toEqual({ column: "pages_read", dir: "ascending" });
  });

  it("sorts by a figure with ties by name, and a never-active person last either way", () => {
    const names = (sorted: MemberNumbers[]) => sorted.map((m) => m.display_name);
    expect(names(sortMembers(PEOPLE, { column: "pages_read", dir: "descending" }))).toEqual(["Meera", "Arjun", "Zoya"]);
    expect(names(sortMembers(PEOPLE, { column: "pages_read", dir: "ascending" }))).toEqual(["Arjun", "Zoya", "Meera"]);
    expect(names(sortMembers(PEOPLE, { column: "values_verified", dir: "descending" }))).toEqual(["Arjun", "Meera", "Zoya"]);
    for (const dir of ["ascending", "descending"] as const) {
      expect(names(sortMembers(PEOPLE, { column: "last_active_at", dir })).at(-1)).toBe("Arjun");
    }
    expect(names(sortMembers(PEOPLE, { column: "name", dir: "descending" }))).toEqual(["Zoya", "Meera", "Arjun"]);
  });
});
