// PRD 7 PR 11c (#604): Management's Access activity page and the Admin's Activity tab, rendered with
// react-dom in the small fake DOM (tests/fake-dom.ts) and pressed. The page: loading, the entries,
// a file Management can't see (the server's mask, shown as it comes: no link, no folder), nothing
// yet, the server's 403 words with Try again, and Show more by the cursor. The tab: the self-given
// entries first, and About a person asking for subject_id.

import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AccessActivity } from "@/components/activity/AccessActivity";
import { ActivityPanel } from "@/components/admin/ActivityPanel";
import { ApiError } from "@/lib/api";
import type { ActivityEntry, ActivityPage } from "@/lib/types";
import { fire, install } from "./fake-dom";
import type { FakeDocument, FakeElement } from "./fake-dom";

const api = vi.hoisted(() => ({
  listAccessActivity: vi.fn(),
  listAudit: vi.fn(),
  listPeople: vi.fn(),
  listFolders: vi.fn(),
}));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  ...api,
}));
// next/link prefetches with requestIdleCallback on `self`, which the fake DOM has not: a plain link.
vi.mock("next/link", async () => {
  const { createElement: make } = await import("react");
  return { default: ({ href, children }: { href: string; children: unknown }) => make("a", { href }, children as never) };
});

const entry = (id: number, action: string, more: Partial<ActivityEntry> = {}): ActivityEntry => ({
  id,
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
const given = entry(1, "access.file_give", {
  target_type: "document",
  target_id: "d1",
  document: { id: "d1", name: "Sale deed.pdf", version_no: 2, deleted: false },
  folder: { id: "inv", path: "Finance / Invoices" },
  details: { versions: 2 },
});
const masked = entry(2, "access.file_give", {
  target_type: "document",
  target_id: null,
  family_id: null,
  document: { id: null, name: "a file you can't see", version_no: null, deleted: false },
  folder: null,
  details: { self: true },
});
const page = (items: ActivityEntry[], next: string | null = null, more: Partial<ActivityPage> = {}): ActivityPage => ({
  items,
  next_cursor: next,
  ...more,
});

let doc: FakeDocument;
let root: Root | null = null;

async function mount(element: ReturnType<typeof createElement>) {
  const container = doc.body.appendChild(doc.createElement("div")) as FakeElement;
  const { createRoot } = await import("react-dom/client");
  root = createRoot(container as unknown as Element);
  await act(async () => root!.render(element));
  await flush();
  return container;
}

/** Let the fakes' promises and the effects they cause run. */
const flush = () => act(async () => void (await new Promise((resolve) => setTimeout(resolve, 0))));
const button = (container: FakeElement, text: string) =>
  container.all().find((element) => element.tagName === "BUTTON" && element.textContent.trim() === text);
const tagged = (container: FakeElement, tag: string) => container.all().filter((element) => element.tagName === tag);
const press = async (element: FakeElement | null | undefined) => {
  expect(element).toBeTruthy();
  await act(async () => void fire(element!, "click"));
  await flush();
};

beforeEach(() => {
  doc = install();
  vi.stubGlobal("navigator", { onLine: true, userAgent: "node" });
});
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("Management's Access activity page", () => {
  it("says it is loading until the first page comes", async () => {
    api.listAccessActivity.mockReturnValue(new Promise(() => undefined));
    const container = await mount(createElement(AccessActivity));
    expect(tagged(container, "H1").map((heading) => heading.textContent)).toEqual(["Access activity"]);
    expect(container.textContent).toContain("Changes to who can see which files, newest first.");
    expect(container.querySelector("[role=status]")?.textContent).toBe("Loading the activity…");
    expect(api.listAccessActivity).toHaveBeenCalledWith();
  });

  it("lists the changes, an Admin's with the file and its folder", async () => {
    api.listAccessActivity.mockResolvedValue(page([given, entry(3, "access.member_add")]));
    const container = await mount(createElement(AccessActivity));
    const rows = container.querySelectorAll("li").map((row) => row.textContent);
    expect(rows[0]).toContain("Ravi gave Priya access to Sale deed.pdf");
    expect(rows[0]).toContain("Finance / Invoices · ");
    expect(rows[1]).toContain("Ravi added Priya to Finance");
    expect(container.querySelector("ol")?.getAttribute("aria-label")).toBe("Access changes, newest first");
    expect(button(container, "Show more")).toBeUndefined(); // the last page
    expect(container.textContent).not.toMatch(/export/i);
  });

  it("shows a file Management can't see as the server's words, with no link and no folder", async () => {
    api.listAccessActivity.mockResolvedValue(page([masked]));
    const container = await mount(createElement(AccessActivity));
    const row = container.querySelector("li")!;
    expect(row.textContent).toContain("Ravi gave Priya access to a file you can't see");
    expect(row.textContent).toContain("Admin gave themselves access"); // the mask keeps `self`
    expect(container.querySelector("a")).toBeNull();
    expect(row.querySelector("time")?.parentNode?.textContent).not.toContain(" · ");
  });

  it("says when nobody has changed who can see what", async () => {
    api.listAccessActivity.mockResolvedValue(page([]));
    const container = await mount(createElement(AccessActivity));
    expect(container.textContent).toContain("Nobody has changed who can see what yet.");
    expect(container.querySelector("ol")).toBeNull();
  });

  it("says the server's words when refused, and Try again asks again", async () => {
    api.listAccessActivity.mockRejectedValueOnce(
      new ApiError(403, "forbidden", "Access activity is for Management and admins."),
    );
    api.listAccessActivity.mockResolvedValueOnce(page([given]));
    const container = await mount(createElement(AccessActivity));
    const alert = container.querySelector("[role=alert]")!;
    expect(alert.textContent).toContain("Access activity is for Management and admins.");
    await press(button(container, "Try again"));
    expect(api.listAccessActivity).toHaveBeenCalledTimes(2);
    expect(container.textContent).toContain("Ravi gave Priya access to Sale deed.pdf");
  });

  it("asks for the next page by the reply's cursor, and keeps what it has", async () => {
    api.listAccessActivity.mockResolvedValueOnce(page([given], "1762164000000000-1"));
    api.listAccessActivity.mockResolvedValueOnce(page([masked]));
    const container = await mount(createElement(AccessActivity));
    await press(button(container, "Show more"));
    expect(api.listAccessActivity).toHaveBeenLastCalledWith("1762164000000000-1");
    expect(container.querySelectorAll("li")).toHaveLength(2);
    expect(button(container, "Show more")).toBeUndefined();
  });

  it("keeps the first page and says so when the next one fails", async () => {
    api.listAccessActivity.mockResolvedValueOnce(page([given], "c1"));
    api.listAccessActivity.mockRejectedValueOnce(new ApiError(503, "unavailable", "Kaagaz is busy. Try again in a minute."));
    const container = await mount(createElement(AccessActivity));
    await press(button(container, "Show more"));
    expect(container.querySelectorAll("li")).toHaveLength(1);
    expect(container.querySelector("[role=alert]")?.textContent).toBe("Kaagaz is busy. Try again in a minute.");
  });
});

describe("the Admin's Activity tab", () => {
  const mine = entry(5, "access.management_on", {
    target_type: "user",
    target_id: "ravi",
    subject_id: "ravi",
    subject_name: "Ravi",
    folder: null,
    details: { self: true },
  });

  beforeEach(() => {
    api.listPeople.mockResolvedValue({ items: [{ id: "priya", name: "Priya" }] });
    api.listFolders.mockResolvedValue({ items: [] });
  });

  it("shows the entries in which an Admin gave themselves access first, then all the activity", async () => {
    api.listAudit.mockResolvedValue(page([given, mine], null, { total: 2, self_given: [mine] }));
    const container = await mount(createElement(ActivityPanel));
    const headings = tagged(container, "H3").map((heading) => heading.textContent);
    expect(headings).toEqual(["Admin gave themselves access", "All activity"]);
    const tables = container.querySelectorAll("table");
    expect(tables).toHaveLength(2);
    expect(tables[0]!.textContent).toContain("made themselves Management");
    expect(tables[0]!.textContent).not.toContain("Sale deed.pdf");
    expect(tables[1]!.textContent).toContain("gave Priya access to Sale deed.pdf");
  });

  it("has no self-given block when there is none", async () => {
    api.listAudit.mockResolvedValue(page([given], null, { total: 1, self_given: [] }));
    const container = await mount(createElement(ActivityPanel));
    expect(tagged(container, "H3")).toHaveLength(0);
    expect(container.querySelectorAll("table")).toHaveLength(1);
  });

  it("asks for the entries about the person chosen in About a person", async () => {
    api.listAudit.mockResolvedValue(page([given], null, { total: 1 }));
    const container = await mount(createElement(ActivityPanel));
    await press(button(container, "About a person"));
    const priya = container
      .querySelectorAll("label")
      .find((label) => label.textContent === "Priya")
      ?.querySelector("input");
    await press(priya);
    expect(api.listAudit).toHaveBeenLastCalledWith(expect.objectContaining({ subject_id: "priya", limit: 50 }));
    expect(button(container, "About: Priya")).toBeDefined();
  });
});
