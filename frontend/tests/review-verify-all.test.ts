// Verify all on the review queue: who sees it, what it asks, the one call it makes and
// the line it leaves (components/review/review.ts, ReviewQueue.tsx).

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { canVerifyAll, verifyAllFilters, verifyAllQuestion, verifyAllText } from "@/components/review/review";
import { ApiError, verifyAllReview } from "@/lib/api";

const queue = readFileSync(join(__dirname, "..", "components/review/ReviewQueue.tsx"), "utf8");

afterEach(() => vi.unstubAllGlobals());

describe("Verify all on the review queue", () => {
  it("is offered to reviewers and admins only, and only while the queue has a value", () => {
    expect(canVerifyAll("reviewer", 856)).toBe(true);
    expect(canVerifyAll("admin", 1)).toBe(true);
    expect(canVerifyAll("member", 856)).toBe(false);
    expect(canVerifyAll("reviewer", 0)).toBe(false);
    expect(canVerifyAll("admin", 0)).toBe(false);
    expect(queue).toContain("canVerifyAll(me.user.role, loaded.total) && !confirmingAll");
    expect(queue).toContain("{`Verify all ${loaded.total}`}");
  });

  it("asks once, in plain words", () => {
    expect(verifyAllQuestion(856)).toBe(
      "Verify all 856 values as they were read? Each is recorded as verified by you.",
    );
    expect(verifyAllQuestion(1)).toBe("Verify this 1 value as it was read? It is recorded as verified by you.");
  });

  it("starts the question on Cancel, with Verify all before it, and locks the queue while it runs", () => {
    expect(queue).toMatch(/onClick=\{\(\) => void verifyAll\(\)\}[\s\S]*ref=\{focusOnMount\}[\s\S]*Cancel/);
    expect(queue).toContain('role="group"');
    expect(queue).toContain("inert={verifyingAll}");
    expect(queue).toContain("disabled={loadingMore || verifyingAll}");
  });

  it("says what it did in one line", () => {
    expect(verifyAllText({ verified: 840, skipped: [{ reason: "no_reading", count: 16 }], left: 16 })).toBe(
      "Verified 840 of 856. 16 have no reading to verify and are still in the queue.",
    );
    expect(verifyAllText({ verified: 3, skipped: [], left: 0 })).toBe("Verified 3 of 3.");
    expect(
      verifyAllText({
        verified: 5,
        skipped: [
          { reason: "no_reading", count: 1 },
          { reason: "too_long", count: 2 },
          { reason: "left_queue", count: 1 },
        ],
        left: 3,
      }),
    ).toBe(
      "Verified 5 of 9. 1 has no reading to verify and is still in the queue." +
        " 2 are too long to verify here and are still in the queue. 1 left the queue while this ran.",
    );
    expect(verifyAllText({ verified: 0, skipped: [], left: 0 })).toBe("Nothing was left to verify.");
  });

  it("sends one POST with the queue's filters, never one per value", async () => {
    const sent: { url: string; method: string; body: unknown }[] = [];
    vi.stubGlobal("fetch", async (url: string, init: RequestInit = {}) => {
      sent.push({ url, method: init.method ?? "GET", body: init.body ? JSON.parse(String(init.body)) : undefined });
      return new Response(JSON.stringify({ verified: 2, skipped: [], left: 0 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });

    expect(await verifyAllReview(verifyAllFilters("d1", "single_reader"))).toEqual({
      verified: 2,
      skipped: [],
      left: 0,
    });
    await verifyAllReview(verifyAllFilters(undefined, null));

    expect(sent).toEqual([
      { url: "/api/review/verify-all", method: "POST", body: { document_id: "d1", reason: ["single_reader"] } },
      { url: "/api/review/verify-all", method: "POST", body: {} },
    ]);
    expect(queue).toContain("verifyAllReview(verifyAllFilters(documentId, reason))");
    expect(queue).not.toContain("verifyEach");
    expect(queue).not.toContain("resolveReview");
  });

  it("waits out a 429's Retry-After and asks again, then gives the 429 up after a few tries", async () => {
    vi.useFakeTimers();
    try {
      const replies = [429, 200];
      const sent: number[] = [];
      vi.stubGlobal("fetch", async () => {
        sent.push(Date.now());
        const status = replies.shift() ?? 429;
        const body = status === 200 ? { verified: 1, skipped: [], left: 0 } : { error: { code: "rate_limited" } };
        return new Response(JSON.stringify(body), {
          status,
          headers: { "Content-Type": "application/json", "Retry-After": "7" },
        });
      });

      const paced = verifyAllReview({});
      await vi.advanceTimersByTimeAsync(6_999);
      expect(sent).toHaveLength(1); // not before the Retry-After
      await vi.advanceTimersByTimeAsync(1);
      expect(await paced).toEqual({ verified: 1, skipped: [], left: 0 });
      expect(sent).toEqual([sent[0], (sent[0] ?? 0) + 7_000]);

      sent.length = 0;
      const refused = verifyAllReview({}).catch((error: unknown) => error);
      await vi.advanceTimersByTimeAsync(60_000);
      const caught = await refused;
      expect(caught).toBeInstanceOf(ApiError);
      expect((caught as ApiError).status).toBe(429);
      expect(sent).toHaveLength(4); // the first try and three paced ones
    } finally {
      vi.useRealTimers();
    }
  });
});
