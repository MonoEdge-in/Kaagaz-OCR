// PRD 7 PR 11a (#604): the people picker with its cap, and the Admin tab deep links (?tab=).

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { PeopleList, pickable } from "@/components/access/PeoplePicker";
import { tabFrom } from "@/components/admin/logic";

const PEOPLE = [
  { id: "u1", name: "Priya Shah" },
  { id: "u2", name: "Amit Rao" },
  { id: "u3", name: "Ravi Kumar" },
];

describe("the people picker", () => {
  it("finds by any part of the name, leaving out who is already there", () => {
    expect(pickable(PEOPLE, " RA ").map((p) => p.id)).toEqual(["u2", "u3"]);
    expect(pickable(PEOPLE, "", ["u1"]).map((p) => p.id)).toEqual(["u2", "u3"]);
  });

  const list = (chosen: typeof PEOPLE, people: typeof PEOPLE | null = PEOPLE) =>
    renderToStaticMarkup(createElement(PeopleList, { legend: "People", people, chosen, onChange: () => undefined, cap: 2 }));

  it("stops at the cap: the other boxes can't be ticked, and a line says so", () => {
    const html = list(PEOPLE.slice(0, 2));
    expect(html).toContain("Choose 2 people or fewer.");
    expect(html.match(/disabled=""/g)).toHaveLength(1);
    expect(list(PEOPLE.slice(0, 1))).not.toContain("or fewer");
  });

  it("says it is loading until the people come", () => {
    expect(list([], null)).toContain("Loading people…");
  });
});

describe("/admin?tab=", () => {
  it("opens a known tab, else Users", () => {
    expect(tabFrom("departments")).toBe("departments");
    expect(tabFrom("users")).toBe("users");
    expect(tabFrom("nonsense")).toBe("users");
    expect(tabFrom(undefined)).toBe("users");
  });

  it("is read by the server page and given to AdminView", () => {
    const page = readFileSync(join(__dirname, "..", "app/(app)/admin/page.tsx"), "utf8");
    expect(page).toContain("const { tab } = await searchParams;");
    expect(page).toContain('tab={typeof tab === "string" ? tab : undefined}');
  });
});
