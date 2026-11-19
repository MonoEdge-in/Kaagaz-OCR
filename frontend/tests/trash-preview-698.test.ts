// #698: a document in Trash shows no viewer (the server refuses its file and page pictures, 409
// in_trash, File management T1): where the page would be, "This file is in Trash, so it can't be
// previewed." / "Restore it to see it." and, for who may restore it (can_delete, as Trash decides),
// Restore; else the Open Trash link. Restore is the existing call; the screen then loads the detail
// again and says it. Rendered with react-dom in the small fake DOM (tests/fake-dom.ts) against a
// fake fetch; the document screen's own choice is read from its source.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { DocumentDetail } from "@/lib/types";
import { TrashPreview } from "../app/(app)/documents/[id]/TrashPreview";
import { FakeDocument, FakeElement, fire, install } from "./fake-dom";

// next/link prefetches with requestIdleCallback on `self`, which the fake DOM has not: a plain link.
vi.mock("next/link", async () => {
  const { createElement: make } = await import("react");
  return { default: ({ href, children }: { href: string; children: unknown }) => make("a", { href }, children as never) };
});

let doc: FakeDocument;
let root: Root | null = null;
let calls: string[] = [];

function serve(status: number, body: unknown) {
  calls = [];
  vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
    calls.push(`${init.method ?? "GET"} ${url}`);
    return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  });
}

const trashed = (canDelete: boolean) =>
  ({ id: "d1", display_name: "Sale deed.pdf", deleted_at: "2026-11-18T10:00:00Z", can_delete: canDelete }) as DocumentDetail;

let restored: string[] = [];
async function screen(detail: DocumentDetail) {
  restored = [];
  const container = doc.body.appendChild(doc.createElement("div")) as FakeElement;
  const { createRoot } = await import("react-dom/client");
  root = createRoot(container as unknown as Element);
  await act(async () => root!.render(createElement(TrashPreview, { detail, onRestored: (message: string) => restored.push(message) })));
}
const button = (words: string) => doc.body.all().find((each) => each.tagName === "BUTTON" && each.textContent.trim() === words) ?? null;

beforeEach(() => {
  doc = install();
  (window as unknown as { location: { href: string } }).location = { href: "http://localhost/documents/d1" };
});
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  vi.unstubAllGlobals();
});

describe("a document in Trash, where the page would be (#698)", () => {
  it("says why there is no preview and offers Restore; asks for nothing until it is pressed", async () => {
    serve(200, { id: "d1", display_name: "Sale deed.pdf", placed: null });
    await screen(trashed(true));
    expect(doc.body.textContent).toContain("This file is in Trash, so it can't be previewed.");
    expect(doc.body.textContent).toContain("Restore it to see it.");
    expect(doc.body.all().some((each) => each.getAttribute("role") === "alert")).toBe(false); // calm, not an error
    expect(calls).toEqual([]); // no file, no page picture

    await act(async () => void fire(button("Restore")!, "click", { button: 0 }));
    await act(async () => undefined);
    expect(calls).toEqual(["POST /api/documents/d1/restore"]);
    expect(restored).toEqual(["Restored Sale deed.pdf."]);
  });

  it("offers no Restore to who may not restore it (can_delete false), only the way to Trash", async () => {
    serve(200, {});
    await screen(trashed(false));
    expect(doc.body.textContent).toContain("This file is in Trash, so it can't be previewed.");
    expect(button("Restore")).toBeNull();
    const link = doc.body.all().find((each) => each.tagName === "A");
    expect([link?.getAttribute("href"), link?.textContent]).toEqual(["/documents?trash=1", "Open Trash"]);
  });

  it("says a refused Restore in the server's words, and the screen is not reloaded", async () => {
    serve(403, { error: { code: "forbidden", message: "Its folder is a department, which only an admin can restore." } });
    await screen(trashed(true));
    await act(async () => void fire(button("Restore")!, "click", { button: 0 }));
    await act(async () => undefined);
    const alert = doc.body.all().find((each) => each.getAttribute("role") === "alert");
    expect(alert?.textContent).toBe("Its folder is a department, which only an admin can restore.");
    expect(restored).toEqual([]);
  });
});

describe("the document screen for a document in Trash (#698)", () => {
  const page = readFileSync(join(__dirname, "../app/(app)/documents/[id]/DocumentView.tsx"), "utf8");

  it("shows TrashPreview instead of either viewer, so neither asks for the file or a page picture", () => {
    const viewer = page.slice(page.indexOf('aria-label="Page"'), page.indexOf("<SplitHandle"));
    const choice = viewer.indexOf("detail.deleted_at !== null ? (");
    expect(choice).toBeGreaterThan(-1);
    expect(viewer.indexOf("<TrashPreview", choice)).toBeGreaterThan(choice);
    expect(viewer.indexOf("<TrashPreview")).toBeLessThan(viewer.indexOf("<PdfDocumentViewer"));
    expect(viewer.indexOf("<TrashPreview")).toBeLessThan(viewer.indexOf("<PageViewer"));
    // After Restore the detail is asked for again and the toast says it (reloadWith), no page reload.
    expect(viewer).toMatch(/<TrashPreview detail=\{detail\} onRestored=\{reloadWith\} \/>/);
  });
});
