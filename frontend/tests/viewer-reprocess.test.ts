import { afterEach, describe, expect, it, vi } from "vitest";
import {
  beingRead,
  canReprocess,
  canSend,
  loadFailure,
  reprocessConfirmText,
  reprocessErrorText,
  reprocessReducer,
  settledText,
  valuesNote,
} from "@/components/viewer/reprocess";
import type { ReprocessState } from "@/components/viewer/reprocess";
import { ApiError, reprocessDocument } from "@/lib/api";
import type { DocumentStatus, Label, Role } from "@/lib/types";

const TRASHED = "2026-09-20T10:00:00Z";
const doc = (status: DocumentStatus, deleted_at: string | null = null) => ({ status, deleted_at });

describe("canReprocess (PRD 2, X1)", () => {
  it("lets a reviewer or an admin read a ready, failed or unreadable document again", () => {
    for (const role of ["reviewer", "admin"] as Role[]) {
      for (const status of ["ready", "failed", "needs_reader"] as DocumentStatus[]) {
        expect(canReprocess(role, doc(status)), `${role} ${status}`).toBe(true);
      }
    }
  });

  it("never offers it to a member", () => {
    for (const status of ["ready", "failed", "needs_reader"] as DocumentStatus[]) {
      expect(canReprocess("member", doc(status)), status).toBe(false);
    }
  });

  it("never offers it while the document is still checked or read, or was rejected", () => {
    for (const status of ["received", "clean", "rejected"] as DocumentStatus[]) {
      expect(canReprocess("admin", doc(status)), status).toBe(false);
    }
  });

  it("never offers it in Trash", () => {
    expect(canReprocess("admin", doc("ready", TRASHED))).toBe(false);
    expect(canReprocess("reviewer", doc("failed", TRASHED))).toBe(false);
  });
});

describe("reprocessConfirmText", () => {
  const OUT_OF_SEARCH =
    "While it is read again, and if reading fails, this document is out of search until it is read successfully.";

  it("names the reviewer fixes in the PRD's words, then says it leaves search", () => {
    expect(reprocessConfirmText(3)).toBe(
      "Reviewer fixes on this document (3) come back only if they were saved as remembered fixes. " +
        OUT_OF_SEARCH,
    );
    expect(reprocessConfirmText(1)).toContain("Reviewer fixes on this document (1) come back");
  });

  it("says plainly when there are no reviewer fixes to lose", () => {
    expect(reprocessConfirmText(0)).toBe(
      `This document has no reviewer fixes, so none are lost. ${OUT_OF_SEARCH}`,
    );
    expect(reprocessConfirmText(0)).not.toContain("(0)");
  });
});

describe("reprocessReducer", () => {
  const idle: ReprocessState = { step: "idle" };
  const confirming: ReprocessState = { step: "confirming" };
  const sending: ReprocessState = { step: "sending" };

  it("asks first, inside the page, and nothing is sent until confirmed", () => {
    expect(reprocessReducer(idle, { type: "open" })).toEqual(confirming);
    expect(canSend(idle)).toBe(false);
    expect(reprocessReducer(idle, { type: "send" })).toBe(idle);
  });

  it("goes back to the button on Cancel, and sends nothing", () => {
    expect(reprocessReducer(confirming, { type: "cancel" })).toEqual(idle);
  });

  it("sends once: a second confirm while sending changes nothing", () => {
    expect(canSend(confirming)).toBe(true);
    expect(reprocessReducer(confirming, { type: "send" })).toEqual(sending);
    expect(canSend(sending)).toBe(false);
    expect(reprocessReducer(sending, { type: "send" })).toBe(sending);
    expect(reprocessReducer(sending, { type: "open" })).toBe(sending);
    expect(reprocessReducer(sending, { type: "cancel" })).toBe(sending);
  });

  it("ends as done, or as an error that can be tried again", () => {
    expect(reprocessReducer(sending, { type: "sent" })).toEqual({ step: "done" });
    const failed = reprocessReducer(sending, { type: "failed", message: "No." });
    expect(failed).toEqual({ step: "error", message: "No." });
    expect(reprocessReducer(failed, { type: "open" })).toEqual(confirming);
  });

  it("ignores a reply that comes when nothing was sent", () => {
    expect(reprocessReducer(idle, { type: "sent" })).toBe(idle);
    expect(reprocessReducer(confirming, { type: "failed", message: "No." })).toBe(confirming);
    const done: ReprocessState = { step: "done" };
    expect(reprocessReducer(done, { type: "open" })).toBe(done);
  });
});

