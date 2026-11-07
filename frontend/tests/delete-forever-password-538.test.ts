// #538 (Tushar, 7 Nov 2026): an admin without two-step sign-in deletes forever from Trash by
// typing their password again, as a member or reviewer an admin allowed does. Who sees Delete
// forever and the password box, and the list itself, rendered with react-dom in the small fake
// DOM (tests/fake-dom.ts) and pressed: the row's dialog and the selection's question ask for the
// password; a 403 password_confirm_required keeps the question open and asks again, and the
// retry deletes.

import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DocumentsTable } from "@/components/documents/DocumentsTable";
import { buildTree } from "@/components/documents/folderTree";
import { canDeleteForever, foreverNeeds, foreverNeedsPassword, passwordAgain } from "@/components/documents/trash";
import { ApiError } from "@/lib/api";
import type { DocumentSummary, Me, SessionScope } from "@/lib/types";
import { FakeDocument, FakeElement, fire, install } from "./fake-dom";

vi.mock("@/lib/session", () => ({ useSession: () => ({ refresh: () => undefined }) }));
// next/link prefetches with requestIdleCallback on `self`, which the fake DOM has not: a plain link.
vi.mock("next/link", async () => {
  const { createElement: make } = await import("react");
  return { default: ({ href, children }: { href: string; children: unknown }) => make("a", { href }, children as never) };
});

const admin = (scope: SessionScope, totp_enabled: boolean): Me => ({
  user: {
    id: "u1",
    email: "asha@example.in",
    display_name: "Asha",
    role: "admin",
    must_change_password: false,
    totp_enabled,
    two_step_turned_off: !totp_enabled,
    can_delete_forever: false,
    email_confirmed: true,
  },
  customer: { id: "c1", name: "Sharma Traders" },
  session: { scope },
  providers: [],
});

describe("who sees Delete forever", () => {
  it("is an admin session without two-step too, who types their password", () => {
    const off = admin("admin", false);
    expect([canDeleteForever(off), foreverNeedsPassword(off), foreverNeeds(off)]).toEqual([true, true, null]);
  });

  it("keeps an admin with two-step as before: no password", () => {
    const on = admin("admin", true);
    expect([canDeleteForever(on), foreverNeedsPassword(on), foreverNeeds(on)]).toEqual([true, false, null]);
  });

  it("still sends an admin's app session to sign in again", () => {
    const app = admin("app", false);
    expect([canDeleteForever(app), foreverNeedsPassword(app), foreverNeeds(app)]).toEqual([false, false, "admin_sign_in"]);
  });

  it("asks for the password again only on 403 password_confirm_required", () => {
    expect(passwordAgain(new ApiError(403, "password_confirm_required", "Type your password again to delete forever."))).toBe(true);
    expect(passwordAgain(new ApiError(403, "forbidden", "No."))).toBe(false);
    expect(passwordAgain(new Error("offline"))).toBe(false);
  });
});

// --- The list in Trash, rendered and pressed ---

const AGAIN = "Type your password again to delete forever.";
const binned = {
  id: "d1",
  display_name: "Bill.pdf",
  detected_type: "pdf",
  size_bytes: 1000,
  status: "ready",
  failure_code: null,
  status_message: "Ready.",
  page_count: 1,
  created_at: "2026-10-15T10:00:00Z",
  uploaded_by_name: "Priya",
  folder_id: null,
  tags: [],
  deleted_at: "2026-11-06T10:00:00Z",
  version_no: 1,
  family_id: "d1",
  can_delete: false,
  attempts: 1,
  max_attempts: 5,
  newer_version: null,
  progress: null,
  time_left: null,
  doc_type: null,
  languages: [],
  pages_unreadable: 0,
  pages_uncertain: 0,
  values_to_review: 0,
} as unknown as DocumentSummary;

let doc: FakeDocument;
let root: Root | null = null;
let calls: string[] = [];
let removed: string[] = [];

