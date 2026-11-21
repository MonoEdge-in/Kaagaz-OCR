// PRD 7 PR 11b (#604): Give access and Who can see this in a document's menu (an Admin both, the
// Head of the file's department Who can see this, anyone else neither; never in Trash), before
// Move to Trash; Give access's caps (100 files, 20 people) checked before sending; its words.

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { GiveAccessForm, giveProblem } from "@/components/access/GiveAccessDialog";
import { giveTitle, sharedToast } from "@/components/access/words";
import { ACTION_HELP, menuEntries } from "@/components/documents/library";
import type { MenuEntry } from "@/components/documents/library";
import type { DocumentSummary } from "@/lib/types";

const doc = (over: Partial<DocumentSummary> = {}) =>
  ({ id: "d1", status: "ready", deleted_at: null, can_delete: true, ...over }) as DocumentSummary;
const words = (entries: MenuEntry[]) => entries.map((entry) => (entry.kind === "download" ? "Download original" : entry.words));
const ADMIN = { giveFile: true, audience: true };
const HEAD = { giveFile: false, audience: true };
const NONE = { giveFile: false, audience: false };
const nothing = () => undefined;

describe("a document's menu", () => {
  it("offers an Admin Give access and Who can see this, before Move to Trash", () => {
    expect(words(menuEntries(doc(), false, "list", false, ADMIN))).toEqual([
      "Rename",
      "Move to folder",
      "Tags",
      "Upload new version",
      "Copy path", // changed on purpose (#730): Copy path and Copy link
      "Copy link",
      "Download original",
      "Give access",
      "Who can see this",
      "Move to Trash",
    ]);
    expect(words(menuEntries(doc(), false, "document", true, ADMIN))).toEqual([
      "Read again",
      "Rename",
      "Move to folder",
      "Tags",
      "Copy path", // changed on purpose (#730)
      "Copy link",
      "Give access",
      "Who can see this",
      "Move to Trash",
    ]);
  });

  it("offers the Head of its department Who can see this only, and anyone else neither", () => {
    expect(words(menuEntries(doc(), false, "list", false, HEAD))).toContain("Who can see this");
    expect(words(menuEntries(doc(), false, "list", false, HEAD))).not.toContain("Give access");
    const member = words(menuEntries(doc(), false, "list", false, NONE));
    expect(member).not.toContain("Give access");
    expect(member).not.toContain("Who can see this");
    expect(words(menuEntries(doc(), false))).toEqual(member); // no abilities given: none
  });

  it("offers neither for a file in Trash (the server says in_trash)", () => {
    const trashed = words(menuEntries(doc({ deleted_at: "2026-11-10T10:00:00Z" }), true, "list", false, ADMIN));
    expect(trashed).toEqual(["Restore", "Delete forever"]);
  });

  it("says what each does in one plain line", () => {
    expect(ACTION_HELP.give).toBe("Let named people open this file, with every version.");
    expect(ACTION_HELP.audience).toBe("See who can open this file, and why.");
  });
});

describe("Give access", () => {
  it("is checked on the client first: at most 100 files and 20 people", () => {
    expect(giveProblem(100, 20)).toBeNull();
    expect(giveProblem(101, 1)).toBe("Choose 100 files or fewer.");
    expect(giveProblem(1, 21)).toBe("Choose 20 people or fewer.");
  });

  const form = (over: Partial<Parameters<typeof GiveAccessForm>[0]> = {}) =>
    renderToStaticMarkup(
      createElement(GiveAccessForm, {
        files: 1,
        chosen: [],
        onChoose: nothing,
        problem: null,
        error: null,
        busy: false,
        onCancel: nothing,
        onGive: nothing,
        ...over,
      }),
    );

  it("says what giving does, asks for people, and puts Cancel first, focused", () => {
    const html = form();
    expect(html).toContain("They can open it, and Ask, search and totals can use it for them. Every version is given, now and later.");
    expect(html).toContain("<legend>People</legend>");
    expect(html.indexOf(">Cancel<")).toBeLessThan(html.indexOf("Give access</button>"));
    expect(html).toMatch(/<button[^>]*data-autofocus="true"[^>]*>Cancel/);
    expect(html).toMatch(/disabled=""[^>]*>Give access<\/button>/); // nobody chosen yet
    expect(form({ chosen: [{ id: "u1", name: "Priya" }] })).not.toMatch(/disabled=""[^>]*>Give access<\/button>/);
  });

  it("says what stops it, and can't be sent then", () => {
    const html = form({ chosen: [{ id: "u1", name: "Priya" }], problem: "Choose 20 people or fewer." });
    expect(html).toContain("Choose 20 people or fewer.");
    expect(html).toMatch(/disabled=""[^>]*>Give access<\/button>/);
  });

  it("says them for many files", () => {
    expect(form({ files: 8 })).toContain("They can open them, and Ask, search and totals can use these files for them.");
  });

  it("names the files in its title and the toast", () => {
    const one = [{ id: "d1", display_name: "Sale deed.pdf" }];
    const eight = Array.from({ length: 8 }, (_, i) => ({ id: `d${i}`, display_name: `File ${i}` }));
    expect(giveTitle(one)).toBe("Give access to “Sale deed.pdf”");
    expect(giveTitle(eight)).toBe("Give access to 8 files");
    expect(sharedToast(8, [{ id: "u1", name: "Priya" }])).toBe("8 files shared with Priya.");
    expect(sharedToast(1, [1, 2, 3].map((n) => ({ id: `u${n}`, name: `P${n}` })))).toBe("Shared with 3 people.");
  });
});
