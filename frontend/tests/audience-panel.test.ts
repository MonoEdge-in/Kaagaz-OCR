// PRD 7 PR 11b (#604): "Who can see this, and why". The groups in the server's order with their
// headings, how many people can't see it, Check a person's answers, the 403 and 404 texts; Make
// private for an Admin only, refused with the reason in place of acting; the named list editable
// only when the server says can_edit; Make private and Make open again wait for their preview.

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { AccessAbilities } from "@/components/access/abilities";
import { AudienceView, PlaceChangeView, audienceFailure } from "@/components/access/AudiencePanel";
import type { FolderPlace } from "@/components/access/places";
import { checkAnswer, everyoneElseText, openAgainText, privateText, reasonHeading } from "@/components/access/words";
import { ApiError } from "@/lib/api";
import type { Audience, NamedList, PlacePreview } from "@/lib/types";

const person = (id: string, name: string) => ({ id, name });
const PRIYA = person("u1", "Priya");
const AMIT = person("u2", "Amit");
const people = (n: number) => Array.from({ length: n }, (_, i) => person(`p${i}`, `Person ${i}`));
const nothing = () => undefined;
const ADMIN: AccessAbilities = { audience: true, makePrivate: true, editNamed: true, giveFile: true, keptStrip: true };
const HEAD: AccessAbilities = { audience: true, makePrivate: false, editNamed: true, giveFile: false, keptStrip: false };
const FINANCE = { id: "fin", name: "Finance" };
const SALARIES = { id: "sal", name: "Salaries" };

const AUDIENCE: Audience = {
  groups: [
    { reason: "management", people: [person("m1", "Meera")] },
    { reason: "head", place: FINANCE, people: [PRIYA] },
    { reason: "member", place: FINANCE, people: [PRIYA, AMIT] },
    { reason: "named", place: SALARIES, people: [person("u3", "Ravi")] },
    { reason: "given", people: [person("u4", "Sana")] },
    { reason: "held", people: [person("u5", "Om")] },
    { reason: "may_open_locked", people: [person("u6", "Lock")] }, // PR 12's, not shown yet
  ],
  everyone_else: 12,
};

const view = (over: Partial<Parameters<typeof AudienceView>[0]> = {}) =>
  renderToStaticMarkup(
    createElement(AudienceView, {
      target: { kind: "folder", id: "sal", name: "Salaries" },
      myId: "me",
      abilities: ADMIN,
      place: { isPrivate: false, department: FINANCE, refusal: null },
      audience: AUDIENCE,
      failed: null,
      named: null,
      onClose: nothing,
      onChanged: nothing,
      onRetry: nothing,
      onMode: nothing,
      ...over,
    }),
  );

describe("who can see it, and why", () => {
  it("lists the groups in the server's order, each under its heading, then how many can't", () => {
    const html = view();
    const headings = [
      "Management (sees all files)",
      "Head of Finance",
      "In Finance",
      "Named on “Salaries”",
      "Given this file directly",
      "Uploaded it, not filed yet",
      "Everyone else (12 people) can&#x27;t see it.",
    ];
    const at = headings.map((words) => html.indexOf(words));
    expect(at.every((i) => i >= 0)).toBe(true);
    expect([...at].sort((a, b) => a - b)).toEqual(at);
    expect(html).not.toContain("Lock"); // a reason this screen doesn't know yet is left out
    expect(html).toContain("Check a person");
  });

  it("words every reason, and everyone else for 0, 1 and many", () => {
    expect(reasonHeading("everyone", null)).toBe("Everyone (this place is open)");
    expect(reasonHeading("head", null)).toBe("Head of its department");
    expect(reasonHeading("may_open_locked", null)).toBeNull();
    expect(everyoneElseText(0)).toBe("Everyone in the organisation can see it.");
    expect(everyoneElseText(1)).toBe("Everyone else (1 person) can't see it.");
  });

  it("answers Check a person with yes and why, or no", () => {
    const places = { member: "Finance", head: "Finance", named: "Salaries" };
    expect(checkAnswer("Priya", true, ["member"], places)).toBe("Yes. Priya can see this, as a member of Finance.");
    expect(checkAnswer("Priya", true, ["head", "member"], places)).toBe("Yes. Priya can see this, as head of Finance.");
    expect(checkAnswer("Ravi", true, ["named"], places)).toBe("Yes. Ravi can see this, as someone named on “Salaries”.");
    expect(checkAnswer("Meera", true, ["management"], {})).toBe("Yes. Meera can see this, as Management.");
    expect(checkAnswer("Om", true, ["everyone"], {})).toBe("Yes. Om can see this, because this place is open to everyone.");
    expect(checkAnswer("Amit", false, [], places)).toBe("No. Amit can't see this.");
  });

  it("says the loading words while it loads", () => {
    expect(view({ audience: null })).toContain("Loading who can see this…");
  });
});

