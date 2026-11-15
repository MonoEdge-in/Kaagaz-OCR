// PRD 7 section 6, PR 13: opening a locked file, the lock and unlock dialogs, the bulk lock and
// the give pop-up. The words and rules (components/viewer/locked.ts) in node; the screens
// rendered with react-dom in the small fake DOM (tests/fake-dom.ts) against a fake fetch, and
// pressed: every state of the prompt (password, wrong, blocked, Google), the page-picture viewer
// (pages, turns, countdown, the check ending), Unlock, Lock with its people and refusals, the
// selection's Lock, and the give pop-up's ticks, search and none-chosen line.

import { act, createElement } from "react";
import type { ReactElement } from "react";
import type { Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { activityWords } from "@/components/activity/words";
import { describeAction } from "@/components/admin/logic";
import { LockedFilesSection } from "@/components/admin/LockedFilesSection";
import { DocumentsTable } from "@/components/documents/DocumentsTable";
import { LockDialog } from "@/components/documents/LockDialog";
import { LockedFilesPicker } from "@/components/documents/LockedFilesPicker";
import { buildTree } from "@/components/documents/folderTree";
import {
  SEARCH_FROM,
  chosenText,
  giveIntroText,
  lockExplainText,
  lockTitle,
  lockedFileLabel,
  lockedMessage,
  matchingFiles,
  noneChosenText,
  openErrorText,
  openFailure,
  openForText,
  refusalsOf,
  refusedLines,
  secondsLeft,
  turnStyle,
} from "@/components/viewer/locked";
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
  providers: [],
});

// --- the words and rules ---

