import { afterEach, describe, expect, it, vi } from "vitest";
import {
  bulkDoneMessage,
  bulkFailedMessage,
  bulkForeverQuestion,
  bulkTrashQuestion,
  canDeleteForever,
  deletedForeverMessage,
  documentsCount,
  foreverNeeds,
  foreverQuestion,
  lostTheRight,
  restoredMessage,
  runEach,
  selectable,
  trashQuestion,
} from "@/components/documents/trash";
import { ApiError, deleteDocumentForever, listDocuments, restoreDocument } from "@/lib/api";
import type { Me, Role, SessionScope } from "@/lib/types";

const me = (role: Role, scope: SessionScope = "app", totp_enabled = true): Me => ({
  user: {
    id: "u1",
    email: "asha@example.in",
    display_name: "Asha",
    role,
    must_change_password: false,
    totp_enabled,
    two_step_turned_off: false,
    can_delete_forever: false,
    email_confirmed: true, // #457
  },
  customer: { id: "c1", name: "Sharma Traders" },
  session: { scope },
});
const granted = (role: Role, scope: SessionScope = "app"): Me => {
  const base = me(role, scope, false);
  return { ...base, user: { ...base.user, can_delete_forever: true } };
};

describe("who may delete for good", () => {
  // Changed on purpose (#538, Tushar, 7 Nov 2026): an admin session without two-step too, with
  // a typed password (delete-forever-password-538.test.ts).
  it("is an admin session (PRD 1, F5); the server checks it again", () => {
    expect(canDeleteForever(me("admin", "admin"))).toBe(true);
    expect(canDeleteForever(me("admin", "admin", false))).toBe(true);
    expect(canDeleteForever(me("admin", "app"))).toBe(false);
    expect(canDeleteForever(me("member"))).toBe(false);
    expect(canDeleteForever(me("reviewer"))).toBe(false);
  });

  it("tells an admin what to do first, and nobody else", () => {
    expect(foreverNeeds(me("admin", "app"))).toBe("admin_sign_in");
    expect(foreverNeeds(me("admin", "admin", false))).toBeNull(); // #538: types their password instead
    expect(foreverNeeds(me("admin", "admin"))).toBeNull();
    expect(foreverNeeds(me("member"))).toBe("not_allowed");
    expect(foreverNeeds(me("reviewer"))).toBe("not_allowed");
    expect(foreverNeeds(granted("member"))).toBeNull();
  });

  it("lets a member or reviewer an admin allowed, after the company sign-in; a grant never counts for an admin", () => {
    expect([canDeleteForever(granted("member")), canDeleteForever(granted("reviewer"))]).toEqual([true, true]);
    expect([canDeleteForever(granted("admin")), canDeleteForever(me("admin", "admin"))]).toEqual([false, true]);
    expect(selectable({ can_delete: false }, true, true)).toBe(true); // ticked in Trash to delete for good
    expect(selectable({ can_delete: false }, false, true)).toBe(false);
  });

  it("tells a lost right from a sign-in that is still needed", () => {
    const reauth = new ApiError(403, "keycloak_reauth_required", "Confirm it's you with your company sign-in first.");
    expect([lostTheRight(new ApiError(403, "forbidden", "No.")), lostTheRight(reauth)]).toEqual([true, false]);
  });
});

describe("the words for Trash", () => {
  it("counts documents in plain English", () => {
    expect(documentsCount(0)).toBe("0 documents");
    expect(documentsCount(1)).toBe("1 document");
    expect(documentsCount(3)).toBe("3 documents");
  });

  // Changed on purpose (MonoEdge design system, 16 Oct 2026): a destructive question names its document.
  it("asks before moving to Trash, naming the document, and says the earlier versions go too", () => {
    expect(trashQuestion({ version_no: 1, display_name: "Bill.pdf" })).toBe("Move “Bill.pdf” to Trash?");
    expect(trashQuestion({ version_no: 2, display_name: "Bill.pdf" })).toBe(
      "Move “Bill.pdf” and its earlier version to Trash?",
    );
    expect(trashQuestion({ version_no: 4, display_name: "Bill.pdf" })).toBe(
      "Move “Bill.pdf” and its 3 earlier versions to Trash?",
    );
  });

  it("asks before moving a selection to Trash", () => {
    expect(bulkTrashQuestion(1)).toBe("Move 1 selected document to Trash?");
    expect(bulkTrashQuestion(3)).toBe("Move 3 selected documents to Trash?");
  });

  it("says that deleting for good can't be undone", () => {
    // Changed on purpose (MonoEdge design system, 16 Oct 2026): the question names its document.
    expect(foreverQuestion({ version_no: 1, display_name: "Bill.pdf" })).toBe(
      "Delete “Bill.pdf” for good? It can't be brought back.",
    );
    expect(foreverQuestion({ version_no: 3, display_name: "Bill.pdf" })).toBe(
      "Delete “Bill.pdf” and its 2 earlier versions for good? They can't be brought back.",
    );
  });

  it("announces a restore and a delete for good, as plain text", () => {
    expect(restoredMessage("Bill <1>.pdf")).toBe("Restored Bill <1>.pdf.");
    expect(deletedForeverMessage("Bill <1>.pdf")).toBe("Deleted Bill <1>.pdf for good.");
  });
});

