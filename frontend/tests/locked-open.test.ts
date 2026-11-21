// PRD 7 section 6, PR 13: opening a locked file, the lock and unlock dialogs and the bulk lock.
// The words and rules (components/viewer/locked.ts) in node; the screens rendered with react-dom
// in the small fake DOM (tests/fake-dom.ts) against a fake fetch, and pressed: every state of the
// prompt (password, wrong, blocked, Google), the page-picture viewer (pages, turns, countdown by
// the server's seconds, the check ending, a failed page, no context menu, no print), Unlock, Lock
// with its people and refusals, the selection's Lock, and where focus goes after each.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { act, createElement, useState } from "react";
import type { ReactElement } from "react";
import type { Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { activityWords } from "@/components/activity/words";
import { describeAction } from "@/components/admin/logic";
import { DocumentsTable } from "@/components/documents/DocumentsTable";
import { LockDialog } from "@/components/documents/LockDialog";
import { buildTree } from "@/components/documents/folderTree";
import {
  lockExplainText,
  lockTitle,
  lockedMessage,
  openFailure,
  openForText,
  refusalsOf,
  refusedLines,
  secondsLeft,
  turnedPicture,
} from "@/components/viewer/locked";
import { useHeadingAfterReload } from "@/components/viewer/useHeadingAfterReload";
import { ApiError } from "@/lib/api";
import type { DocumentDetail, DocumentSummary, Me, SessionScope } from "@/lib/types";
import { DocumentMenu } from "../app/(app)/documents/[id]/DocumentMenu";
import { LockedDocument } from "../app/(app)/documents/[id]/LockedDocument";
import { FakeDocument, FakeElement, fire, install } from "./fake-dom";

const session = vi.hoisted(() => ({ me: null as unknown }));
vi.mock("@/lib/session", () => ({ useSession: () => ({ me: session.me, refresh: () => undefined }) }));
// next/link prefetches with requestIdleCallback on `self`, which the fake DOM has not: a plain link.
vi.mock("next/link", async () => {
  const { createElement: make } = await import("react");
  return { default: ({ href, children }: { href: string; children: unknown }) => make("a", { href }, children as never) };
});

const person = (role: Me["user"]["role"], scope: SessionScope): Me => ({
  user: {
    id: "u1",
    email: "asha@example.in",
    display_name: "Asha",
    role,
    must_change_password: false,
    totp_enabled: true,
    two_step_turned_off: false,
    can_delete_forever: false,
    email_confirmed: true,
  },
  customer: { id: "c1", name: "Sharma Traders" },
  session: { scope },
});

// --- the words and rules ---

describe("the locked files' words", () => {
  it("says why locked files did not open, in the server's own words", () => {
    expect(openFailure(new ApiError(429, "rate_limited", "Too many tries to open locked files. Try again in an hour."))).toEqual({
      message: "Too many tries to open locked files. Try again in an hour.",
    });
    expect(openFailure(new TypeError("Failed to fetch")).message).toBe("Check your internet connection, then try again.");
  });

  it("counts down from the server's seconds by performance.now(), never below none", () => {
    expect(secondsLeft(600, 1000, 1000)).toBe(600);
    expect(secondsLeft(600, 1000, 1000 + 535_500)).toBe(65);
    expect(openForText(600)).toBe("Open for 10:00");
    expect(openForText(65)).toBe("Open for 1:05");
    expect(secondsLeft(600, 1000, 1000 + 601_000)).toBe(0);
    expect(secondsLeft(600, 1000, 500)).toBe(600); // a tick from before it opened: none spent
  });

  it("frames a turned page in its turned shape, as the page viewer does", () => {
    const page = { width: 600, height: 800 }; // portrait, 3:4
    expect(turnedPicture(page, 0)).toEqual({
      frame: { aspectRatio: String(0.75) },
      picture: { width: "100%", height: "100%", transform: "translate(-50%, -50%)" },
    });
    const sideways = turnedPicture(page, 90);
    expect(Number(sideways.frame.aspectRatio)).toBeCloseTo(4 / 3); // landscape: nothing clipped
    expect(sideways.picture).toEqual({
      width: "75%",
      height: String(100 / 0.75) + "%",
      transform: "translate(-50%, -50%) rotate(90deg)",
    });
    expect(turnedPicture(page, 270).frame).toEqual(sideways.frame);
    expect(turnedPicture(page, 180)).toEqual({
      frame: { aspectRatio: String(0.75) },
      picture: { width: "100%", height: "100%", transform: "translate(-50%, -50%) rotate(180deg)" },
    });
  });

  it("hides the viewer and its pictures in print", () => {
    const css = readFileSync(join(__dirname, "..", "components/viewer/Locked.module.css"), "utf8");
    expect(css).toMatch(/@media print \{\s*\.viewer,\s*\.picture \{\s*display: none !important;/);
  });

  it("asks to lock one file by its name and many by their count", () => {
    expect(lockTitle(["Sale deed.pdf"])).toBe('Lock "Sale deed.pdf"?');
    expect(lockTitle(["a", "b", "c"])).toBe("Lock 3 files?");
    expect(lockExplainText(1)).toContain("Dashboard totals will no longer count this file.");
    expect(lockExplainText(2)).toContain("no longer count these files.");
    expect([lockedMessage(1), lockedMessage(8)]).toEqual(["File locked.", "8 files locked."]);
  });

  it("names each file a bulk lock refused, and why", () => {
    const caught = new ApiError(409, "not_lockable", "Some can't be locked.", {
      refused: [
        { id: "d1", reason: "kind" },
        { id: "d2", reason: "in_trash" },
        { id: "d3", reason: "something_new" },
      ],
    });
    const refused = refusalsOf(caught);
    expect(refusedLines(refused, new Map([["d1", "Prices.xlsx"], ["d2", "Old.pdf"]]))).toEqual([
      "Prices.xlsx can't be locked yet. Keep it in a private folder instead.",
      "Old.pdf is in the Trash.",
      "A file couldn't be locked.",
    ]);
    expect(refusalsOf(new ApiError(400, "wrong_password", "No."))).toEqual([]);
  });

  it("says each lock action in the activity", () => {
    const entry = (action: string, details: Record<string, unknown> = {}) => ({ action, actor_name: "Asha", details });
    expect(activityWords(entry("lock.lock"))).toBe("locked it");
    expect(activityWords(entry("lock.unlock"))).toBe("unlocked it");
    expect(describeAction("lock.open", { via: "password" })).toBe("Opened locked files with their password");
    expect(describeAction("lock.open", { via: "google" })).toBe("Opened locked files with Google");
    expect(describeAction("lock.open_failed", { via: "password" })).toBe("Typed a wrong password to open locked files");
    expect(describeAction("lock.open_failed", { via: "google" })).toBe("A Google sign-in to open locked files was refused");
    expect(describeAction("lock.open_blocked")).toBe("Blocked from opening locked files after wrong passwords");
    expect(describeAction("lock.view")).toBe("Viewed a locked file");
    expect(describeAction("lock.grant")).toBe("Allowed someone to open a locked file");
    expect(describeAction("lock.revoke")).toBe("Stopped someone opening a locked file");
  });
});

// --- the screens, rendered and pressed ---

let doc: FakeDocument;
let root: Root | null = null;
let calls: { call: string; body: unknown }[] = [];

type Reply = [number, unknown?, Record<string, string>?];

/** fetch answers each "METHOD /path" with the next of its replies (a status, a body, headers). */
function serve(routes: Record<string, Reply[]>) {
  calls = [];
  vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
    const call = `${init.method ?? "GET"} ${url}`;
    calls.push({ call, body: typeof init.body === "string" ? JSON.parse(init.body) : undefined });
    const [status, body, headers] = routes[call]?.shift() ?? [500, { error: { code: "error", message: `no route ${call}` } }];
    return new Response(body === undefined ? null : JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json", ...headers },
    });
  });
}
const asked = () => calls.map((each) => each.call);
const error = (status: number, code: string, message: string, details?: unknown): Reply => [
  status,
  { error: { code, message, details } },
];

