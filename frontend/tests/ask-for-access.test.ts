// PRD 7 PR 11c (#604, lead decision 7 and PR 10c's note): "No files here yet. Ask your admin for
// access." when All documents is empty for someone the walls keep files from, never on day one.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { ASK_FOR_ACCESS, asksForAccess } from "@/components/access/askForAccess";
import type { Me, Role } from "@/lib/types";

const read = (path: string) => readFileSync(join(__dirname, "..", path), "utf8");

describe("No files here yet. Ask your admin for access.", () => {
  const me = (role: Role, access?: Me["access"]) => ({ user: { id: "u1", role }, access }) as Me;
  const walled = { management: false, heads: [], in_department: false, walled: true };

  it("is for someone in no department, once the organisation keeps something to some people", () => {
    expect(asksForAccess(me("member", walled))).toBe(true);
    expect(asksForAccess(me("reviewer", walled))).toBe(true);
    // Day one: nobody is in a department yet, and everyone sees everything (PR 10c's note).
    expect(asksForAccess(me("member", { ...walled, walled: false }))).toBe(false);
    expect(asksForAccess(me("member", { ...walled, in_department: true }))).toBe(false);
    expect(asksForAccess(me("member", { ...walled, management: true }))).toBe(false);
    expect(asksForAccess(me("admin", walled))).toBe(false);
    expect(asksForAccess(me("member"))).toBe(false); // an older server
    expect(asksForAccess(me("member", { management: false, heads: [] }))).toBe(false);
    expect(ASK_FOR_ACCESS).toEqual({ title: "No files here yet", text: "Ask your admin for access." });
  });

  it("shows only on All documents, keeping Upload", () => {
    const view = read("components/documents/DocumentsView.tsx");
    expect(view).toContain(
      "const askAccess = library.folderId === null && library.departmentId === null && asksForAccess(me);",
    );
    expect(view).toContain('title={current ? "No documents in this folder yet" : askAccess ? ASK_FOR_ACCESS.title : "No documents yet"}');
    const state = view.slice(view.indexOf("askAccess ? ASK_FOR_ACCESS.title"), view.indexOf("ASK_FOR_ACCESS.text"));
    expect(state).toContain("{uploadButton}");
  });
});
