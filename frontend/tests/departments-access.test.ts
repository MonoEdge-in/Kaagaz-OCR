// PRD 7 PR 11a (#604): the Departments tab's access. Each row's head, people and status; the
// warning for Department only with nobody in it; the Department only dialog, whose button waits
// for the names; the new department's head step.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { DepartmentOnlyView } from "@/components/admin/DepartmentOnlyDialog";
import { DepartmentPeople } from "@/components/admin/DepartmentPeople";
import { DepartmentsPanel } from "@/components/admin/DepartmentsPanel";
import { NewDepartmentHead } from "@/components/admin/NewDepartmentHead";
import type { AdminDepartment, Folder, PlacePreview } from "@/lib/types";

const folder = (id: string, name: string, sort_order: number): Folder => ({
  id,
  name,
  parent_id: null,
  created_at: "2026-11-01T00:00:00Z",
  kind: "department",
  sort_order,
});
const FOLDERS = [folder("d1", "Finance", 0), folder("d2", "Sales", 1)];
const FINANCE: AdminDepartment = {
  id: "d1",
  name: "Finance",
  department_only: false,
  members: 12,
  heads: [{ id: "u1", name: "Priya Shah" }],
  warnings: [],
};
const SALES: AdminDepartment = { id: "d2", name: "Sales", department_only: true, members: 0, heads: [], warnings: ["no_members"] };

const people = (n: number) => Array.from({ length: n }, (_, i) => ({ id: `p${i}`, name: `Person ${i}` }));
const nothing = () => undefined;

describe("each department's row", () => {
  const html = renderToStaticMarkup(createElement(DepartmentsPanel, { initial: FOLDERS, initialAccess: [FINANCE, SALES] }));
  const finance = html.slice(html.indexOf(">Finance<"), html.indexOf(">Sales<"));
  const sales = html.slice(html.indexOf(">Sales<"));

  it("says its head, how many people, and whether it is open, with People and the switch", () => {
    expect(finance).toContain("Head: Priya Shah");
    expect(finance).toContain("12 people");
    expect(finance).toContain("<span>Open to everyone</span>");
    expect(finance).toContain("People (12)");
    expect(finance).toMatch(/>Keep to its people<span class="visually-hidden">: Finance<\/span>/);
    expect(sales).toContain("No head yet");
    expect(sales).toContain('<span class="status-chip" data-tone="working">Department only</span>');
    expect(sales).toMatch(/>Open to everyone<span class="visually-hidden">: Sales<\/span>/);
  });

  it("warns when Department only is on with nobody in it, with Add people", () => {
    expect(sales).toContain("No one is in Sales yet. Only Management and people given a file directly will see its files.");
    expect(sales).toMatch(/>Add people<span class="visually-hidden"> to Sales<\/span>/);
    expect(finance).not.toContain("No one is in");
  });

  it("keeps Rename and the order buttons, and shows no access before the list answers", () => {
    const bare = renderToStaticMarkup(createElement(DepartmentsPanel, { initial: FOLDERS }));
    expect(bare).toContain('aria-label="Move up: Sales"');
    expect(bare).not.toContain("People (");
  });
});