describe("the locked files' words", () => {
  it("says why Google did not confirm, by its code", () => {
    expect(openErrorText(null)).toBeNull();
    expect(openErrorText("not_fresh")).toBe("Sign in with Google again to open locked files.");
    expect(openErrorText("not_yours")).toBe("That Google account isn't the one connected to you.");
    expect(openErrorText("expired")).toBe("Google couldn't confirm it was you. Try again.");
  });

  it("says why the password did not open them, and when a block ends", () => {
    const now = Date.parse("2026-11-15T10:00:00Z");
    expect(openFailure(new ApiError(400, "wrong_password", "Your password is not right."), now)).toEqual({
      message: "Your password is not right.",
      google: false,
    });
    expect(openFailure(new ApiError(409, "google_confirm_required", "Use Google."), now)).toEqual({ message: "", google: true });
    const blocked = openFailure(new ApiError(429, "open_blocked", "Too many wrong passwords.", undefined, 900), now);
    expect(blocked.message).toMatch(/^Too many wrong passwords\. You can try again at .+\.$/);
    expect(openFailure(new ApiError(429, "rate_limited", "Slow down."), now).message).toBe("Slow down.");
    expect(openFailure(new TypeError("offline"), now).message).toBe("Check your internet connection, then try again.");
  });

  it("counts down the 10 minutes, never below none", () => {
    const now = Date.parse("2026-11-15T10:00:00Z");
    expect(secondsLeft("2026-11-15T10:10:00Z", now)).toBe(600);
    expect(openForText(600)).toBe("Open for 10:00");
    expect(openForText(65)).toBe("Open for 1:05");
    expect(secondsLeft("2026-11-15T09:59:00Z", now)).toBe(0);
    expect(turnStyle(0)).toBeUndefined();
    expect(turnStyle(90)).toEqual({ transform: "rotate(90deg)" });
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

  it("words the give pop-up as the PRD does", () => {
    expect(giveIntroText("Ravi", "Land & Legal", 120, 5)).toBe(
      "Give Ravi access to Land & Legal. Land & Legal has 120 files. 5 are locked. Choose which locked files Ravi can open:",
    );
    expect(giveIntroText("Ravi", "HR", 1, 1)).toBe("Give Ravi access to HR. HR has 1 file. 1 is locked. Choose which locked files Ravi can open:");
    expect(giveIntroText("Ravi", "HR", 4, 0)).toBe("Give Ravi access to HR. HR has 4 files. None are locked.");
    expect(chosenText(3, 5)).toBe("3 of 5 chosen");
    expect(noneChosenText("Ravi", 115)).toBe("Ravi will get the 115 other files, but none of the locked ones.");
    expect(noneChosenText("Ravi", 1)).toBe("Ravi will get the 1 other file, but none of the locked ones.");
    const deed = { id: "d1", name: "Sale deed.pdf", path: "Land & Legal / Daregaon", open: false };
    expect(lockedFileLabel(deed)).toBe("Sale deed.pdf (Land & Legal / Daregaon)");
    expect(lockedFileLabel({ ...deed, path: null })).toBe("Sale deed.pdf");
    expect(matchingFiles([deed], "  DAREGAON ")).toEqual([deed]);
    expect(matchingFiles([deed], "loan")).toEqual([]);
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
const screen = (detail: DocumentDetail = lockedFile(), openError: string | null = null) => {
  changed = [];
  return mount(
    createElement(LockedDocument, {
      detail,
      back: { href: "/documents", name: "documents" },
      openError,
      onChanged: (message: string) => changed.push(message),
    }),
  );
};
const closed = { open: false, until: null, via: "password" };
const OPEN_UNTIL = "2026-11-15T10:10:00Z";

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

  it("asks for the password, says a wrong one, then shows the pages as pictures only", async () => {
    vi.useFakeTimers({ now: Date.parse("2026-11-15T10:00:00Z"), toFake: ["Date", "performance", "setInterval", "clearInterval"] });
    serve({
      "GET /api/documents/locked-open": [[200, closed]],
      "POST /api/documents/locked-open": [
        error(400, "wrong_password", "Your password is not right."),
        [200, { open: true, until: OPEN_UNTIL, via: "password" }],
      ],
    });
    await screen();
    expect(text()).toContain(
      "Enter your password to open locked files. This keeps locked files safe on shared computers. They stay open for 10 minutes.",
    );
    expect(button("Open")?.hasAttribute("disabled")).toBe(true); // nothing typed yet

    await type(byId("locked-open-password"), "wrong");
    await submit("locked-open-password");
    expect(calls[1]).toEqual({ call: "POST /api/documents/locked-open", body: { password: "wrong" } });
    expect(all().find((each) => each.getAttribute("role") === "alert")?.textContent).toBe("Your password is not right.");
    expect(byId("locked-open-password")?.value).toBe(""); // typed again

    await type(byId("locked-open-password"), "right");
    await submit("locked-open-password");
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
    expect(second?.style.transform).toBe("rotate(90deg)"); // page 2 is turned for everyone
    expect(button("Next page")?.hasAttribute("disabled")).toBe(true);
  });

  it("goes back to the prompt when the 10 minutes are up", async () => {
    vi.useFakeTimers({ now: Date.parse("2026-11-15T10:09:58Z"), toFake: ["Date", "performance", "setInterval", "clearInterval"] });
    serve({ "GET /api/documents/locked-open": [[200, { open: true, until: OPEN_UNTIL, via: "password" }]] });
    await screen();
    expect(text()).toContain("Open for 0:02");
    await act(async () => void vi.advanceTimersByTime(3000));
    expect(images()).toEqual([]);
    expect(byId("locked-open-password")).not.toBeNull();
  });

  it("goes back to the prompt when a picture is refused because the check ended (signed out elsewhere)", async () => {
    serve({
      "GET /api/documents/locked-open": [
        [200, { open: true, until: "2099-01-01T00:00:00Z", via: "password" }],
        [200, closed],
      ],
    });
    await screen();
    await act(async () => void fire(images()[0]!, "error"));
    await settle();
    expect(images()).toEqual([]);
    expect(byId("locked-open-password")).not.toBeNull();
  });

  it("says a page failed when the check is still live", async () => {
    serve({
      "GET /api/documents/locked-open": [
        [200, { open: true, until: "2099-01-01T00:00:00Z", via: "password" }],
        [200, { open: true, until: "2099-01-01T00:00:00Z", via: "password" }],
      ],
    });
    await screen();
    await act(async () => void fire(images()[0]!, "error"));
    await settle();
    expect(text()).toContain("This page couldn't be shown. Try again.");
  });

  it("says when five wrong tries blocked opening, and when to try again", async () => {
    serve({
      "GET /api/documents/locked-open": [[200, closed]],
      "POST /api/documents/locked-open": [
        [429, { error: { code: "open_blocked", message: "Too many wrong passwords. Try again in 15 minutes." } }, { "Retry-After": "900" }],
      ],
    });
    await screen();
    await type(byId("locked-open-password"), "guess");
    await submit("locked-open-password");
    expect(text()).toMatch(/Too many wrong passwords\. Try again in 15 minutes\. You can try again at .+\./);
  });

  it("sends a person who signs in with Google only to Google, and back to this file", async () => {
    serve({
      "GET /api/documents/locked-open": [[200, { open: false, until: null, via: "google" }]],
      "POST /api/auth/oidc/google/confirm": [[200, { url: "https://accounts.google.com/o/oauth2/auth?x=1" }]],
    });
    await screen();
    expect(text()).toContain("Confirm with Google to open locked files.");
    expect(byId("locked-open-password")).toBeNull();
    await press(button("Continue with Google"));
    expect(calls[1]).toEqual({ call: "POST /api/auth/oidc/google/confirm", body: { next: "/documents/d1" } });
    expect((window as unknown as { location: { href: string } }).location.href).toBe(
      "https://accounts.google.com/o/oauth2/auth?x=1",
    );
  });

  it("turns to Google when the password is refused as google_confirm_required", async () => {
    serve({
      "GET /api/documents/locked-open": [[200, closed]],
      "POST /api/documents/locked-open": [error(409, "google_confirm_required", "Confirm with Google.")],
    });
    await screen();
    await type(byId("locked-open-password"), "anything");
    await submit("locked-open-password");
    expect(button("Continue with Google")).not.toBeNull();
    expect(all().some((each) => each.getAttribute("role") === "alert")).toBe(false);
  });

  it("says why Google came back without opening them", async () => {
    serve({ "GET /api/documents/locked-open": [[200, { open: false, until: null, via: "google" }]] });
    await screen(lockedFile(), "not_fresh");
    expect(text()).toContain("Sign in with Google again to open locked files.");
  });

  it("asks again when the check could not be read", async () => {
    serve({ "GET /api/documents/locked-open": [error(500, "error", "Oops."), [200, closed]] });
    await screen();
    expect(text()).toContain("Kaagaz couldn't check whether locked files are open. Try again.");
    await press(button("Try again"));
    expect(byId("locked-open-password")).not.toBeNull();
  });

  it("gives an Admin's session Unlock, with the warning and the password; nobody else", async () => {
    serve({ "GET /api/documents/locked-open": [[200, closed]] });
    await screen();
    expect(button("Unlock")).toBeNull();
    await unmount();

    session.me = person("admin", "admin");
    serve({
      "GET /api/documents/locked-open": [[200, closed]],
      "POST /api/documents/d1/unlock": [error(400, "wrong_password", "Your password is not right."), [200, { unlocked: 2, reading: true }]],
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

// --- giving a place that holds locked files ---

const placeFiles = (count: number) =>
  Array.from({ length: count }, (_, n) => ({
    id: `f${n + 1}`,
    name: n === 0 ? "Sale deed.pdf" : `Deed ${n + 1}.pdf`,
    path: n === 0 ? "Land & Legal / Daregaon" : null,
    open: n === 1,
  }));

describe("the give pop-up", () => {
  async function picker(count: number, onGive: (ids: string[]) => Promise<void>, kind: "department" | "folder" = "department") {
    await mount(
      createElement(LockedFilesPicker, {
        person: { id: "u3", name: "Ravi" },
        place: { kind, id: "p1", name: "Land & Legal" },
        head: kind === "department" ? false : undefined,
        onGive,
        onClose: () => undefined,
      }),
    );
    await settle();
  }

  it("lists the place's locked files, ticks the ones already given, and gives the ticked", async () => {
    serve({ "GET /api/admin/departments/p1/locked-files?user=u3&head=false": [[200, { files: 120, locked: placeFiles(5) }]] });
    const given: string[][] = [];
    await picker(5, async (ids) => void given.push(ids));
    expect(text()).toContain(
      "Give Ravi access to Land & Legal. Land & Legal has 120 files. 5 are locked. Choose which locked files Ravi can open:",
    );
    expect(text()).toContain("Sale deed.pdf (Land & Legal / Daregaon)");
    expect(boxes().map((box) => box.checked)).toEqual([false, true, false, false, false]);
    expect(text()).toContain("1 of 5 chosen");
    expect(all().some((each) => each.getAttribute("type") === "search")).toBe(false); // 10 or fewer

    await press(button("Clear all"));
    expect(text()).toContain("0 of 5 chosen");
    expect(text()).toContain("Ravi will get the 115 other files, but none of the locked ones.");
    await press(button("Select all"));
    expect(text()).toContain("5 of 5 chosen");
    await tick(boxes()[0]);
    await press(button("Give access"));
    expect(given).toEqual([["f2", "f3", "f4", "f5"]]);
  });

  it("gives none ticked too, and says why a give failed", async () => {
    serve({ "GET /api/folders/p1/locked-files?user=u3": [[200, { files: 6, locked: placeFiles(2) }]] });
    await picker(2, () => Promise.reject(new ApiError(409, "not_in_place", "One of these files is not in this folder.")), "folder");
    await press(button("Clear all"));
    await press(button("Give access"));
    expect(text()).toContain("One of these files is not in this folder.");
  });

  it("finds a file by name past ten", async () => {
    serve({ "GET /api/admin/departments/p1/locked-files?user=u3&head=false": [[200, { files: 40, locked: placeFiles(SEARCH_FROM + 1) }]] });
    await picker(SEARCH_FROM + 1, async () => undefined);
    const search = byLabel("Find a locked file by name")!;
    await type(search, "daregaon");
    expect(boxes()).toHaveLength(1);
    expect(text()).toContain("1 of 11 chosen"); // the count is of every file, not only those shown
  });
});

describe("the Access page's locked files", () => {
  it("lists them and takes one back", async () => {
    serve({
      "DELETE /api/documents/f1/lock-grants/u3": [error(500, "error", "Something went wrong."), [204]],
    });
    const said: string[] = [];
    await mount(
      createElement(LockedFilesSection, {
        userId: "u3",
        items: [{ id: "f1", name: "Sale deed.pdf", path: "Land & Legal", given_at: "2026-11-15T10:00:00Z", given_by: "u1" }],
        onChanged: (message: string) => said.push(message),
      }),
    );
    expect(text()).toContain("Sale deed.pdf (Land & Legal)");
    await press(button("Remove"));
    expect(text()).toContain("Something went wrong.");
    await press(button("Remove"));
    expect(said).toEqual(["Access removed."]);
  });

  it("says when there are none", async () => {
    await mount(createElement(LockedFilesSection, { userId: "u3", items: [], onChanged: () => undefined }));
    expect(text()).toContain("They can't open any locked file.");
  });
});
