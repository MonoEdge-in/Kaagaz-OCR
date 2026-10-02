// The shared poller (lib/poll.ts) under fake timers: no request while the tab
// is hidden, the 3 → 6 → 12 → 15 s steps, back to 3 s on a change, one
// request at a time, and retrying after a failure.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { POLL_STEPS_MS, createPoller, progressSignature } from "@/lib/poll";

interface Doc {
  id: string;
  status: "received" | "clean" | "ready";
  status_message: string;
  progress: { step: "reading"; done: number; total: number } | null;
}

function harness() {
  const calls: number[] = []; // when each request was sent, in ms since the start
  const seen: string[] = [];
  const start = Date.now();
  const world = {
    hidden: false,
    failing: false,
    doc: { id: "d1", status: "received", status_message: "Waiting to be read", progress: null } as Doc,
    // When set, each request waits for release() instead of answering at once.
    held: [] as (() => void)[],
    hold: false,
    out: 0,
    mostOut: 0,
  };
  const poller = createPoller<Doc>(
    () => ({
      load: () => {
        calls.push(Date.now() - start);
        world.out += 1;
        world.mostOut = Math.max(world.mostOut, world.out);
        const answer = world.failing
          ? Promise.reject(new Error("offline"))
          : Promise.resolve({ ...world.doc });
        const done = () => {
          world.out -= 1;
        };
        if (!world.hold) {
          void answer.then(done, done);
          return answer;
        }
        return new Promise<Doc>((resolve, reject) => {
          world.held.push(() => {
            done();
            answer.then(resolve, reject);
          });
        });
      },
      onResult: (doc) => seen.push(doc.status_message),
      onFailure: () => seen.push("failed"),
      again: (doc) => doc.status !== "ready",
      signature: (doc) => progressSignature([doc]),
    }),
    () => world.hidden,
  );
  const gaps = () => calls.slice(1).map((at, i) => at - (calls[i] ?? 0));
  return { poller, world, calls, seen, gaps };
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("createPoller", () => {
  it("waits 3, 6, 12, then 15 s while nothing changes", async () => {
    const { poller, gaps } = harness();
    poller.refresh();
    await vi.advanceTimersByTimeAsync(3000 + 6000 + 12000 + 15000 + 15000);
    expect(gaps()).toEqual([3000, 6000, 12000, 15000, 15000]);
    expect(POLL_STEPS_MS).toEqual([3000, 6000, 12000, 15000]);
    poller.stop();
  });

  it("goes back to 3 s whenever the status, status_message or progress changes", async () => {
    const { poller, world, gaps } = harness();
    poller.refresh();
    await vi.advanceTimersByTimeAsync(3000 + 6000); // 3 requests; the next waits 12 s
    world.doc = { ...world.doc, status_message: "Reading page 1 of 4" };
    await vi.advanceTimersByTimeAsync(12000); // this one sees the change
    await vi.advanceTimersByTimeAsync(3000);
    world.doc = { ...world.doc, progress: { step: "reading", done: 2, total: 4 } };
    await vi.advanceTimersByTimeAsync(6000);
    await vi.advanceTimersByTimeAsync(3000);
    world.doc = { ...world.doc, status: "clean" };
    await vi.advanceTimersByTimeAsync(6000);
    await vi.advanceTimersByTimeAsync(3000);
    expect(gaps()).toEqual([3000, 6000, 12000, 3000, 6000, 3000, 6000, 3000]);
    poller.stop();
  });

  it("stops once the answer says nothing is changing any more", async () => {
    const { poller, world, calls, seen } = harness();
    poller.refresh();
    await vi.advanceTimersByTimeAsync(3000);
    world.doc = { ...world.doc, status: "ready", status_message: "Ready" };
    await vi.advanceTimersByTimeAsync(6000);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(calls).toHaveLength(3);
    expect(seen.at(-1)).toBe("Ready");
  });

  it("sends nothing while the tab is hidden, and asks once when it is shown", async () => {
    const { poller, world, calls } = harness();
    world.hidden = true;
    poller.refresh();
    await vi.advanceTimersByTimeAsync(120_000);
    expect(calls).toEqual([]);

    world.hidden = false;
    poller.wake();
    await vi.advanceTimersByTimeAsync(0);
    expect(calls).toHaveLength(1);

    // Hidden again while it polls: the planned poll comes due, but waits.
    world.hidden = true;
    await vi.advanceTimersByTimeAsync(120_000);
    expect(calls).toHaveLength(1);
    world.hidden = false;
    poller.wake();
    poller.wake(); // visible and online together: still one request
    await vi.advanceTimersByTimeAsync(0);
    expect(calls).toHaveLength(2);
    poller.stop();
  });

  // #35 regression: the list must still be asked for after the last upload of
  // a batch settles, even when it settles while a request is already out.
  it("sends one request at a time, and a refresh asked for meanwhile runs once after it", async () => {
    const { poller, world, calls } = harness();
    world.hold = true;
    poller.refresh();
    poller.refresh();
    poller.refresh();
    await vi.advanceTimersByTimeAsync(0);
    expect(calls).toHaveLength(1);
    world.held.shift()?.();
    await vi.advanceTimersByTimeAsync(0);
    expect(calls).toHaveLength(2);
    world.held.shift()?.();
    await vi.advanceTimersByTimeAsync(0);
    expect(calls).toHaveLength(2); // the three refreshes became two requests, not three
    expect(world.mostOut).toBe(1);
    poller.stop();
  });

  // #35 regression: one failed refresh must never leave the list stale for good.
  it("keeps trying after a failure, backing off, and asks at once when back online", async () => {
    const { poller, world, calls, seen, gaps } = harness();
    world.failing = true;
    poller.refresh();
    await vi.advanceTimersByTimeAsync(6000 + 12000 + 15000);
    expect(gaps()).toEqual([6000, 12000, 15000]);
    world.failing = false;
    poller.wake(); // back online
    await vi.advanceTimersByTimeAsync(0);
    expect(calls).toHaveLength(5);
    expect(seen).toEqual(["failed", "failed", "failed", "failed", "Waiting to be read"]);
    poller.stop();
  });

  it("forgets its plan once stopped", async () => {
    const { poller, calls } = harness();
    poller.refresh();
    await vi.advanceTimersByTimeAsync(0);
    poller.stop();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(calls).toHaveLength(1);
  });

  it("asks after the first step when told to poll later", async () => {
    const { poller, calls } = harness();
    poller.later();
    await vi.advanceTimersByTimeAsync(2999);
    expect(calls).toEqual([]);
    await vi.advanceTimersByTimeAsync(1);
    expect(calls).toEqual([3000]);
    poller.stop();
  });
});

describe("progressSignature", () => {
  it("changes with the status, the status_message or the progress, and nothing else", () => {
    const doc = { id: "d1", status: "clean", status_message: "Reading", progress: null, display_name: "a.pdf" };
    const same = progressSignature([doc]);
    expect(progressSignature([{ ...doc, display_name: "b.pdf" }])).toBe(same);
    expect(progressSignature([{ ...doc, status: "ready" }])).not.toBe(same);
    expect(progressSignature([{ ...doc, status_message: "Reading page 2" }])).not.toBe(same);
    expect(progressSignature([{ ...doc, progress: { step: "reading", done: 1, total: 2 } }])).not.toBe(same);
    const newer = { id: "d2", version_no: 2, status: "clean", failure_code: null, status_message: "Reading" };
    expect(progressSignature([{ ...doc, newer_version: newer }])).not.toBe(same);
  });
});
