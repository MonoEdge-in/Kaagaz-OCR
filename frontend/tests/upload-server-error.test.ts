// Issue 357 (27 Oct 2026): a server error (5xx) during an upload is tried again, and the row
// says the server had a problem, never "Waiting for connection"; a lost connection still does.
// The pace and the 429 handling are tests/request-budget.test.ts's.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { uploadStateText } from "@/components/documents/logic";
import { uploadDocument } from "@/lib/api";

const SESSION = { id: "s1", part_size: 8 * 1024 * 1024, part_count: 1 };
const DOCUMENT = { id: "d1", display_name: "deed.pdf", status: "received" };

/** A stand-in API whose first call fails as `first` says; every later call works. */
function stubApi(first: "server" | "connection") {
  let calls = 0;
  vi.stubGlobal("fetch", async (url: string) => {
    calls += 1;
    if (calls === 1 && first === "connection") throw new TypeError("Failed to fetch");
    if (calls === 1) {
      const body = { error: { code: "internal_error", message: "Something went wrong on our side." } };
      return new Response(JSON.stringify(body), { status: 503, headers: { "Content-Type": "application/json" } });
    }
    const path = url.split("?")[0] ?? url;
    const body = path.endsWith("/candidates")
      ? { candidates: [] }
      : path.endsWith("/complete")
        ? { document: DOCUMENT, duplicate: false, in_trash: false }
        : SESSION;
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  class FakeXhr {
    status = 204;
    responseText = "";
    upload = { onprogress: null };
    onload: (() => void) | null = null;
    onerror = null;
    onabort = null;
    withCredentials = false;
    open() {}
    setRequestHeader() {}
    getResponseHeader() {
      return null;
    }
    send() {
      queueMicrotask(() => this.onload?.());
    }
  }
  vi.stubGlobal("XMLHttpRequest", FakeXhr);
}

// Fixed (#239, 27 Oct 2026): the clock moves only while the upload waits on a timer. The file's
// hash (crypto.subtle) takes real time, and a slow machine used up the 10 s before the first call.
// Fixed (#447): bounded by 4 s of real time, not 100,000 turns, which pass in about 100 ms idle.
async function pass(ms: number, isDone: () => boolean) {
  for (let left = ms, until = performance.now() + 4_000; left > 0 && !isDone() && performance.now() < until; ) {
    await new Promise((resolve) => setImmediate(resolve));
    if (vi.getTimerCount() > 0) {
      vi.advanceTimersByTime(100);
      left -= 100;
    }
  }
}

async function waitsSaid(first: "server" | "connection"): Promise<[boolean, string | undefined][]> {
  stubApi(first);
  const said: [boolean, string | undefined][] = [];
  let done = false;
  void uploadDocument(new File(["%PDF-1.7"], "deed.pdf"), "u1", () => {}, undefined, undefined, undefined, undefined, undefined, (waiting, why) =>
    said.push([waiting, why]),
  ).then(() => (done = true));
  await pass(10_000, () => done);
  expect(done).toBe(true); // tried again, and stored
  return said;
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("an upload that has to wait says why", () => {
  it("says the server had a problem after a 5xx", async () => {
    expect(await waitsSaid("server")).toEqual([
      [true, "server"],
      [false, undefined],
    ]);
    expect(uploadStateText({ kind: "offline", fraction: 0, server: true })).toBe("The server had a problem. Trying again…");
  });

  it("still says Waiting for connection when the connection is lost", async () => {
    expect(await waitsSaid("connection")).toEqual([
      [true, "connection"],
      [false, undefined],
    ]);
    expect(uploadStateText({ kind: "offline", fraction: 0 })).toBe("Waiting for connection");
    expect(uploadStateText({ kind: "offline", fraction: 0, server: false })).toBe("Waiting for connection");
  });
});