async function mount(element: ReactElement) {
  const container = doc.body.appendChild(doc.createElement("div")) as FakeElement;
  const { createRoot } = await import("react-dom/client");
  root = createRoot(container as unknown as Element);
  await act(async () => root!.render(element));
  return container;
}

const all = () => doc.body.all();
const text = () => doc.body.textContent;
const button = (words: string) =>
  all().find(
    (element) =>
      element.tagName === "BUTTON" &&
      (element.textContent.trim() === words || element.all().some((inner) => inner.tagName === "SPAN" && inner.textContent === words)),
  ) ?? null;
const byLabel = (label: string) => all().find((element) => element.getAttribute("aria-label") === label) ?? null;
const byId = (id: string) => all().find((element) => element.getAttribute("id") === id) ?? null;
const images = () => all().filter((element) => element.tagName === "IMG");
const boxes = () => all().filter((element) => element.tagName === "INPUT" && element.type === "checkbox");
const press = (element: FakeElement | null) => act(async () => void fire(element!, "click", { button: 0 }));
const settle = () => act(async () => undefined);
async function unmount() {
  await act(async () => root?.unmount());
  root = null;
}
async function type(box: FakeElement | null, words: string) {
  box!.value = words;
  await act(async () => void fire(box!, "input"));
}
/** Submit the form holding the field `id` (the password box). */
async function submit(id: string) {
  await act(async () => void fire(byId(id)!.closest("form")!, "submit"));
}
async function tick(box: FakeElement | undefined) {
  box!.checked = !box!.checked;
  await act(async () => void fire(box!, "click", { button: 0 }));
}