/** fetch answers each "METHOD /path" with the next of its replies (a status, and a body or none). */
function serve(routes: Record<string, [number, unknown?][]>) {
  calls = [];
  vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
    const call = `${init.method ?? "GET"} ${url}`;
    calls.push(call);
    const [status, body] = routes[call]?.shift() ?? [500, { error: { code: "error", message: `no route ${call}` } }];
    const headers = { "Content-Type": "application/json" };
    return new Response(body === undefined ? null : JSON.stringify(body), { status, headers });
  });
}

async function show(me: Me, items: DocumentSummary[] = [binned]) {
  removed = [];
  const container = doc.body.appendChild(doc.createElement("div")) as FakeElement;
  // Loaded once the fake DOM is in place: react-dom looks for a DOM as it loads (fake-dom.ts).
  const { createRoot } = await import("react-dom/client");
  root = createRoot(container as unknown as Element);
  const props = {
    items,
    inTrash: true,
    me,
    now: Date.parse("2026-11-07T10:00:00Z"),
    tree: buildTree([]),
    tags: [],
    suggestions: [],
    movedLeaves: false,
    sorting: { sort: "name", order: "asc", onSort: () => undefined },
    onRemoved: (message: string) => removed.push(message),
    onSaved: () => undefined,
    onFocusList: () => undefined,
    onTagCreated: () => undefined,
    onFolderCreated: () => undefined,
    onUploadVersion: () => undefined,
  } as unknown as Parameters<typeof DocumentsTable>[0];
  await act(async () => root!.render(createElement(DocumentsTable, props)));
  return container;
}

const all = () => doc.body.all();
const byLabel = (label: string) => all().find((element) => element.getAttribute("aria-label") === label) ?? null;
/** The button that reads `words`, or whose BusyLabel's idle words are `words`. */
const button = (words: string) =>
  all().find(
    (element) =>
      element.tagName === "BUTTON" &&
      (element.textContent.trim() === words || element.all().some((inner) => inner.tagName === "SPAN" && inner.textContent === words)),
  ) ?? null;
const passwordBox = (id: string) => all().find((element) => element.getAttribute("id") === id) ?? null;
const press = (element: FakeElement | null) => act(async () => void fire(element!, "click", { button: 0 }));
async function type(box: FakeElement | null, text: string) {
  box!.value = text;
  await act(async () => void fire(box!, "input"));
}
async function tick(box: FakeElement | null) {
  box!.checked = !box!.checked;
  await act(async () => void fire(box!, "click", { button: 0 }));
}

beforeEach(() => {
  doc = install();
});

afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  vi.unstubAllGlobals();
});