describe("acting on a selection", () => {
  it("asks before deleting a selection for good, and says every version goes", () => {
    expect(bulkForeverQuestion(1)).toBe(
      "Delete 1 selected document for good, with all its versions? It can't be brought back.",
    );
    expect(bulkForeverQuestion(3)).toBe(
      "Delete 3 selected documents for good, with all their versions? They can't be brought back.",
    );
  });

  it("announces what was done to the selection", () => {
    expect(bulkDoneMessage("trash", 1)).toBe("Moved 1 document to Trash.");
    expect(bulkDoneMessage("restore", 1)).toBe("Restored 1 document.");
    expect(bulkDoneMessage("restore", 4)).toBe("Restored 4 documents.");
    expect(bulkDoneMessage("forever", 2)).toBe("Deleted 2 documents for good.");
  });

  it("says how many failed, and why", () => {
    expect(bulkFailedMessage("trash", 1, "Check your connection, then try again.")).toBe(
      "1 document could not be moved to Trash. Check your connection, then try again.",
    );
    expect(bulkFailedMessage("restore", 2, "Not allowed.")).toBe(
      "2 documents could not be restored. Not allowed.",
    );
    expect(bulkFailedMessage("forever", 3, "Sign in again.")).toBe(
      "3 documents could not be deleted. Sign in again.",
    );
  });

  it("runs the call for each document, one at a time, in order", async () => {
    const seen: string[] = [];
    const progress: number[] = [];
    const result = await runEach(
      ["a", "b", "c"],
      async (id) => {
        seen.push(id);
      },
      (handled) => progress.push(handled),
    );
    expect(seen).toEqual(["a", "b", "c"]);
    expect(progress).toEqual([1, 2, 3]);
    expect(result).toEqual({ done: ["a", "b", "c"], failed: [], firstError: undefined, lastError: undefined });
  });

  it("keeps going when one fails, and keeps the first error and the last (#538)", async () => {
    const first = new Error("first");
    const second = new Error("second");
    const progress: number[] = [];
    const result = await runEach(
      ["a", "b", "c", "d"],
      async (id) => {
        if (id === "b") throw first;
        if (id === "d") throw second;
      },
      (handled) => progress.push(handled),
    );
    expect(result).toEqual({ done: ["a", "c"], failed: ["b", "d"], firstError: first, lastError: second });
    expect(progress).toEqual([1, 2, 3, 4]);
  });

  it("does nothing for an empty selection", async () => {
    const result = await runEach([], async () => {
      throw new Error("never called");
    });
    expect(result).toEqual({ done: [], failed: [], firstError: undefined, lastError: undefined });
  });
});

describe("the Trash calls", () => {
  const calls: { url: string; method: string; csrf: string | null }[] = [];

  function answer(status: number, body?: unknown) {
    vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
      calls.push({
        url,
        method: init.method ?? "GET",
        csrf: new Headers(init.headers).get("X-Requested-With"),
      });
      return new Response(body === undefined ? null : JSON.stringify(body), { status });
    });
  }

  afterEach(() => {
    vi.unstubAllGlobals();
    calls.length = 0;
  });

  it("restores with POST /documents/{id}/restore, CSRF header included", async () => {
    answer(200, { id: "d1", display_name: "bill.pdf" });
    await expect(restoreDocument("d1")).resolves.toMatchObject({ id: "d1" });
    expect(calls).toEqual([{ url: "/api/documents/d1/restore", method: "POST", csrf: "kaagaz" }]);
  });

  it("deletes for good with DELETE /documents/{id}/permanent", async () => {
    answer(204);
    await expect(deleteDocumentForever("d1")).resolves.toBeUndefined();
    expect(calls).toEqual([{ url: "/api/documents/d1/permanent", method: "DELETE", csrf: "kaagaz" }]);
  });

  it("gives the server's words when the document is not in Trash", async () => {
    const message = "Only a document in Trash can be deleted forever.";
    answer(409, { error: { code: "not_in_trash", message } });
    const failure = await deleteDocumentForever("d1").catch((caught: unknown) => caught);
    expect(failure).toBeInstanceOf(ApiError);
    expect(failure).toMatchObject({ status: 409, code: "not_in_trash", message });
  });

  it("lists only what is in Trash with trashed=true", async () => {
    answer(200, { items: [], total: 0 });
    await listDocuments({ limit: 25, offset: 0, trashed: true });
    expect(calls.map((call) => call.url)).toEqual(["/api/documents?limit=25&offset=0&trashed=true"]);
  });
});
