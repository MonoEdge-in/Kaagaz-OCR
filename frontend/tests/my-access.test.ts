// #707: My access (/my-access), what the signed-in person can see and why, from GET /me/access. The
// Access page's sections, read only: each case's plain words, the "kept" line, links to the files,
// and none of the admin page's buttons.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MyAccessView } from "@/components/access/MyAccess";
import * as api from "@/lib/api";
import type { MyAccess } from "@/lib/types";

const NONE: MyAccess = { management: false, departments: [], private_folders: [], files: [], walled: false };

const FULL: MyAccess = {
  management: false,
  departments: [
    { id: "d1", name: "Finance", head: true, department_only: true },
    { id: "d2", name: "Sales", head: false, department_only: false },
  ],
  private_folders: [{ id: "f1", name: "Salaries", path: "Finance / Salaries" }],
  files: [
    { id: "doc 1", name: "Sale deed.pdf", path: "Sales / 2026", given_at: "2026-11-03T10:00:00Z", given_by: "Ravi" },
    { id: "doc2", name: "Auto.pdf", path: null, given_at: "2026-11-04T10:00:00Z", given_by: null },
  ],
  walled: true,
};

const render = (data: MyAccess | null, failed: string | null = null) =>
  renderToStaticMarkup(createElement(MyAccessView, { data, failed, onRetry: () => undefined }));

describe("My access", () => {
  it("says what the page is, then the sections in order: Departments, Private folders, Files given", () => {
    const html = render(FULL);
    expect(html).toContain("<h1>My access</h1>");
    expect(html).toContain("What you can see in Kaagaz, and why.");
    const order = [">Departments<", ">Private folders<", ">Files given to you<"].map((heading) => html.indexOf(heading));
    expect(order.every((at) => at > 0)).toBe(true);
    expect([...order].sort((a, b) => a - b)).toEqual(order);
  });

  it("says why each department, private folder and file applies", () => {
    const html = render(FULL);
    expect(html).toContain("Finance is Department only: you see its files because you&#x27;re in it.");
    expect(html).toContain("You&#x27;re its Head: you can see who has access to its files.");
    expect(html).toContain("You&#x27;re in Sales.");
    expect(html.match(/its Head/g)).toHaveLength(1); // Sales: a member only
    expect(html).toContain("Finance / Salaries");
    expect(html).toContain("You&#x27;re named on this private folder.");
    expect(html).toMatch(/Given to you by Ravi, \d{1,2} Nov 2026\./);
    expect(html).toMatch(/Given to you on \d{1,2} Nov 2026\./); // Kaagaz gave it (auto-filing)
    expect(html).toContain("Sales / 2026");
  });

  it("links each file to the file", () => {
    const html = render(FULL);
    expect(html).toContain('href="/documents/doc%201"');
    expect(html).toContain('href="/documents/doc2"');
    expect(html).toMatch(/<a [^>]*href="\/documents\/doc%201"[^>]*>Sale deed.pdf<\/a>/);
  });

  it("has none of the admin page's buttons", () => {
    const html = render(FULL);
    expect(html).not.toMatch(/Give access|Remove|Management<\/button>|type="checkbox"/);
    expect(html).not.toContain("<button");
  });

  it("says Management sees every file, and then no kept line", () => {
    const html = render({ ...FULL, management: true });
    expect(html).toContain("You see every file (Management).");
    expect(html).not.toContain("Some files are kept to other people");
  });

  it("says some files are kept to other people only when the organisation keeps some", () => {
    const kept = "Some files are kept to other people. Ask an admin if you need one.";
    expect(render(FULL)).toContain(kept);
    expect(render({ ...FULL, walled: false })).not.toContain(kept);
    expect(render(FULL)).not.toContain("You see every file");
  });

  it("says each empty list plainly", () => {
    const html = render(NONE);
    expect(html).toContain("No departments yet.");
    expect(html).toContain("You aren&#x27;t named on any private folder.");
    expect(html).toContain("No files have been given to you.");
  });

  it("says it is loading, and a failed load with Try again", () => {
    expect(render(null)).toContain("Loading your access…");
    const html = render(null, "Kaagaz had a problem. Try again in a minute.");
    expect(html).toContain("Could not load your access.");
    expect(html).toContain("Try again");
    expect(html).toContain("What you can see in Kaagaz, and why.");
  });

  afterEach(() => vi.unstubAllGlobals());

  it("reads GET /me/access", async () => {
    const calls: string[] = [];
    vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
      calls.push(`${init.method} ${url}`);
      return new Response(JSON.stringify(NONE), { status: 200 });
    });
    expect(await api.getMyAccess()).toEqual(NONE);
    expect(calls).toEqual(["GET /api/me/access"]);
  });

  it("is a page of its own with a plain tab title", () => {
    const route = readFileSync(join(__dirname, "..", "app/(app)/my-access/page.tsx"), "utf8");
    expect(route).not.toMatch(/^"use client"/);
    expect(route).toContain('export const metadata = { title: "My access" };');
  });
});
