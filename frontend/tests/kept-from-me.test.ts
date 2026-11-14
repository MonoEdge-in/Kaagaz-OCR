// PRD 7 PR 11b (#604): "5 files here are kept from you." An admin session's alone: never asked for
// anyone else; nothing while it loads, on any failure or for 0 (never a "0"); Show names lists
// Name, Kind, Uploaded by and Date, each with Give access, Who can see this and Give myself access.
// The screen is rendered with react-dom in the small fake DOM (tests/fake-dom.ts), against a fake
// fetch.

import { act, createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type { Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { KeptFromMe, KeptNamesView, KeptStripView } from "@/components/access/KeptFromMe";
import { keptStripText } from "@/components/access/words";
import type { KeptFromMe as Kept, Me, Role } from "@/lib/types";
import { FakeDocument, FakeElement, install } from "./fake-dom";

const KEPT: Kept = {
  count: 5,
  items: [
    { id: "d1", name: "Salary sheet.xlsx", kind: "xlsx", uploader: "Priya", created_at: "2026-11-03T10:00:00Z", version_no: 1, locked: false },
    { id: "d2", name: "Bonus.pdf", kind: "pdf", uploader: null, created_at: "2026-11-04T10:00:00Z", version_no: 2, locked: false },
  ],
};
const person = (role: Role, scope: "app" | "admin"): Me => ({
  user: { id: "u1", role } as Me["user"],
  customer: { id: "c1", name: "Sharma Traders" },
  session: { scope },
  providers: [],
  access: { management: role === "member", heads: ["fin"] },
});
const nothing = () => undefined;

describe("the strip's words", () => {
  it("say how many, and nothing at all for 0", () => {
    expect(keptStripText(5)).toBe("5 files here are kept from you.");
    expect(keptStripText(1)).toBe("1 file here is kept from you.");
    expect(keptStripText(0)).toBeNull();
    expect(renderToStaticMarkup(createElement(KeptStripView, { count: 0, onShow: nothing }))).toBe("");
    expect(renderToStaticMarkup(createElement(KeptStripView, { count: 5, onShow: nothing }))).toContain(">Show names</button>");
  });
});

describe("the names list", () => {
  const html = renderToStaticMarkup(createElement(KeptNamesView, { kept: KEPT, error: null, onGive: nothing, onAudience: nothing, onMyself: nothing }));

  it("says why the names show, how many are listed, and each one's name, kind, uploader and date", () => {
    expect(html).toContain("You can see their names because you&#x27;re an admin. You can&#x27;t open them.");
    expect(html).toContain("Showing the first 2 of 5, by name.");
    for (const heading of ["Name", "Kind", "Uploaded by", "Date"]) expect(html).toContain(`<th scope="col">${heading}</th>`);
    expect(html).toContain("<td>Salary sheet.xlsx</td>");
    expect(html).toContain("<td>Priya</td>");
    expect(html).toContain("<td>—</td>"); // an uploader no longer known
    expect(html).not.toContain("href="); // a name, never a link to a file they can't open
  });

  it("gives each one Give access, Who can see this and Give myself access", () => {
    const menu = html.slice(html.indexOf("Salary sheet.xlsx</td>"), html.indexOf("Bonus.pdf</td>"));
    expect(menu).toContain(">Give access<");
    expect(menu).toContain(">Who can see this<");
    expect(menu).toContain(">Give myself access<");
    expect(menu).toContain("Open this file yourself. Asks for your password and tells Management.");
  });
});

let doc: FakeDocument;
let root: Root | null = null;
let asked: string[] = [];

/** fetch answers GET /api/folders/sal/kept-from-me with `reply` (a status number: that error). */
function serve(reply: Kept | number) {
  asked = [];
  vi.stubGlobal("fetch", async (url: string) => {
    asked.push(url);
    if (typeof reply === "number") {
      return new Response(JSON.stringify({ error: { code: "x", message: "x" } }), { status: reply, headers: { "Content-Type": "application/json" } });
    }
    return new Response(JSON.stringify(reply), { status: 200, headers: { "Content-Type": "application/json" } });
  });
}

async function show(me: Me, reply: Kept | number) {
  serve(reply);
  const container = doc.body.appendChild(doc.createElement("div")) as FakeElement;
  const { createRoot } = await import("react-dom/client");
  root = createRoot(container as unknown as Element);
  await act(async () => root!.render(createElement(KeptFromMe, { folder: { id: "sal", name: "Salaries" }, me, onChanged: nothing })));
  await act(async () => void (await new Promise((resolve) => setTimeout(resolve, 0))));
  return container.textContent;
}

beforeEach(() => {
  doc = install();
});

afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  vi.unstubAllGlobals();
});

describe("the strip in a folder", () => {
  it("shows an admin session the count, with Show names", async () => {
    const text = await show(person("admin", "admin"), KEPT);
    expect(asked).toEqual(["/api/folders/sal/kept-from-me"]);
    expect(text).toContain("5 files here are kept from you.");
    expect(text).toContain("Show names");
  });

  it("shows nothing for 0, and nothing on any failure", async () => {
    expect(await show(person("admin", "admin"), { count: 0, items: [] })).toBe("");
    for (const status of [403, 404, 500]) {
      await act(async () => root?.unmount());
      expect(await show(person("admin", "admin"), status), String(status)).toBe("");
    }
  });

  it("never asks for anyone else: Management, a Head, a member, an admin with an app session", async () => {
    for (const me of [person("member", "app"), person("reviewer", "app"), person("admin", "app")]) {
      await act(async () => root?.unmount());
      expect(await show(me, KEPT)).toBe("");
      expect(asked).toEqual([]);
    }
  });
});