describe("an admin without two-step, in Trash", () => {
  it("is offered Delete forever on the row; the dialog asks for the password, and a 403 asks again", async () => {
    serve({
      "POST /api/auth/confirm-password": [[204], [204]],
      "DELETE /api/documents/d1/permanent": [[403, { error: { code: "password_confirm_required", message: AGAIN } }], [204]],
    });
    await show(admin("admin", false));
    await press(byLabel("More actions for Bill.pdf"));
    await press(byLabel("Delete forever"));
    const box = passwordBox("forever-password-d1");
    expect(box).not.toBeNull();
    expect(button("Delete forever")?.hasAttribute("disabled")).toBe(true); // nothing typed yet

    await type(box, "secret");
    await press(button("Delete forever"));
    // The token ran out between the two calls: the dialog stays, says why, and asks again.
    expect(calls).toEqual(["POST /api/auth/confirm-password", "DELETE /api/documents/d1/permanent"]);
    expect(removed).toEqual([]);
    const again = passwordBox("forever-password-d1");
    expect(again?.value).toBe("");
    expect(doc.activeElement).toBe(again); // focus waits in the box, not lost on a disabled button
    expect(all().find((element) => element.tagName === "DIALOG")?.textContent).toContain(AGAIN);

    await type(again, "secret");
    await press(button("Delete forever"));
    expect(calls.slice(2)).toEqual(["POST /api/auth/confirm-password", "DELETE /api/documents/d1/permanent"]);
    expect(removed).toEqual(["Deleted Bill.pdf for good."]);
  });

  it("is offered Delete forever on a selection; a 403 keeps the question open, and the retry deletes", async () => {
    serve({
      "POST /api/auth/confirm-password": [[204], [204]],
      "DELETE /api/documents/d1/permanent": [[403, { error: { code: "password_confirm_required", message: AGAIN } }], [204]],
    });
    await show(admin("admin", false));
    await tick(byLabel("Select Bill.pdf"));
    await press(button("Delete forever (1)"));
    await type(passwordBox("bulk-forever-password"), "secret");
    await press(button("Delete now"));

    expect(calls).toEqual(["POST /api/auth/confirm-password", "DELETE /api/documents/d1/permanent"]);
    expect(removed).toEqual([]);
    expect(doc.body.textContent).toContain(`1 document could not be deleted. ${AGAIN}`);
    expect(passwordBox("bulk-forever-password")?.value).toBe("");
    expect(doc.activeElement).toBe(passwordBox("bulk-forever-password"));

    await type(passwordBox("bulk-forever-password"), "secret");
    await press(button("Delete now"));
    expect(calls.slice(2)).toEqual(["POST /api/auth/confirm-password", "DELETE /api/documents/d1/permanent"]);
    expect(removed).toEqual(["Deleted 1 document for good."]);
  });

  it("asks again when the password ran out partway, though the first failure was something else", async () => {
    const second = { ...binned, id: "d2", family_id: "d2", display_name: "Rent.pdf" };
    const third = { ...binned, id: "d3", family_id: "d3", display_name: "Tax.pdf" };
    serve({
      "POST /api/auth/confirm-password": [[204]],
      "DELETE /api/documents/d1/permanent": [[404, { error: { code: "not_found", message: "Not found." } }]],
      "DELETE /api/documents/d2/permanent": [[403, { error: { code: "password_confirm_required", message: AGAIN } }]],
      "DELETE /api/documents/d3/permanent": [[403, { error: { code: "password_confirm_required", message: AGAIN } }]],
    });
    await show(admin("admin", false), [binned, second, third]);
    await tick(byLabel("Select all visible documents"));
    await press(button("Delete forever (3)"));
    await type(passwordBox("bulk-forever-password"), "secret");
    await press(button("Delete now"));

    expect(doc.body.textContent).toContain(`3 documents could not be deleted. ${AGAIN}`);
    expect(doc.activeElement).toBe(passwordBox("bulk-forever-password")); // the question is still open
  });

  it("stops asking once the server says the right is gone (an out-of-date screen)", async () => {
    serve({
      "DELETE /api/documents/d1/permanent": [[403, { error: { code: "password_confirm_required", message: AGAIN } }]],
      "POST /api/auth/confirm-password": [[403, { error: { code: "forbidden", message: "You can't do that." } }]],
    });
    await show(admin("admin", true));
    await tick(byLabel("Select Bill.pdf"));
    await press(button("Delete forever (1)"));
    await press(button("Delete now"));
    await type(passwordBox("bulk-forever-password"), "secret");
    await press(button("Delete now"));
    expect(passwordBox("bulk-forever-password")).toBeNull(); // asked for again only by the next 403
  });

  it("keeps an admin with two-step as before: no password, one call", async () => {
    serve({ "DELETE /api/documents/d1/permanent": [[204]] });
    await show(admin("admin", true));
    await tick(byLabel("Select Bill.pdf"));
    await press(button("Delete forever (1)"));
    expect(passwordBox("bulk-forever-password")).toBeNull();
    await press(button("Delete now"));
    expect([calls, removed]).toEqual([["DELETE /api/documents/d1/permanent"], ["Deleted 1 document for good."]]);
  });
});