beforeEach(() => {
  doc = install();
  (window as unknown as { location: { href: string } }).location = { href: "http://localhost/documents/d1" };
  session.me = person("member", "app");
});

afterEach(async () => {
  await unmount();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const lockedFile = (changes: Partial<DocumentDetail> = {}) =>
  ({
    id: "d1",
    display_name: "Sale deed.pdf",
    detected_type: "pdf",
    status: "locked",
    locked: true,
    deleted_at: null,
    page_count: 2,
    pages: [
      { page_no: 1, width: 600, height: 800, has_image: true },
      { page_no: 2, width: 600, height: 800, has_image: true },
    ],
    page_turns: [{ page_no: 2, turn: 90 }],
    ...changes,
  }) as unknown as DocumentDetail;

let changed: string[] = [];
const screen = (detail: DocumentDetail = lockedFile()) => {
  changed = [];
  return mount(
    createElement(LockedDocument, {
      detail,
      back: { href: "/documents", name: "documents" },
      onChanged: (message: string) => changed.push(message),
    }),
  );
};
const closed = { open: false, until: null, seconds: null, via: "keycloak" };
const OPEN_UNTIL = "2026-11-15T10:10:00Z";
const open = (seconds = 600) => ({ open: true, until: OPEN_UNTIL, seconds, via: "keycloak" });

describe("a locked file's screen", () => {
  it("says a file still being prepared, or in Trash, and asks for nothing", async () => {
    serve({});
    await screen(lockedFile({ status: "received" }));
    expect(text()).toContain("This file is still being prepared.");
    await unmount();
    await screen(lockedFile({ deleted_at: "2026-11-14T10:00:00Z" }));
    expect(text()).toContain("This file is in the Trash. Restore it first.");
    expect(asked()).toEqual([]);
  });

  it("asks for the company sign-in, then shows the pages as pictures only", async () => {
    vi.useFakeTimers({ now: Date.parse("2026-11-15T10:00:00Z"), toFake: ["Date", "performance", "setInterval", "clearInterval"] });
    serve({
      "GET /api/documents/locked-open": [[200, closed]],
      "POST /api/documents/locked-open": [[200, open()]],
    });
    await screen();
    expect(text()).toContain("Verify with company sign-in to open locked files.");
    expect(byId("locked-open-password")).toBeNull(); // no password to type

    await act(async () => void fire(all().find((each) => each.tagName === "FORM")!, "submit"));
    expect(calls[1]).toEqual({ call: "POST /api/documents/locked-open", body: undefined });
    const [picture] = images();
    expect(picture?.getAttribute("src")).toBe("/api/documents/d1/locked-pages/1");
    expect(picture?.getAttribute("draggable")).toBe("false");
    expect(text()).toContain("Page 1 of 2");
    expect(text()).toContain("Open for 10:00");
    // No way to the original, its text or Ask: no download link, no other image route.
    expect(all().some((each) => each.tagName === "A" && each.hasAttribute("download"))).toBe(false);
    expect(text()).not.toContain("Ask about this document");
    expect(asked().filter((call) => !call.includes("locked-open"))).toEqual([]);

    await press(button("Next page"));
    const [second] = images();
    expect(second?.getAttribute("src")).toBe("/api/documents/d1/locked-pages/2");
    // Page 2 is turned for everyone: its frame takes the turned (landscape) shape, nothing clipped.
    expect(second?.style.transform).toBe("translate(-50%, -50%) rotate(90deg)");
    expect(Number((second?.parentNode as FakeElement).style.aspectRatio)).toBeCloseTo(4 / 3);
    expect(button("Next page")?.hasAttribute("disabled")).toBe(true);
  });

  it("goes back to the prompt when the 10 minutes are up", async () => {
    vi.useFakeTimers({ now: Date.parse("2026-11-15T10:09:58Z"), toFake: ["Date", "performance", "setInterval", "clearInterval"] });
    serve({ "GET /api/documents/locked-open": [[200, open(2)]] });
    await screen();
    expect(text()).toContain("Open for 0:02");
    await act(async () => void vi.advanceTimersByTime(3000));
    expect(images()).toEqual([]);
    expect(button("Open")).not.toBeNull();
  });

  it("counts down by the server's seconds whatever this PC's clock says", async () => {
    // The PC's clock is an hour behind the server's until: the countdown still follows seconds.
    vi.useFakeTimers({ now: Date.parse("2026-11-15T09:00:00Z"), toFake: ["Date", "performance", "setInterval", "clearInterval"] });
    serve({ "GET /api/documents/locked-open": [[200, open(90)]] });
    await screen();
    expect(text()).toContain("Open for 1:30");
    vi.setSystemTime(Date.parse("2026-11-15T12:00:00Z")); // then it jumps three hours ahead
    await act(async () => void vi.advanceTimersByTime(30_000));
    expect(text()).toContain("Open for 1:00");
    expect(images()).toHaveLength(1);
    await act(async () => void vi.advanceTimersByTime(61_000));
    expect(images()).toEqual([]); // ended by its own 90 seconds, not by the PC's clock
    expect(button("Open")).not.toBeNull();
  });

  it("offers no context menu on the pictures (Save image as)", async () => {
    vi.useFakeTimers({ now: Date.parse("2026-11-15T10:00:00Z"), toFake: ["Date", "performance", "setInterval", "clearInterval"] });
    serve({ "GET /api/documents/locked-open": [[200, open()]] });
    await screen();
    const frame = images()[0]!.parentNode!.parentNode as FakeElement;
    expect(fire(frame, "contextmenu", { button: 2 })).toBe(false); // prevented
    expect(fire(images()[0]!, "contextmenu", { button: 2, bubbles: true })).toBe(false);
  });

  it("goes back to the prompt when a picture is refused because the check ended (signed out elsewhere)", async () => {
    serve({
      "GET /api/documents/locked-open": [
        [200, open()],
        [200, closed],
      ],
    });
    await screen();
    await act(async () => void fire(images()[0]!, "error"));
    await settle();
    expect(images()).toEqual([]);
    expect(button("Open")).not.toBeNull();
  });

  it("says a page failed when the check is still live, tries it again, and forgets it on another page", async () => {
    serve({ "GET /api/documents/locked-open": [[200, open()], [200, open()], [200, open()]] });
    await screen();
    await act(async () => void fire(images()[0]!, "error"));
    await settle();
    expect(text()).toContain("This page couldn't be shown.");
    expect(images()).toEqual([]);
    await press(button("Try again"));
    expect(images()[0]?.getAttribute("src")).toBe("/api/documents/d1/locked-pages/1"); // drawn anew
    await act(async () => void fire(images()[0]!, "error"));
    await settle();
    await press(button("Next page"));
    expect(text()).not.toContain("This page couldn't be shown.");
    expect(images()[0]?.getAttribute("src")).toBe("/api/documents/d1/locked-pages/2");
  });





  it("asks again when the check could not be read", async () => {
    serve({ "GET /api/documents/locked-open": [error(500, "error", "Oops."), [200, closed]] });
    await screen();
    expect(text()).toContain("Kaagaz couldn't check whether locked files are open. Try again.");
    await press(button("Try again"));
    expect(button("Open")).not.toBeNull();
  });

  it("gives an Admin's session Unlock, with the warning and the password; nobody else", async () => {
    serve({ "GET /api/documents/locked-open": [[200, closed]] });
    await screen();
    expect(button("Unlock")).toBeNull();
    await unmount();

    session.me = person("admin", "admin");
    serve({
      "GET /api/documents/locked-open": [[200, closed]],
      "POST /api/documents/d1/unlock": [error(400, "wrong_password", "Your password is not right."), [200, { unlocked: true, reading: true }]],
    });
    await screen();
    await press(button("Unlock"));
    const dialog = all().find((each) => each.tagName === "DIALOG")!;
    expect(dialog.textContent).toContain('Unlock "Sale deed.pdf"?');
    expect(dialog.textContent).toContain("Everyone who can see its folder can open it, and Ask can use it again.");
    await type(byId("unlock-password"), "nope");
    await submit("unlock-password");
    expect(dialog.textContent).toContain("Your password is not right.");
    await type(byId("unlock-password"), "secret");
    await submit("unlock-password");
    expect(calls.at(-1)).toEqual({ call: "POST /api/documents/d1/unlock", body: { password: "secret" } });
    expect(changed).toEqual(["File unlocked."]);
  });
});

// --- locking ---

const people = [
  { id: "u1", name: "Asha", you: true },
  { id: "u2", name: "Priya", you: false },
  { id: "u3", name: "Ravi", you: false },
];

describe("the lock dialog", () => {
  it("lists who can see it, ticks the Admin, and locks with the password", async () => {
    serve({
      "GET /api/documents/d1/lock-preview": [[200, { people }]],
      "POST /api/documents/d1/lock": [
        error(409, "not_lockable", "This kind of file can't be locked yet. Keep it in a private folder instead."),
        [200, { locked: true, versions: 1, open_to: 2 }],
      ],
    });
    const locked: string[] = [];
    await mount(
      createElement(LockDialog, {
        documents: [{ id: "d1", display_name: "Sale deed.pdf" }],
        onClose: () => undefined,
        onLocked: (message: string) => locked.push(message),
      }),
    );
    await settle();
    expect(text()).toContain('Lock "Sale deed.pdf"?');
    expect(text()).toContain("People who can open it can still photograph the screen.");
    expect(text()).toContain("Asha (you)");
    expect(boxes().map((box) => box.checked)).toEqual([true, false, false]);
    await tick(boxes()[2]);
    await type(byId("lock-password"), "secret");
    await submit("lock-password");
    expect(text()).toContain("This kind of file can't be locked yet. Keep it in a private folder instead.");
    expect(locked).toEqual([]);

    await type(byId("lock-password"), "secret");
    await submit("lock-password");
    expect(calls.at(-1)).toEqual({
      call: "POST /api/documents/d1/lock",
      body: { open_to: ["u1", "u3"], password: "secret" },
    });
    expect(locked).toEqual(["File locked."]);
  });

  it("locks a selection all or nothing, naming each file it refused", async () => {
    serve({
      "POST /api/documents/lock-preview": [[200, { people }]],
      "POST /api/documents/lock": [
        error(409, "not_lockable", "Some files can't be locked.", { refused: [{ id: "d2", reason: "kind" }] }),
        [200, { locked: 2 }],
      ],
    });
    const locked: string[] = [];
    await mount(
      createElement(LockDialog, {
        documents: [
          { id: "d1", display_name: "Sale deed.pdf" },
          { id: "d2", display_name: "Prices.xlsx" },
        ],
        onClose: () => undefined,
        onLocked: (message: string) => locked.push(message),
      }),
    );
    await settle();
    expect(calls[0]).toEqual({ call: "POST /api/documents/lock-preview", body: { document_ids: ["d1", "d2"] } });
    expect(text()).toContain("Lock 2 files?");
    await type(byId("lock-password"), "secret");
    await submit("lock-password");
    expect(text()).toContain("Prices.xlsx can't be locked yet. Keep it in a private folder instead.");
    await type(byId("lock-password"), "secret");
    await submit("lock-password");
    expect(calls.at(-1)?.body).toEqual({ document_ids: ["d1", "d2"], open_to: ["u1"], password: "secret" });
    expect(locked).toEqual(["2 files locked."]);
  });

  it("says when the people could not be loaded, and keeps Lock off", async () => {
    serve({ "GET /api/documents/d1/lock-preview": [error(404, "not_found", "We couldn't find this document.")] });
    await mount(
      createElement(LockDialog, {
        documents: [{ id: "d1", display_name: "Sale deed.pdf" }],
        onClose: () => undefined,
        onLocked: () => undefined,
      }),
    );
    await settle();
    expect(text()).toContain("We couldn't find this document.");
    expect(button("Lock file")?.hasAttribute("disabled")).toBe(true);
  });
});

const detail = {
  id: "d1",
  display_name: "Sale deed.pdf",
  deleted_at: null,
  can_delete: true,
  status: "ready",
  detected_type: "pdf",
} as unknown as DocumentDetail;

describe("Lock in the document's menu", () => {
  const menu = () =>
    mount(
      createElement(DocumentMenu, {
        buttonRef: { current: null },
        detail,
        readAgain: false,
        onReadAgain: () => undefined,
        onChanged: () => undefined,
      }),
    );

  it("is an Admin's, in an admin session, and opens the lock dialog", async () => {
    session.me = person("admin", "admin");
    serve({ "GET /api/documents/d1/lock-preview": [[200, { people }]] });
    await menu();
    await press(byLabel("More actions for Sale deed.pdf"));
    await press(button("LockKeep it out of Ask and open it only with a password"));
    await settle();
    expect(all().find((each) => each.tagName === "DIALOG")?.textContent).toContain('Lock "Sale deed.pdf"?');
  });

  it("is nobody else's", async () => {
    for (const me of [person("member", "app"), person("admin", "app")]) {
      session.me = me;
      await menu();
      await press(byLabel("More actions for Sale deed.pdf"));
      expect(text()).not.toContain("Keep it out of Ask");
      await unmount();
      doc = install();
    }
  });
});

const row = (id: string, name: string, locked = false) =>
  ({
    id,
    display_name: name,
    detected_type: "pdf",
    size_bytes: 1000,
    status: locked ? "locked" : "ready",
    locked,
    failure_code: null,
    status_message: "Ready.",
    page_count: 1,
    created_at: "2026-10-15T10:00:00Z",
    uploaded_by_name: "Priya",
    folder_id: null,
    tags: [],
    deleted_at: null,
    version_no: 1,
    family_id: id,
    can_delete: true,
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
  }) as unknown as DocumentSummary;

describe("the selection's Lock", () => {
  async function list(me: Me, saved: string[] = []) {
    session.me = me;
    const props = {
      items: [row("d1", "Sale deed.pdf"), row("d2", "Loan.pdf"), row("d3", "Will.pdf", true)],
      inTrash: false,
      me,
      now: Date.parse("2026-11-07T10:00:00Z"),
      tree: buildTree([]),
      tags: [],
      suggestions: [],
      movedLeaves: false,
      sorting: { sort: "name", order: "asc", onSort: () => undefined },
      onRemoved: () => undefined,
      onSaved: (message: string) => saved.push(message),
      onFocusList: () => undefined,
      onTagCreated: () => undefined,
      onFolderCreated: () => undefined,
      onUploadVersion: () => undefined,
    } as unknown as Parameters<typeof DocumentsTable>[0];
    await mount(createElement(DocumentsTable, props));
  }
  const tickRow = (name: string) => tick(byLabel(`Select ${name}`) ?? undefined);

  it("locks the ticked files that are not locked yet, for an Admin's session", async () => {
    serve({ "POST /api/documents/lock-preview": [[200, { people }]], "POST /api/documents/lock": [[200, { locked: 2 }]] });
    const saved: string[] = [];
    await list(person("admin", "admin"), saved);
    await tickRow("Sale deed.pdf");
    expect(button("Lock 1 file")).not.toBeNull();
    await tickRow("Loan.pdf");
    await tickRow("Will.pdf"); // locked already: not counted
    await press(button("Lock 2 files"));
    await settle();
    await type(byId("lock-password"), "secret");
    await submit("lock-password");
    expect(calls.at(-1)?.body).toEqual({ document_ids: ["d1", "d2"], open_to: ["u1"], password: "secret" });
    expect(saved).toEqual(["2 files locked."]);
  });

  it("is not offered to anyone else", async () => {
    serve({});
    await list(person("reviewer", "app"));
    await tickRow("Sale deed.pdf");
    expect(button("Lock 1 file")).toBeNull();
  });
});

// --- where focus goes after a lock or unlock (the button that opened the dialog is gone) ---

describe("focus after a lock or unlock", () => {
  function Heading({ loaded, onReady }: { loaded: unknown; onReady(focus: () => void): void }) {
    const { headingRef, focusAfterReload } = useHeadingAfterReload(loaded);
    onReady(focusAfterReload);
    return createElement("h1", { ref: headingRef, tabIndex: -1 }, "Sale deed.pdf");
  }
  function Reloads({ onReady }: { onReady(focus: () => void, reload: () => void): void }) {
    const [loaded, setLoaded] = useState<object>({});
    return createElement(Heading, { loaded, onReady: (focus) => onReady(focus, () => setLoaded({})) });
  }

  it("focuses the heading once the file is loaded again, only when asked", async () => {
    let focus = () => undefined as void;
    let reload = () => undefined as void;
    await mount(createElement(Reloads, { onReady: (f, r) => ((focus = f), (reload = r)) }));
    const heading = all().find((each) => each.tagName === "H1")!;
    await act(async () => reload()); // a reload nobody asked to focus after
    expect(doc.activeElement).not.toBe(heading);
    focus();
    expect(doc.activeElement).not.toBe(heading); // not before the file is loaded again
    await act(async () => reload());
    expect(doc.activeElement).toBe(heading);
    doc.body.focus();
    await act(async () => reload());
    expect(doc.activeElement).not.toBe(heading); // once
  });

  it("asks for the heading's focus on a lock from the menu and on an unlock, in the document's screen", () => {
    const view = readFileSync(join(__dirname, "..", "app/(app)/documents/[id]/DocumentView.tsx"), "utf8");
    expect(view).toContain("const { headingRef, focusAfterReload } = useHeadingAfterReload(shown);");
    expect(view).toMatch(/onLocked=\{\(message\) => \{\s*focusAfterReload\(\);\s*reloadWith\(message\);/);
    expect(view).toMatch(/const unlocked = \(message: string\) => \{\s*focusAfterReload\(\);/);
    expect(view).toContain("headingRef={headingRef}");
    expect(view).toContain("<h1 ref={headingRef} tabIndex={-1} className={styles.title}>");
  });

  it("puts the locked screen's heading where it can take focus", async () => {
    serve({ "GET /api/documents/locked-open": [[200, closed]] });
    const headingRef = { current: null as HTMLHeadingElement | null };
    await mount(
      createElement(LockedDocument, {
        detail: lockedFile(),
        back: { href: "/documents", name: "documents" },
        onChanged: () => undefined,
        headingRef,
      }),
    );
    const heading = headingRef.current as unknown as FakeElement;
    expect(heading.tagName).toBe("H1");
    expect(heading.getAttribute("tabindex")).toBe("-1");
    expect(heading.textContent).toBe("Sale deed.pdf");
  });

  it("puts focus back on the document menu's button when cancelled, and asks for the heading once locked", async () => {
    session.me = person("admin", "admin");
    const buttonRef = { current: null as HTMLButtonElement | null };
    const locked: string[] = [];
    const changed: string[] = [];
    serve({
      "GET /api/documents/d1/lock-preview": [[200, { people }], [200, { people }]],
      "POST /api/documents/d1/lock": [[200, { locked: true, versions: 1, open_to: 1 }]],
    });
    await mount(
      createElement(DocumentMenu, {
        buttonRef,
        detail,
        readAgain: false,
        onReadAgain: () => undefined,
        onChanged: (message: string) => changed.push(message),
        onLocked: (message: string) => locked.push(message),
      }),
    );
    const menuButton = () => byLabel("More actions for Sale deed.pdf");
    // Cancelled: focus goes back to the menu's button.
    await press(menuButton());
    await press(button("LockKeep it out of Ask and open it only with a password"));
    await settle();
    await press(button("Cancel"));
    const onMenu = () => doc.activeElement?.getAttribute("aria-label") === "More actions for Sale deed.pdf";
    expect(onMenu()).toBe(true);
    // Locked: the screen changes under it, so onLocked (DocumentView: focusAfterReload, then the
    // reload) has the locked screen's heading take the focus, not onChanged.
    await press(menuButton());
    await press(button("LockKeep it out of Ask and open it only with a password"));
    await settle();
    await type(byId("lock-password"), "secret");
    await submit("lock-password");
    expect(locked).toEqual(["File locked."]);
    expect(changed).toEqual([]);
  });

  it("puts focus on the list's select-all box once the selection is locked, as a move does", async () => {
    session.me = person("admin", "admin");
    serve({ "POST /api/documents/lock-preview": [[200, { people }]], "POST /api/documents/lock": [[200, { locked: 2 }]] });
    const focusedList: number[] = [];
    const props = {
      items: [row("d1", "Sale deed.pdf"), row("d2", "Loan.pdf")],
      inTrash: false,
      me: session.me,
      now: Date.parse("2026-11-07T10:00:00Z"),
      tree: buildTree([]),
      tags: [],
      suggestions: [],
      movedLeaves: false,
      sorting: { sort: "name", order: "asc", onSort: () => undefined },
      onRemoved: () => undefined,
      onSaved: () => undefined,
      onFocusList: () => focusedList.push(1),
      onTagCreated: () => undefined,
      onFolderCreated: () => undefined,
      onUploadVersion: () => undefined,
    } as unknown as Parameters<typeof DocumentsTable>[0];
    await mount(createElement(DocumentsTable, props));
    await tick(byLabel("Select Sale deed.pdf") ?? undefined);
    await tick(byLabel("Select Loan.pdf") ?? undefined);
    const opener = button("Lock 2 files")!;
    await press(opener);
    await settle();
    await type(byId("lock-password"), "secret");
    await submit("lock-password");
    expect(opener.isConnected).toBe(false); // the bar's buttons are gone
    const focused = doc.activeElement;
    expect(focused?.tagName === "INPUT" && focused.type === "checkbox" ? "select-all box" : focusedList.length ? "list" : "lost").not.toBe("lost");
  });
});