describe("loadFailure (QA, 15 Oct 2026)", () => {
  it("offers Try again for a server or network failure, never for a document that is not there", () => {
    expect(loadFailure(new ApiError(404, "not_found", "We couldn't find that."))).toEqual({
      message: "We couldn't find that.",
      retry: false,
    });
    expect(loadFailure(new ApiError(503, "backend_unavailable", "Busy.")).retry).toBe(true);
    expect(loadFailure(new TypeError("Failed to fetch"))).toEqual({
      message: "Check your internet connection, then try again.",
      retry: true,
    });
  });
});

describe("reprocessErrorText", () => {
  it("explains a 409: only a ready, failed or unreadable document out of Trash", () => {
    const error = new ApiError(409, "not_reprocessable", "server words");
    expect(reprocessErrorText(error)).toBe(
      "This document can't be read again right now. Only a ready, failed or unreadable document that is not in Trash can be.",
    );
  });

  it("explains a 403: only reviewers and admins", () => {
    const error = new ApiError(403, "forbidden", "server words");
    expect(reprocessErrorText(error)).toBe(
      "Only a reviewer or an admin can read a document again.",
    );
  });

  it("passes other server replies through, and asks to check the connection otherwise", () => {
    expect(reprocessErrorText(new ApiError(429, "rate_limited", "Try again in a few minutes."))).toBe(
      "Try again in a few minutes.",
    );
    expect(reprocessErrorText(new TypeError("Failed to fetch"))).toBe(
      "Check your internet connection, then try again.",
    );
  });
});

describe("reprocessDocument", () => {
  const calls: { url: string; method: string; csrf: string | null }[] = [];

  afterEach(() => {
    vi.unstubAllGlobals();
    calls.length = 0;
  });

  it("posts to /documents/{id}/reprocess with the CSRF header", async () => {
    vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
      calls.push({
        url,
        method: init.method ?? "GET",
        csrf: new Headers(init.headers).get("X-Requested-With"),
      });
      return new Response(JSON.stringify({ id: "d1", status: "received" }), { status: 200 });
    });
    await expect(reprocessDocument("d1")).resolves.toMatchObject({ id: "d1", status: "received" });
    expect(calls).toEqual([{ url: "/api/documents/d1/reprocess", method: "POST", csrf: "kaagaz" }]);
  });

  it("sends no body without a model, and the chosen model as read_model (MS4)", async () => {
    const bodies: unknown[] = [];
    vi.stubGlobal("fetch", async (_url: string, init: RequestInit) => {
      bodies.push(init.body === undefined ? undefined : JSON.parse(String(init.body)));
      return new Response(JSON.stringify({ id: "d1", status: "received" }), { status: 200 });
    });
    await reprocessDocument("d1");
    await reprocessDocument("d1", "anthropic/claude-sonnet-5.5");
    expect(bodies).toEqual([undefined, { read_model: "anthropic/claude-sonnet-5.5" }]);
  });
});

// A8a: the viewer follows a document while it is read, on load too.
describe("Read again settles", () => {
  it("goes back to the button once the document is no longer being read", () => {
    expect(reprocessReducer({ step: "done" }, { type: "settled" })).toEqual({ step: "idle" });
    const sending: ReprocessState = { step: "sending" };
    expect(reprocessReducer(sending, { type: "settled" })).toBe(sending);
  });

  it("says what was found, or why reading failed", () => {
    const values = (labels: Label[]) => labels.map((label) => ({ label }));
    const ready = { status: "ready" as const, status_message: "Ready" };
    const many = values([...Array<Label>(36).fill("verified"), ...Array<Label>(6).fill("needs_review")]);
    expect(settledText({ ...ready, values: many }, true)).toBe("Read again. 42 values found, 6 need review.");
    expect(settledText({ ...ready, values: values(["needs_review"]) }, false)).toBe(
      "Reading finished. 1 value found, 1 needs review.",
    );
    const failed = { status: "failed" as const, status_message: "This file could not be read.", values: [] };
    expect(settledText(failed, true)).toBe("Reading failed. This file could not be read.");
  });
});

describe("valuesNote", () => {
  it("says the values are not final while the document is read", () => {
    expect(valuesNote("received", 0)).toBe("Values show here once the document has been read.");
    expect(valuesNote("clean", 0)).toBe("Values show here once the document has been read.");
    expect(valuesNote("clean", 12)).toBe(
      "Being read again. These values are from the last reading and may change.",
    );
    for (const status of ["ready", "failed", "needs_reader", "rejected"] as DocumentStatus[]) {
      expect(valuesNote(status, 3)).toBeNull();
    }
    expect([beingRead("received"), beingRead("clean"), beingRead("ready")]).toEqual([true, true, false]);
  });
});