describe("when it can't be read", () => {
  it("says one not-found text for a missing or hidden place, and the 403 text, with no Try again", () => {
    const missing = audienceFailure(new ApiError(404, "not_found", "x"), "folder");
    expect(missing).toEqual({
      text: "We couldn't find this folder. It may have been moved or deleted, or you may not have access to it.",
      retry: false,
    });
    expect(audienceFailure(new ApiError(404, "not_found", "x"), "document").text).toMatch(/^We couldn't find this document\. /);
    expect(audienceFailure(new ApiError(403, "forbidden", "x"), "folder")).toEqual({
      text: "Only admins, and the head of this department, can see who has access.",
      retry: false,
    });
    const forbidden = view({ failed: new ApiError(403, "forbidden", "x") });
    expect(forbidden).toContain("Only admins, and the head of this department, can see who has access.");
    expect(forbidden).not.toContain("Try again");
    expect(forbidden).not.toContain("Priya"); // nothing of a list it couldn't read
  });

  it("offers Try again for anything else", () => {
    expect(audienceFailure(new TypeError("Failed to fetch"), "folder").retry).toBe(true);
    expect(view({ failed: new ApiError(503, "busy", "x") })).toContain("Try again");
  });
});

describe("Make private and Make open again", () => {
  const place = (over: Partial<FolderPlace>): FolderPlace => ({ isPrivate: false, department: FINANCE, refusal: null, ...over });

  it("are an Admin's: Make private on an open folder, Make open again on a private one", () => {
    expect(view()).toContain(">Make private</button>");
    expect(view({ place: place({ isPrivate: true }) })).toContain(">Make open again</button>");
    expect(view({ place: place({ isPrivate: true }) })).not.toContain(">Make private<");
  });

  it("are not a Head's, nor offered for a file", () => {
    expect(view({ abilities: HEAD })).not.toMatch(/Make (private|open again)/);
    expect(view({ target: { kind: "document", id: "d1", name: "Sale deed.pdf" }, place: null })).not.toContain("Make private");
  });

  it("stays when refused, does nothing, and says why in place of acting", () => {
    const refused = (refusal: FolderPlace["refusal"]) => view({ place: place({ refusal }) });
    expect(refused("department")).toContain('aria-disabled="true"');
    expect(refused("department")).toContain("A department can&#x27;t be private. Use Department only on the Departments tab instead.");
    expect(refused("inside_private")).toContain("This folder is inside a private folder, so it&#x27;s private already.");
    expect(refused("holds_private")).toContain("A folder inside this one is private. Make that folder open again first.");
    expect(view()).not.toContain("aria-disabled");
  });

  it("waits for the preview, then says who loses access, with Cancel first", () => {
    const form = (preview: PlacePreview | null, on = true) =>
      renderToStaticMarkup(
        createElement(PlaceChangeView, {
          on,
          department: "Finance",
          chosen: [],
          onChoose: nothing,
          preview,
          failed: false,
          busy: false,
          error: null,
          onRetry: nothing,
          onCancel: nothing,
          onConfirm: nothing,
        }),
      );
    const loading = form(null);
    expect(loading).toContain("Checking who would lose access…");
    expect(loading).toMatch(/<button[^>]*disabled=""[^>]*>Make private<\/button>/);
    const ready = form({ losing: people(9), gaining: [], files: 42 });
    expect(ready).toContain("Only people you name, the Finance head and Management will see it. 9 people will lose access: Person 0, Person 1 and 7 more.");
    expect(ready).toContain("Show all 9");
    expect(ready).toContain("Name people who can still see it");
    expect(ready.indexOf(">Cancel<")).toBeLessThan(ready.indexOf(">Make private<"));
    expect(ready).not.toMatch(/disabled=""[^>]*>Make private/);
    expect(form({ losing: [], gaining: [PRIYA], files: 42 }, false)).toContain("Everyone who can see Finance will see these 42 files.");
  });

  it("words the change with and without a department, and with nobody losing", () => {
    expect(privateText(null, [PRIYA, AMIT])).toBe("Only people you name and Management will see it. 2 people will lose access: Priya and Amit.");
    expect(privateText("Finance", [])).toBe("Only people you name, the Finance head and Management will see it. Nobody loses access.");
    expect(openAgainText(null, 1)).toBe("Everyone in the organisation will see this file.");
    expect(openAgainText(null, 0)).toBe("Everyone in the organisation will see these 0 files.");
  });
});

describe("the named list", () => {
  const named = (can_edit: boolean): NamedList => ({
    folder: { id: "sal", name: "Salaries", path: "Finance / Salaries" },
    named: [PRIYA],
    heads: [],
    can_edit,
  });

  it("can be changed only when the server says can_edit", () => {
    const editable = view({ named: named(true) });
    expect(editable).toContain("Named on this folder");
    expect(editable).toContain("Remove<span class=\"visually-hidden\"> Priya</span>");
    expect(editable).toContain(">Add people</button>");
    const readOnly = view({ named: named(false) });
    expect(readOnly).toContain("Priya");
    expect(readOnly).not.toContain(">Remove");
    expect(readOnly).not.toContain(">Add people<");
  });

  it("says when nobody is named", () => {
    expect(view({ named: { ...named(true), named: [] } })).toContain("No one is named on this folder.");
  });
});