describe("the Department only dialog", () => {
  const view = (department: AdminDepartment, preview: PlacePreview | null, failed = false) =>
    renderToStaticMarkup(
      createElement(DepartmentOnlyView, {
        department,
        myId: "me",
        preview,
        failed,
        onRetry: nothing,
        onClose: nothing,
        onAddPeople: nothing,
        onConfirm: nothing,
      }),
    );

  it("checks who would lose access first, and its button waits for the names", () => {
    const html = view(FINANCE, null);
    expect(html).toContain("Keep Finance to its people?");
    expect(html).toContain('<div role="status"><p class="muted">Checking who would lose access…</p></div>');
    expect(html).toMatch(/<button type="button" class="btn btn-danger" disabled="">Keep to its people<\/button>/);
  });

  it("says a failed check, with Try again, and still waits", () => {
    const html = view(FINANCE, null, true);
    expect(html).toContain('<div role="status"><p class="error-text">Couldn&#x27;t check who would lose access.</p></div>');
    expect(html).toContain("Try again");
    expect(html).toMatch(/disabled="">Keep to its people/);
  });

  it("names who loses access, Show all for the rest, then Cancel, Add people to Finance, Keep to its people", () => {
    const html = view(FINANCE, { losing: people(14), gaining: [], files: 120 });
    // The names are read in the same live region that said "Checking…".
    expect(html).toContain('<div role="status"><p>14 people will no longer see Finance files: Person 0, Person 1 and 12 more.');
    expect(html).toContain("<summary>Show all 14</summary>");
    expect(html.match(/<li>Person \d+<\/li>/g)).toHaveLength(14);
    const at = ['data-autofocus="true">Cancel', ">Add people to Finance<", ">Keep to its people<"].map((words) => html.indexOf(words));
    expect(at.every((i) => i > 0)).toBe(true);
    expect(at).toEqual([...at].sort((a, b) => a - b));
    expect(html).not.toMatch(/disabled="">Keep/);
  });

  it("adds the words for a department with nobody in it, and no Show all for three names or fewer", () => {
    const html = view({ ...FINANCE, members: 0 }, { losing: people(3), gaining: [], files: 4 });
    expect(html).toContain("No one is in Finance yet, so only Management will see its files.");
    expect(html).not.toContain("Show all");
  });

  it("opens again with how many files everyone will see", () => {
    const html = view(SALES, { losing: [], gaining: people(5), files: 120 });
    expect(html).toContain("Open Sales to everyone?");
    expect(html).toContain("Everyone in the organisation will be able to see Sales&#x27;s 120 files.");
    expect(html).toMatch(/class="btn btn-primary">Open to everyone<\/button>/);
    expect(html).not.toContain("Add people");
  });

  it("asks for the password first when the admin is among those who gain the files", () => {
    const dialog = readFileSync(join(__dirname, "..", "components/admin/DepartmentOnlyDialog.tsx"), "utf8");
    expect(dialog).toContain("onConfirm(asksFirst({ myId, gaining: preview.gaining }))"); // values: access-password-step
  });
});

describe("a department's people", () => {
  it("says it is loading them, and opens with the picker from Add people", () => {
    const html = renderToStaticMarkup(
      createElement(DepartmentPeople, {
        department: { id: "d1", name: "Finance" },
        me: { id: "me", name: "Ravi" },
        adding: true,
        onAdding: nothing,
        run: async () => null,
        onChanged: nothing,
        onNotice: nothing,
      }),
    );
    expect(html).toContain("Loading the people in Finance…");
    expect(html).toContain("<legend>Add people to Finance</legend>");
    expect(html).toMatch(/disabled="">Add to Finance<\/button>/); // nobody chosen yet
  });

  it("asks before taking a Head tick away, in its own words", () => {
    const source = readFileSync(join(__dirname, "..", "components/admin/DepartmentPeople.tsx"), "utf8");
    expect(source).toContain('else setAsking({ member, kind: "head" });');
    expect(source).toContain('asking.kind === "head" ? REMOVE_AS_HEAD');
  });
});

describe("the new department's head step", () => {
  it("asks who heads it, with Choose a person, Me and Decide later", () => {
    const html = renderToStaticMarkup(
      createElement(NewDepartmentHead, {
        department: { id: "d1", name: "Finance" },
        me: { id: "me", name: "Ravi" },
        run: async () => null,
        onDone: nothing,
      }),
    );
    // An h3 under the tab's h2, which takes focus when the step opens.
    expect(html).toMatch(/<h3 [^>]*tab[iI]ndex="-1"[^>]*>Who heads Finance\?<\/h3>/);
    expect(html).toContain("The head sees every Finance file, including its private folders, and chooses who is named on them.");
    for (const button of ["Choose a person", "Me", "Decide later"]) expect(html).toContain(`>${button}</button>`);
  });

  it("is keyed by the department, so a second one starts fresh", () => {
    const panel = readFileSync(join(__dirname, "..", "components/admin/DepartmentsPanel.tsx"), "utf8");
    expect(panel).toContain("key={heading.id}");
  });
});
